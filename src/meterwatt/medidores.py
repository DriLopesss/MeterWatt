"""Medidores: pareamento, leituras recebidas por MQTT, painel, histórico,
consumo fantasma e meta do mês.

Minimização (LGPD): só medidores pareados a uma conta que autorizou o tratamento do
consumo têm leituras guardadas. Mensagem de aparelho desconhecido é descartada.
"""
import json
import math
import re
import statistics
import unicodedata

from .erros import ErroDeNegocio, NaoEncontrado
from .seguranca import Limitador, normalizar_codigo_pareamento, novo_codigo_pareamento

ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TEXTO_SIMPLES = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
TAMANHO_MAXIMO_MENSAGEM = 2048
INTERVALO_MINIMO_S = 0.8          # o firmware manda a cada 1 s; mais rápido que isso é descartado
INTERVALO_MAXIMO_S = 60           # buraco maior não entra na estimativa por integração
LIMITE_OFFLINE_S = 20
VALIDADE_CODIGO_S = 15 * 60
CODIGOS_ATIVOS_MAXIMO = 3
BALDE_S = 300                     # consumo agregado de 5 em 5 minutos
PONTOS_NO_GRAFICO = 720
PERIODOS = {"2h": 2 * 3600, "24h": 86400, "7d": 7 * 86400}
NOITE_INICIO_H, NOITE_FIM_H = 0, 5
COBERTURA_MINIMA_NOITE = 0.8


def numero(dados, chave, minimo, maximo):
    valor = dados.get(chave)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    valor = float(valor)
    if math.isnan(valor) or math.isinf(valor) or not minimo <= valor <= maximo:
        return None
    return valor


def texto_simples(valor):
    return valor if isinstance(valor, str) and TEXTO_SIMPLES.match(valor) else None


def limpar_apelido(apelido):
    if not isinstance(apelido, str):
        return None
    apelido = " ".join(unicodedata.normalize("NFC", apelido).split())
    if not 1 <= len(apelido) <= 40 or any(unicodedata.category(c).startswith("C") for c in apelido):
        return None
    return apelido


def resumir_pontos(pontos, maximo, buraco_s=30):
    """Reduz a lista para no máximo `maximo` pontos (média de vizinhos), sem emendar buracos."""
    if len(pontos) <= maximo:
        return pontos
    tamanho = math.ceil(len(pontos) / maximo)
    saida, grupo = [], []
    for ponto in pontos:
        if grupo and (len(grupo) >= tamanho or ponto[0] - grupo[-1][0] > buraco_s):
            saida.append([grupo[-1][0], round(sum(v for _, v in grupo) / len(grupo), 1)])
            grupo = []
        grupo.append(ponto)
    if grupo:
        saida.append([grupo[-1][0], round(sum(v for _, v in grupo) / len(grupo), 1)])
    return saida


def _r(valor, casas):
    return None if valor is None else round(valor, casas)


def _reais(kwh, tarifa, casas=2):
    """Custo em reais; None quando a pessoa ainda não informou a tarifa."""
    return None if tarifa is None or kwh is None else round(kwh * tarifa, casas)


class Medidores:
    def __init__(self, plataforma):
        self.p = plataforma
        # Tentativas de pareamento por aparelho: dificulta adivinhar códigos.
        self.limite_pareamento = Limitador(10, 15 * 60)

    # ------------------------------------------------------------ apoio

    def _do_usuario(self, conn, uid, mid):
        if not isinstance(mid, str) or not ID_VALIDO.match(mid):
            raise NaoEncontrado("Medidor não encontrado.")
        m = conn.execute("SELECT * FROM medidores WHERE id = ? AND usuario_id = ?", (mid, uid)).fetchone()
        if not m:
            raise NaoEncontrado("Medidor não encontrado.")   # de outra conta também: 404, sem confirmar que existe
        return m

    def _ids(self, conn, uid):
        return [r["id"] for r in conn.execute("SELECT id FROM medidores WHERE usuario_id = ? ORDER BY pareado_em", (uid,))]

    def _wh_entre(self, conn, ids, inicio, fim):
        if not ids:
            return 0.0
        marcadores = ",".join("?" * len(ids))
        v = conn.execute(f"SELECT SUM(wh) FROM consumo_5min WHERE medidor_id IN ({marcadores}) AND inicio >= ? AND inicio < ?",
                         (*ids, inicio, fim)).fetchone()[0]
        return v or 0.0

    def _usuario(self, conn, uid):
        return conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()

    # ------------------------------------------------------------ MQTT

    def processar_mqtt(self, topico, carga):
        """Ponto de entrada de toda mensagem MQTT. Devolve o que fez (útil nos testes)."""
        partes = topico.split("/") if isinstance(topico, str) else []
        if len(partes) != 3 or partes[0] != "meterwatt" or not ID_VALIDO.match(partes[1]):
            return "ignorada"
        if not isinstance(carga, (bytes, bytearray)) or len(carga) > TAMANHO_MAXIMO_MENSAGEM:
            return "ignorada"
        try:
            texto = carga.decode("utf-8")
        except UnicodeDecodeError:
            return "ignorada"
        mid, tipo = partes[1], partes[2]
        if tipo == "status":
            return self._status(mid, texto)
        try:
            dados = json.loads(texto)
        except ValueError:
            return "ignorada"
        if not isinstance(dados, dict):
            return "ignorada"
        if tipo == "telemetria":
            return self.registrar_leitura(mid, dados)
        if tipo == "pareamento":
            return self.parear(mid, dados)
        return "ignorada"

    def _status(self, mid, texto):
        if texto not in ("online", "offline"):
            return "ignorada"
        conn = self.p.db()
        with conn:
            n = conn.execute("UPDATE medidores SET status_mqtt = ?, ultima_mensagem = ? WHERE id = ?",
                             (texto, self.p.agora(), mid)).rowcount
        return "status" if n else "desconhecido"

    def parear(self, mid, dados):
        codigo = normalizar_codigo_pareamento(dados.get("codigo"))
        agora = self.p.agora()
        if not codigo or self.limite_pareamento.esgotado(mid, agora):
            return "recusado"
        conn = self.p.db()
        linha = conn.execute("SELECT * FROM codigos_pareamento WHERE codigo_hash = ?", (self.p.cofre.hmac(codigo),)).fetchone()
        if not linha or linha["usado_em"] is not None or linha["expira_em"] < agora:
            self.limite_pareamento.registrar(mid, agora)
            return "recusado"
        uid = linha["usuario_id"]
        if not self.p.consentimento(conn, uid, "consumo"):
            return "recusado"
        existente = conn.execute("SELECT usuario_id FROM medidores WHERE id = ?", (mid,)).fetchone()
        with conn:
            if existente and existente["usuario_id"] != uid:
                conn.execute("UPDATE codigos_pareamento SET usado_em = ? WHERE id = ?", (agora, linha["id"]))
                self.p.auditar(conn, uid, "pareamento_recusado", mid)
                return "recusado"
            conn.execute("UPDATE codigos_pareamento SET usado_em = ?, medidor_id = ? WHERE id = ?", (agora, mid, linha["id"]))
            if not existente:
                conn.execute("INSERT INTO medidores (id, usuario_id, apelido, pareado_em, ultima_mensagem) VALUES (?, ?, ?, ?, ?)",
                             (mid, uid, f"Medidor {mid[-4:].upper()}", agora, agora))
                self.p.auditar(conn, uid, "medidor_pareado", mid)
        self.limite_pareamento.zerar(mid)
        self.p.publicar(f"meterwatt/{mid}/comando", "pareado")
        return "pareado"

    def registrar_leitura(self, mid, dados):
        conn = self.p.db()
        m = conn.execute("SELECT * FROM medidores WHERE id = ?", (mid,)).fetchone()
        if not m:
            return "desconhecido"                 # não pareado: descartado, nada é guardado
        if not self.p.consentimento(conn, m["usuario_id"], "consumo"):
            return "sem_autorizacao"
        agora = self.p.agora()
        if m["ultima_gravacao"] is not None and 0 <= agora - m["ultima_gravacao"] < INTERVALO_MINIMO_S:
            return "rapida_demais"
        simulado = 1 if dados.get("simulado") is True else 0
        rssi = numero(dados, "rssi", -130, 0)
        fw = texto_simples(dados.get("fw")) or m["fw"]

        if dados.get("sensor_ok") is False:
            with conn:
                conn.execute("INSERT INTO leituras (medidor_id, instante, sensor_ok, erro_sensor, rssi, simulado) "
                             "VALUES (?, ?, 0, ?, ?, ?)", (mid, agora, texto_simples(dados.get("erro_sensor")) or "desconhecido",
                                                          rssi, simulado))
                conn.execute("UPDATE medidores SET ultima_mensagem = ?, ultima_gravacao = ?, status_mqtt = 'online', fw = ?, "
                             "potencia_anterior = NULL WHERE id = ?", (agora, agora, fw, mid))
            return "sensor_sem_leitura"

        tensao = numero(dados, "tensao_v", 0, 300)
        potencia = numero(dados, "potencia_w", 0, 30000)
        corrente = numero(dados, "corrente_a", 0, 120)
        if corrente is None:
            corrente = numero(dados, "irms_a", 0, 120)
        wh = 0.0
        contador = m["contador_wh"]
        if tensao is not None and potencia is not None and corrente is not None:
            tipo, valor = "pzem", potencia
            va = round(tensao * corrente, 1)
            fp, freq = numero(dados, "fp", 0, 1), numero(dados, "frequencia_hz", 40, 70)
            novo = numero(dados, "energia_wh", 0, 1e10)
            if novo is not None:
                # Contador menor = zerado no módulo; salto absurdo = leitura estranha. Nos dois casos, recomeça dali.
                if contador is not None and contador <= novo <= contador + 30000:
                    wh = novo - contador
                contador = novo
        elif corrente is not None:
            va = numero(dados, "va", 0, 30000)
            if va is None:
                return "invalida"
            tipo, valor, tensao, potencia, fp, freq = "corrente", va, None, None, None, None
            ant, t_ant = m["potencia_anterior"], m["ultima_gravacao"]
            if ant is not None and t_ant is not None and 0 < agora - t_ant <= INTERVALO_MAXIMO_S:
                wh = (ant + va) / 2 * (agora - t_ant) / 3600   # trapézio
        else:
            return "invalida"

        balde = int(agora // BALDE_S * BALDE_S)
        with conn:
            conn.execute("INSERT INTO leituras (medidor_id, instante, sensor_ok, tensao_v, corrente_a, potencia_w, potencia_va, "
                         "fp, frequencia_hz, rssi, simulado) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (mid, agora, tensao, corrente, potencia, va, fp, freq, rssi, simulado))
            conn.execute(
                "INSERT INTO consumo_5min (medidor_id, inicio, n, soma_w, min_w, max_w, wh, simulado) VALUES (?, ?, 1, ?, ?, ?, ?, ?) "
                "ON CONFLICT (medidor_id, inicio) DO UPDATE SET n = n + 1, soma_w = soma_w + excluded.soma_w, "
                "min_w = MIN(min_w, excluded.min_w), max_w = MAX(max_w, excluded.max_w), wh = wh + excluded.wh, "
                "simulado = MAX(simulado, excluded.simulado)",
                (mid, balde, valor, valor, valor, wh, simulado))
            conn.execute("UPDATE medidores SET ultima_mensagem = ?, ultima_gravacao = ?, status_mqtt = 'online', tipo = ?, fw = ?, "
                         "contador_wh = ?, potencia_anterior = ? WHERE id = ?",
                         (agora, agora, tipo, fw, contador, valor if tipo == "corrente" else None, mid))
        return "gravada"

    # ------------------------------------------------------------ pareamento pelo painel

    def gerar_codigo(self, sessao, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        if not self.p.consentimento(conn, uid, "consumo"):
            raise ErroDeNegocio("Para parear um medidor, autorize o tratamento dos dados de consumo em Privacidade.",
                                403, "sem_autorizacao")
        agora = self.p.agora()
        ativos = conn.execute("SELECT COUNT(*) FROM codigos_pareamento WHERE usuario_id = ? AND usado_em IS NULL AND expira_em > ?",
                              (uid, agora)).fetchone()[0]
        if ativos >= CODIGOS_ATIVOS_MAXIMO:
            raise ErroDeNegocio("Você já tem códigos válidos. Use um deles ou espere 15 minutos.", 429)
        codigo = novo_codigo_pareamento()
        with conn:
            conn.execute("INSERT INTO codigos_pareamento (usuario_id, codigo_hash, criado_em, expira_em) VALUES (?, ?, ?, ?)",
                         (uid, self.p.cofre.hmac(normalizar_codigo_pareamento(codigo)), agora, agora + VALIDADE_CODIGO_S))
            self.p.auditar(conn, uid, "codigo_pareamento", None, ip)
        return {"codigo": codigo, "expira_em": self.p.iso(agora + VALIDADE_CODIGO_S), "validade_s": VALIDADE_CODIGO_S}

    def renomear(self, sessao, mid, apelido, ip=None):
        conn = self.p.db()
        self._do_usuario(conn, sessao["usuario_id"], mid)
        apelido = limpar_apelido(apelido)
        if not apelido:
            raise ErroDeNegocio("O nome do medidor precisa ter de 1 a 40 caracteres.", 400)
        with conn:
            conn.execute("UPDATE medidores SET apelido = ? WHERE id = ?", (apelido, mid))
            self.p.auditar(conn, sessao["usuario_id"], "medidor_renomeado", mid, ip)

    def remover(self, sessao, mid, ip=None):
        """Desfaz o pareamento e apaga as leituras desse medidor."""
        conn = self.p.db()
        self._do_usuario(conn, sessao["usuario_id"], mid)
        with conn:
            conn.execute("DELETE FROM medidores WHERE id = ?", (mid,))
            self.p.auditar(conn, sessao["usuario_id"], "medidor_removido", mid, ip)
        self.p.publicar(f"meterwatt/{mid}/comando", "despareado")

    # ------------------------------------------------------------ painel

    def listar(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        tarifa = self.p.tarifa_de(sessao)
        hoje = self.p.inicio_do_dia(agora)
        saida = []
        for m in conn.execute("SELECT * FROM medidores WHERE usuario_id = ? ORDER BY pareado_em", (uid,)).fetchall():
            l = conn.execute("SELECT * FROM leituras WHERE medidor_id = ? ORDER BY instante DESC LIMIT 1", (m["id"],)).fetchone()
            idade = None if m["ultima_mensagem"] is None else max(0.0, agora - m["ultima_mensagem"])
            online = idade is not None and idade <= LIMITE_OFFLINE_S and m["status_mqtt"] != "offline"
            atual = l if (l is not None and agora - l["instante"] <= LIMITE_OFFLINE_S) else None
            sensor_ok = bool(atual["sensor_ok"]) if atual else None
            kwh_hoje = self._wh_entre(conn, [m["id"]], hoje, agora + 1) / 1000
            potencia = None
            if atual and atual["sensor_ok"]:
                potencia = atual["potencia_w"] if m["tipo"] == "pzem" else atual["potencia_va"]
            saida.append({
                "id": m["id"], "apelido": m["apelido"], "tipo": m["tipo"], "fw": m["fw"],
                "pareado_em": self.p.iso(m["pareado_em"]), "online": online,
                "segundos_desde_mensagem": _r(idade, 1), "sensor_ok": sensor_ok,
                "erro_sensor": atual["erro_sensor"] if atual else None,
                "simulado": bool(l["simulado"]) if l else False,
                "unidade": "W" if m["tipo"] == "pzem" else "VA",
                "energia_estimada": m["tipo"] == "corrente",
                "tensao_v": atual["tensao_v"] if atual else None,
                "corrente_a": atual["corrente_a"] if atual else None,
                "potencia": potencia,
                "fp": atual["fp"] if atual else None,
                "frequencia_hz": atual["frequencia_hz"] if atual else None,
                "rssi": l["rssi"] if l else None,
                "kwh_hoje": round(kwh_hoje, 4),
                "custo_hoje": _reais(kwh_hoje, tarifa),
                "custo_por_hora": _reais(potencia / 1000, tarifa) if potencia is not None and online else None,
            })
        return {"medidores": saida, "tarifa_kwh": tarifa, "limite_offline_s": LIMITE_OFFLINE_S,
                "servidor_em": self.p.iso(agora)}

    def historico(self, sessao, mid, periodo="2h"):
        conn = self.p.db()
        m = self._do_usuario(conn, sessao["usuario_id"], mid)
        if periodo not in PERIODOS:
            periodo = "2h"
        agora = self.p.agora()
        desde = agora - PERIODOS[periodo]
        coluna = "potencia_w" if m["tipo"] == "pzem" else "potencia_va"
        if periodo == "2h":
            pontos = [[round(r[0], 1), r[1]] for r in conn.execute(
                f"SELECT instante, {coluna} FROM leituras WHERE medidor_id = ? AND instante >= ? AND sensor_ok = 1 "
                "ORDER BY instante", (mid, desde))]
            pontos = resumir_pontos(pontos, PONTOS_NO_GRAFICO)
        else:
            baldes = [(r["inicio"], r["soma_w"] / r["n"]) for r in conn.execute(
                "SELECT inicio, n, soma_w FROM consumo_5min WHERE medidor_id = ? AND inicio >= ? ORDER BY inicio",
                (mid, int(desde)))]
            pontos = [[t + BALDE_S, round(v, 1)] for t, v in baldes]
            if periodo == "7d":
                pontos = resumir_pontos(pontos, 7 * 48, buraco_s=BALDE_S * 2)
        return {"id": mid, "periodo": periodo, "unidade": "W" if m["tipo"] == "pzem" else "VA",
                "agora": round(agora, 1), "pontos": pontos}

    def consumo_por_dia(self, sessao, dias=30):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        dias = max(1, min(int(dias), 366))
        agora = self.p.agora()
        ids = self._ids(conn, uid)
        tarifa = self.p.tarifa_de(sessao)
        totais = {}
        if ids:
            marcadores = ",".join("?" * len(ids))
            for r in conn.execute(f"SELECT inicio, wh FROM consumo_5min WHERE medidor_id IN ({marcadores}) AND inicio >= ?",
                                  (*ids, int(self.p.inicio_do_dia(agora, dias - 1)))):
                dia = self.p.dia_local(r["inicio"])
                totais[dia] = totais.get(dia, 0.0) + r["wh"]
        saida = []
        for i in range(dias - 1, -1, -1):
            dia = self.p.dia_local(self.p.inicio_do_dia(agora, i) + 3600)
            kwh = totais.get(dia, 0.0) / 1000
            saida.append({"dia": dia, "kwh": round(kwh, 3), "custo": _reais(kwh, tarifa)})
        return {"dias": saida, "tarifa_kwh": tarifa}

    # ------------------------------------------------------------ recursos novos

    def fantasma(self, sessao):
        """Consumo fantasma: o que a casa gasta parada (aparelhos em espera, roteador, geladeira...).

        Para cada noite (0h às 5h) dos últimos 7 dias com pelo menos 80% dos intervalos de
        5 minutos medidos, pega o menor intervalo. A mediana dessas noites é a "base" do medidor.
        Usar a mediana evita que uma noite atípica (luz cortada, viagem) distorça o número.
        """
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        tarifa = self.p.tarifa_de(sessao)
        baldes_por_noite = (NOITE_FIM_H - NOITE_INICIO_H) * 3600 // BALDE_S
        por_medidor, watts_total, noites_usadas, estimado = [], 0.0, 0, False
        for m in conn.execute("SELECT id, apelido, tipo FROM medidores WHERE usuario_id = ? ORDER BY pareado_em", (uid,)).fetchall():
            minimos = []
            for d in range(0, 8):
                inicio = self.p.inicio_do_dia(agora, d) + NOITE_INICIO_H * 3600
                fim = self.p.inicio_do_dia(agora, d) + NOITE_FIM_H * 3600
                if fim > agora or agora - inicio > 7 * 86400 + NOITE_FIM_H * 3600:
                    continue
                medias = [r["soma_w"] / r["n"] for r in conn.execute(
                    "SELECT n, soma_w FROM consumo_5min WHERE medidor_id = ? AND inicio >= ? AND inicio < ? AND n > 0",
                    (m["id"], int(inicio), int(fim)))]
                if len(medias) >= COBERTURA_MINIMA_NOITE * baldes_por_noite:
                    minimos.append(min(medias))
            if len(minimos) >= 2:
                base = statistics.median(minimos)
                watts_total += base
                noites_usadas = max(noites_usadas, len(minimos))
                estimado = estimado or m["tipo"] == "corrente"
                por_medidor.append({"id": m["id"], "apelido": m["apelido"], "watts": round(base, 1), "noites": len(minimos)})
            else:
                por_medidor.append({"id": m["id"], "apelido": m["apelido"], "watts": None, "noites": len(minimos)})
        if not any(x["watts"] is not None for x in por_medidor):
            return {"disponivel": False, "medidores": por_medidor,
                    "motivo": "Precisamos de pelo menos 2 noites medidas (entre 0h e 5h) para calcular."}
        kwh_mes = watts_total * 24 * 30 / 1000
        ids = [x["id"] for x in por_medidor if x["watts"] is not None]
        kwh_7d = self._wh_entre(conn, ids, agora - 7 * 86400, agora + 1) / 1000
        dias_medidos = self._dias_com_dados(conn, ids, agora)
        fatia = None
        if kwh_7d > 0 and dias_medidos > 0:
            fatia = min(1.0, watts_total * 24 * dias_medidos / 1000 / kwh_7d)
        return {
            "disponivel": True, "watts": round(watts_total, 1), "kwh_mes": round(kwh_mes, 1),
            "custo_mes": _reais(kwh_mes, tarifa), "custo_ano": _reais(kwh_mes * 12, tarifa),
            "fatia_do_consumo": _r(fatia, 3), "noites": noites_usadas, "estimado": estimado, "medidores": por_medidor,
        }

    def _dias_com_dados(self, conn, ids, agora, janela_dias=7):
        if not ids:
            return 0.0
        marcadores = ",".join("?" * len(ids))
        primeiro = conn.execute(f"SELECT MIN(inicio) FROM consumo_5min WHERE medidor_id IN ({marcadores}) AND inicio >= ?",
                                (*ids, agora - janela_dias * 86400)).fetchone()[0]
        if primeiro is None:
            return 0.0
        return max(1 / 24, (agora - primeiro) / 86400)

    def meta(self, sessao):
        """Meta do mês: quanto já gastou, a projeção até o fim do mês e se cabe na meta.

        Projeção = gasto do mês até agora + (média diária dos últimos 7 dias × dias que faltam).
        """
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        u = self._usuario(conn, uid)
        tarifa = self.p.tarifa_de(u)
        if tarifa is None:
            return {"meta": u["meta_mensal_reais"], "sem_tarifa": True, "status": None}
        ids = self._ids(conn, uid)
        inicio_mes, proximo = self.p.inicio_do_mes(agora), self.p.inicio_do_proximo_mes(agora)
        gasto = self._wh_entre(conn, ids, inicio_mes, agora + 1) / 1000 * tarifa
        dias_medidos = self._dias_com_dados(conn, ids, agora)
        media_dia = (self._wh_entre(conn, ids, agora - 7 * 86400, agora + 1) / 1000 * tarifa / dias_medidos) if dias_medidos else 0.0
        dias_restantes = max(0.0, (proximo - agora) / 86400)
        projecao = gasto + media_dia * dias_restantes
        meta = u["meta_mensal_reais"]
        status = None
        if meta:
            status = "acima" if projecao > meta else ("atencao" if projecao > 0.9 * meta else "ok")
        return {
            "meta": meta, "gasto_ate_agora": round(gasto, 2), "media_diaria": round(media_dia, 2),
            "projecao": round(projecao, 2), "dias_restantes": round(dias_restantes, 1),
            "percentual": round(projecao / meta, 3) if meta else None, "status": status,
            "folga_diaria": round((meta - gasto) / dias_restantes, 2) if meta and dias_restantes >= 1 else None,
            "base_de_dias": round(dias_medidos, 1), "tarifa_kwh": tarifa, "sem_tarifa": False,
        }
