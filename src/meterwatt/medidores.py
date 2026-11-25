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
SEGREDO_VALIDO = re.compile(r"^[A-Za-z0-9]{24,64}$")       # senha que o próprio medidor sorteia
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
ATRASADAS_POR_MENSAGEM = 20       # o firmware manda 8 minutos por mensagem
ATRASADAS_ESPACO_S = 50           # dois minutos guardados não podem estar mais perto que isso
POTENCIA_MAXIMA_W = 30000         # o que um PZEM de 100 A mede, com folga: limite de energia por segundo
ATRASO_MAXIMO_S = 13 * 3600       # o firmware guarda até 12 horas
RECUPERACAO_VISIVEL_S = 7 * 86400
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


FASES_POR_LIGACAO = {"monofasica": 1, "bifasica": 2, "trifasica": 3}


def ler_fases(dados, anteriores, agora):
    """A lista "fases" da telemetria (de 2 a 3 fases), conferida. Devolve None se não veio ou veio estranha
    (aí vale a leitura de cima, como num medidor de uma fase). Cada fase: ok com os números, ou o erro e
    desde quando ela está sem resposta (para o aviso de fase)."""
    lista = dados.get("fases")
    if not isinstance(lista, list) or not 2 <= len(lista) <= 3:
        return None
    antes = {f.get("fase"): f for f in (anteriores or []) if isinstance(f, dict)}
    saida, vistas = [], set()
    for item in lista:
        if not isinstance(item, dict):
            return None
        fase = item.get("fase")
        if isinstance(fase, bool) or not isinstance(fase, int) or not 1 <= fase <= 3 or fase in vistas:
            return None
        vistas.add(fase)
        tensao, corrente, potencia = numero(item, "tensao_v", 0, 300), numero(item, "corrente_a", 0, 120), numero(item, "potencia_w", 0, 30000)
        if item.get("ok") is True and None not in (tensao, corrente, potencia):
            saida.append({"fase": fase, "ok": True, "tensao_v": tensao, "corrente_a": corrente, "potencia_w": potencia,
                          "fp": numero(item, "fp", 0, 1), "energia_wh": numero(item, "energia_wh", 0, 1e10)})
        else:
            anterior = antes.get(fase)
            desde = anterior["desde"] if anterior and not anterior.get("ok") and isinstance(anterior.get("desde"), (int, float)) else agora
            saida.append({"fase": fase, "ok": False, "erro": texto_simples(item.get("erro")) or "desconhecido", "desde": desde})
    return sorted(saida, key=lambda f: f["fase"])


def energia_entre(anterior, novo, segundos, pode_baixar=True):
    """Wh entre duas leituras do contador do PZEM e o contador a guardar.

    Um salto maior do que 30 kW durante todo o intervalo não é consumo: é leitura estranha ou mensagem forjada,
    e nada é somado. Contador menor: o módulo foi zerado (só ao vivo; nos minutos guardados sem conexão,
    `pode_baixar=False` mantém o anterior, porque ninguém zera o módulo sem conexão)."""
    if anterior is None:
        return 0.0, novo
    if novo < anterior:
        return 0.0, (novo if pode_baixar else anterior)
    if novo - anterior > POTENCIA_MAXIMA_W * max(segundos, 1) / 3600 + 10:
        return 0.0, novo
    return novo - anterior, novo


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
        self.limite_atrasadas = Limitador(120, 10 * 60)        # mensagens de minutos guardados, por medidor
        # Códigos errados de todos os medidores juntos: o limite por id não basta, porque a conta de provisão
        # pode publicar em nome de qualquer id (revisão de 25/11).
        self.limite_pareamento_geral = Limitador(60, 60)

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

    def ids_da_casa(self, conn, uid):
        """Os medidores que somam no total da conta: os de entrada ("casa"). Um medidor de circuito mede
        uma parte do que a entrada já mede; somar os dois contaria a mesma energia duas vezes."""
        return [r["id"] for r in conn.execute(
            "SELECT id FROM medidores WHERE usuario_id = ? AND papel = 'casa' ORDER BY pareado_em", (uid,))]

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
            if dados.get("tipo") == "atrasadas":
                return self.registrar_atrasadas(mid, dados)
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
        if not codigo or self.limite_pareamento.esgotado(mid, agora) or self.limite_pareamento_geral.esgotado("todos", agora):
            return "recusado"
        conn = self.p.db()
        linha = conn.execute("SELECT * FROM codigos_pareamento WHERE codigo_hash = ?", (self.p.cofre.hmac(codigo),)).fetchone()
        if not linha or linha["usado_em"] is not None or linha["expira_em"] < agora:
            self.limite_pareamento.registrar(mid, agora)
            self.limite_pareamento_geral.registrar("todos", agora)
            return "recusado"
        uid = linha["usuario_id"]
        if not self.p.consentimento(conn, uid, "consumo"):
            return "recusado"
        existente = conn.execute("SELECT usuario_id FROM medidores WHERE id = ?", (mid,)).fetchone()
        if not existente:
            total = conn.execute("SELECT COUNT(*) FROM medidores WHERE usuario_id = ?", (uid,)).fetchone()[0]
            if total >= self.p.pagamentos.limite_de_medidores(conn, uid):
                return "recusado"                 # o plano mudou entre gerar o código e parear
        # Senha própria: o medidor sorteia um segredo e manda junto com o código. Se o broker
        # aceita contas por aparelho, o servidor cadastra esse segredo como a senha dele.
        # Nunca para um medidor de outra conta: isso trocaria a senha do aparelho de outra pessoa.
        segredo = dados.get("segredo")
        segredo_ok = isinstance(segredo, str) and bool(SEGREDO_VALIDO.match(segredo))
        marca = self.p.cofre.hmac("medidor:" + segredo) if segredo_ok else None
        conhecido = conn.execute("SELECT segredo_hmac FROM medidores_conhecidos WHERE id = ?", (mid,)).fetchone()
        # Um aparelho que já foi visto só pareia de novo com o mesmo segredo (é ele mesmo), a não ser que continue
        # na mesma conta (a dona tem o código dela; serve se o segredo se perdeu num apagamento da memória).
        outro_aparelho = conhecido is not None and conhecido["segredo_hmac"] != marca and not (existente and existente["usuario_id"] == uid)
        if (existente and existente["usuario_id"] != uid) or outro_aparelho:
            with conn:
                conn.execute("UPDATE codigos_pareamento SET usado_em = ? WHERE id = ?", (agora, linha["id"]))
                self.p.auditar(conn, uid, "pareamento_recusado", mid)
            self.limite_pareamento_geral.registrar("todos", agora)
            return "recusado"
        # Firmware 3.6: o id de cliente MQTT (o id e um sufixo sorteado) e o tópico onde ele espera a resposta.
        cliente = dados.get("cliente")
        cliente = cliente if isinstance(cliente, str) and re.fullmatch(re.escape(mid) + r"-[0-9a-f]{8}", cliente) else None
        resposta = dados.get("resposta")
        topico_resposta = (f"meterwatt-resposta/{resposta}" if isinstance(resposta, str) and re.fullmatch(r"[0-9a-f]{16}", resposta)
                           else f"meterwatt/{mid}/comando")
        propria = bool(self.p.credenciais and segredo_ok and self.p.credenciais.registrar(mid, segredo, cliente))
        with conn:
            conn.execute("UPDATE codigos_pareamento SET usado_em = ?, medidor_id = ? WHERE id = ?", (agora, mid, linha["id"]))
            if not existente:
                conn.execute("INSERT INTO medidores (id, usuario_id, apelido, pareado_em, ultima_mensagem) VALUES (?, ?, ?, ?, ?)",
                             (mid, uid, f"Medidor {mid[-4:].upper()}", agora, agora))
                self.p.auditar(conn, uid, "medidor_pareado", mid)
            if propria:
                conn.execute("UPDATE medidores SET credencial_propria = 1 WHERE id = ?", (mid,))
            if marca:
                conn.execute("INSERT INTO medidores_conhecidos (id, segredo_hmac, visto_em) VALUES (?, ?, ?) "
                             "ON CONFLICT (id) DO UPDATE SET segredo_hmac = excluded.segredo_hmac, visto_em = excluded.visto_em",
                             (mid, marca, agora))
        self.limite_pareamento.zerar(mid)
        self.p.publicar(topico_resposta, "pareado_credencial" if propria else "pareado")
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
        fases = ler_fases(dados, json.loads(m["fases"]) if m["fases"] else None, agora)
        lidas = [f for f in fases if f["ok"]] if fases else []
        guardar_fases = json.dumps([{k: v for k, v in f.items() if k != "energia_wh"} for f in fases]) if fases else None
        uptime = numero(dados, "uptime_s", 0, 2 ** 32)
        fases_antes = json.loads(m["fases"]) if m["fases"] else None

        if dados.get("sensor_ok") is False or (fases and not lidas):
            with conn:
                self.p.qualidade.ao_ler(conn, m, agora, uptime, fases_antes, fases, simulado)
                conn.execute("UPDATE medidores SET uptime_s = ? WHERE id = ?", (uptime, mid))
                conn.execute("INSERT INTO leituras (medidor_id, instante, sensor_ok, erro_sensor, rssi, simulado) "
                             "VALUES (?, ?, 0, ?, ?, ?)", (mid, agora, texto_simples(dados.get("erro_sensor")) or "desconhecido",
                                                          rssi, simulado))
                conn.execute("UPDATE medidores SET ultima_mensagem = ?, ultima_gravacao = ?, status_mqtt = 'online', fw = ?, "
                             "potencia_anterior = NULL, fases = ? WHERE id = ?", (agora, agora, fw, guardar_fases, mid))
            return "sensor_sem_leitura"

        tensao = numero(dados, "tensao_v", 0, 300)
        potencia = numero(dados, "potencia_w", 0, 30000)
        corrente = numero(dados, "corrente_a", 0, 120)
        if corrente is None:
            corrente = numero(dados, "irms_a", 0, 120)
        wh = 0.0
        contador = m["contador_wh"]
        contadores = json.loads(m["contadores_fases"]) if m["contadores_fases"] else {}
        # Tempo desde a última vez que o contador foi lido (ao vivo ou pelos minutos guardados).
        desde_contador = agora - max(m["ultima_gravacao"] or 0, m["atrasadas_ate"] or 0)
        if lidas:
            # Várias fases: o total sai das fases lidas, e a energia é contada fase a fase. Uma fase que ficou
            # sem resposta guarda o último contador; quando volta, o que ela mediu no intervalo entra junto.
            tipo = "pzem"
            potencia = sum(f["potencia_w"] for f in lidas)
            corrente = sum(f["corrente_a"] for f in lidas)
            tensao = sum(f["tensao_v"] for f in lidas) / len(lidas)
            va = round(sum(f["tensao_v"] * f["corrente_a"] for f in lidas), 1)
            fp = min(1.0, potencia / va) if va > 0 else None
            freq = numero(dados, "frequencia_hz", 40, 70)
            valor = potencia
            for f in lidas:
                novo, chave = f["energia_wh"], str(f["fase"])
                if novo is None:
                    continue
                parte, contadores[chave] = energia_entre(contadores.get(chave), novo, desde_contador)
                wh += parte
        elif tensao is not None and potencia is not None and corrente is not None:
            tipo, valor = "pzem", potencia
            va = round(tensao * corrente, 1)
            fp, freq = numero(dados, "fp", 0, 1), numero(dados, "frequencia_hz", 40, 70)
            novo = numero(dados, "energia_wh", 0, 1e10)
            if novo is not None:
                # Contador menor = zerado no módulo; salto acima de 30 kW no intervalo = leitura estranha. Recomeça dali.
                wh, contador = energia_entre(contador, novo, desde_contador)
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
            if tipo == "pzem":
                tensoes = {f["fase"]: f["tensao_v"] for f in lidas} if lidas else {1: tensao}
                self.p.qualidade.registrar_tensao(conn, mid, agora, tensoes)
            self.p.qualidade.ao_ler(conn, m, agora, uptime, fases_antes, fases, simulado)
            if wh > 0 and m["papel"] == "casa":      # o total que só cresce (integração com o Home Assistant)
                conn.execute("UPDATE usuarios SET energia_total_wh = energia_total_wh + ? WHERE id = ?", (wh, m["usuario_id"]))
            conn.execute("UPDATE medidores SET ultima_mensagem = ?, ultima_gravacao = ?, status_mqtt = 'online', tipo = ?, fw = ?, "
                         "contador_wh = ?, potencia_anterior = ?, fases = ?, contadores_fases = ?, uptime_s = ? WHERE id = ?",
                         (agora, agora, tipo, fw, contador, valor if tipo == "corrente" else None, guardar_fases,
                          json.dumps(contadores) if contadores else None, uptime, mid))
        if tipo == "pzem":
            self.p.detetive.observar(conn, mid, m["usuario_id"], agora, potencia)
        return "gravada"

    def registrar_atrasadas(self, mid, dados):
        """Minutos que o medidor guardou sem conexão (firmware 3.5): cada um no seu horário.

        O horário vem como o tempo ligado do medidor; o de agora vem junto, então instante = agora - (uptime
        de agora - uptime do minuto). A energia sai dos contadores do PZEM, como nas leituras ao vivo: o
        firmware manda os minutos guardados antes de voltar às leituras ao vivo, então os contadores seguem
        em ordem e nada é contado duas vezes. Uma mensagem repetida não entra de novo (atrasadas_ate).
        """
        conn = self.p.db()
        m = conn.execute("SELECT * FROM medidores WHERE id = ?", (mid,)).fetchone()
        if not m:
            return "desconhecido"
        if not self.p.consentimento(conn, m["usuario_id"], "consumo"):
            return "sem_autorizacao"
        agora = self.p.agora()
        # O firmware manda no máximo 90 mensagens numa recuperação (12 horas, 8 minutos cada).
        if self.limite_atrasadas.esgotado(mid, agora):
            return "rapida_demais"
        self.limite_atrasadas.registrar(mid, agora)
        uptime = numero(dados, "uptime_agora", 0, 2 ** 32)
        lista = dados.get("minutos")
        if uptime is None or not isinstance(lista, list) or not 1 <= len(lista) <= ATRASADAS_POR_MENSAGEM:
            return "invalida"
        itens = []
        for item in lista:
            if not isinstance(item, dict):
                return "invalida"
            if isinstance(item.get("u"), bool) or not isinstance(item.get("u"), int):
                return "invalida"           # o firmware manda segundos inteiros
            u, n = numero(item, "u", 0, uptime), numero(item, "n", 1, 120)
            w, minimo, maximo = numero(item, "w", 0, 30000), numero(item, "min", 0, 30000), numero(item, "max", 0, 30000)
            e = item.get("e")
            if None in (u, n, w, minimo, maximo) or not isinstance(e, list) or not 1 <= len(e) <= 3:
                return "invalida"
            if any(x is not None and (isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 1e10) for x in e):
                return "invalida"
            itens.append((u, int(n), w, minimo, maximo, e))
        itens.sort(key=lambda i: i[0])
        simulado = 1 if dados.get("simulado") is True else 0
        contador = m["contador_wh"]
        contadores = json.loads(m["contadores_fases"]) if m["contadores_fases"] else {}
        ate = m["atrasadas_ate"] or 0
        ao_vivo = m["ultima_gravacao"] or 0
        lido_em = max(ate, ao_vivo)           # quando o contador foi lido pela última vez
        entraram, primeiro, total_wh = 0, None, 0.0
        with conn:
            for u, n, w, minimo, maximo, e in itens:
                instante = agora - (uptime - u)
                # Os minutos guardados cobrem a queda: depois da última leitura ao vivo, um por minuto (nunca mais
                # perto que 50 s: sem isso, uma mensagem forjada empilharia milhares de "minutos" no mesmo segundo).
                if (uptime - u > ATRASO_MAXIMO_S or instante < m["pareado_em"] or instante <= ao_vivo
                        or (ate and instante < ate + ATRASADAS_ESPACO_S)):
                    continue
                wh = 0.0
                if len(e) == 1:
                    if e[0] is not None:
                        wh, contador = energia_entre(contador, e[0], instante - lido_em, pode_baixar=False)
                else:
                    for fase, novo in enumerate(e, start=1):
                        if novo is None:
                            continue
                        parte, contadores[str(fase)] = energia_entre(contadores.get(str(fase)), novo, instante - lido_em,
                                                                     pode_baixar=False)
                        wh += parte
                lido_em = instante
                conn.execute(
                    "INSERT INTO consumo_5min (medidor_id, inicio, n, soma_w, min_w, max_w, wh, simulado) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (medidor_id, inicio) DO UPDATE SET n = n + excluded.n, soma_w = soma_w + excluded.soma_w, "
                    "min_w = MIN(min_w, excluded.min_w), max_w = MAX(max_w, excluded.max_w), wh = wh + excluded.wh, "
                    "simulado = MAX(simulado, excluded.simulado)",
                    (mid, int(instante // BALDE_S * BALDE_S), n, w * n, minimo, maximo, wh, simulado))
                ate = instante
                entraram += 1
                total_wh += wh
                primeiro = instante if primeiro is None else primeiro
            if total_wh > 0 and m["papel"] == "casa":
                conn.execute("UPDATE usuarios SET energia_total_wh = energia_total_wh + ? WHERE id = ?", (total_wh, m["usuario_id"]))
            if entraram:
                # As mensagens de uma recuperação chegam em segundos: somam no mesmo resumo.
                r = json.loads(m["ultima_recuperacao"]) if m["ultima_recuperacao"] else None
                if not r or agora - r["em"] > 120:
                    r = {"de": primeiro, "minutos": 0, "perdidos": 0}
                r.update(em=agora, ate=ate, minutos=r["minutos"] + entraram,
                         perdidos=max(r["perdidos"], int(numero(dados, "perdidos", 0, 1e6) or 0)))
                conn.execute("UPDATE medidores SET contador_wh = ?, contadores_fases = ?, atrasadas_ate = ?, ultima_recuperacao = ?, "
                             "ultima_mensagem = ?, status_mqtt = 'online' WHERE id = ?",
                             (contador, json.dumps(contadores) if contadores else None, ate, json.dumps(r), agora, mid))
        return "atrasadas"

    # ------------------------------------------------------------ pareamento pelo painel

    def gerar_codigo(self, sessao, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        if not self.p.consentimento(conn, uid, "consumo"):
            raise ErroDeNegocio("Para parear um medidor, autorize o tratamento dos dados de consumo em Privacidade.",
                                403, "sem_autorizacao")
        total = conn.execute("SELECT COUNT(*) FROM medidores WHERE usuario_id = ?", (uid,)).fetchone()[0]
        limite = self.p.pagamentos.limite_de_medidores(conn, uid)
        if total >= limite:
            if self.p.pagamentos.plano_de(conn, uid) == "casa":
                raise ErroDeNegocio(f"A sua conta já tem {limite} medidores, o máximo do plano.", 403, "limite_plano")
            raise ErroDeNegocio(f"O plano grátis permite {limite} medidor{'es' if limite != 1 else ''}. "
                                "Para parear mais, assine o plano Casa.", 403, "limite_plano")
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
        self.alterar(sessao, mid, {"apelido": apelido}, ip)

    def alterar(self, sessao, mid, dados, ip=None):
        """Nome e/ou o que o medidor mede: {"apelido": ..., "papel": "casa"|"circuito", "dentro_de": id}."""
        conn = self.p.db()
        uid = sessao["usuario_id"]
        m = self._do_usuario(conn, uid, mid)
        if not isinstance(dados, dict) or not ({"apelido", "papel"} & set(dados)):
            raise ErroDeNegocio("Nada para mudar.", 400)
        apelido = None
        if "apelido" in dados:
            apelido = limpar_apelido(dados["apelido"])
            if not apelido:
                raise ErroDeNegocio("O nome do medidor precisa ter de 1 a 40 caracteres.", 400)
        papel = dentro_de = None
        if "papel" in dados:
            papel, dentro_de = self._papel_valido(conn, uid, m, dados.get("papel"), dados.get("dentro_de"))
        with conn:
            if apelido is not None:
                conn.execute("UPDATE medidores SET apelido = ? WHERE id = ?", (apelido, mid))
                self.p.auditar(conn, uid, "medidor_renomeado", mid, ip)
            if papel is not None and (papel, dentro_de) != (m["papel"], m["dentro_de"]):
                conn.execute("UPDATE medidores SET papel = ?, dentro_de = ? WHERE id = ?", (papel, dentro_de, mid))
                self.p.auditar(conn, uid, "medidor_papel", f"{mid}: {'entrada' if papel == 'casa' else 'circuito'}", ip)

    def _papel_valido(self, conn, uid, m, papel, dentro_de):
        if papel not in ("casa", "circuito"):
            raise ErroDeNegocio("Diga se o medidor mede a entrada (casa) ou um circuito.", 400)
        if papel == "casa":
            return "casa", None
        entradas = [r["id"] for r in conn.execute(
            "SELECT id FROM medidores WHERE usuario_id = ? AND papel = 'casa' AND id != ? ORDER BY pareado_em", (uid, m["id"]))]
        if not entradas:
            raise ErroDeNegocio("Um circuito fica dentro de um medidor de entrada, e a conta precisa ter pelo menos um.", 400,
                                "sem_entrada")
        if conn.execute("SELECT 1 FROM medidores WHERE dentro_de = ? AND papel = 'circuito'", (m["id"],)).fetchone():
            raise ErroDeNegocio("Há circuitos dentro deste medidor. Mude esses circuitos antes.", 400, "tem_circuitos")
        if dentro_de is None and len(entradas) == 1:
            dentro_de = entradas[0]
        if dentro_de not in entradas:
            raise ErroDeNegocio("Escolha dentro de qual medidor de entrada este circuito fica.", 400, "dentro_de")
        return "circuito", dentro_de

    def remover(self, sessao, mid, ip=None):
        """Desfaz o pareamento e apaga as leituras desse medidor."""
        conn = self.p.db()
        self._do_usuario(conn, sessao["usuario_id"], mid)
        uid = sessao["usuario_id"]
        with conn:
            conn.execute("DELETE FROM medidores WHERE id = ?", (mid,))
            # Sem nenhum medidor de entrada, nada somaria no total: os que ficam passam a ser entrada.
            entradas = [r["id"] for r in conn.execute("SELECT id FROM medidores WHERE usuario_id = ? AND papel = 'casa'", (uid,))]
            if not entradas:
                conn.execute("UPDATE medidores SET papel = 'casa', dentro_de = NULL WHERE usuario_id = ?", (uid,))
            elif len(entradas) == 1:     # circuito que ficou sem "dentro de" só pode estar na entrada que sobrou
                conn.execute("UPDATE medidores SET dentro_de = ? WHERE usuario_id = ? AND papel = 'circuito' AND dentro_de IS NULL",
                             (entradas[0], uid))
            self.p.auditar(conn, uid, "medidor_removido", mid, ip)
        self.p.publicar(f"meterwatt/{mid}/comando", "despareado")
        self.esquecer(mid)

    def esquecer(self, mid):
        """Depois que o medidor saiu de uma conta: apaga a senha dele no broker e o que havia na memória."""
        self.p.detetive.detectores.pop(mid, None)
        if self.p.credenciais:
            self.p.credenciais.remover(mid)

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
                "papel": m["papel"], "dentro_de": m["dentro_de"],
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
                # Casa com mais de uma fase: cada uma (só com leitura recente). E quantas a ligação da conta pede,
                # para a tela avisar quando o medidor de entrada mede menos fases do que a casa tem.
                "fases": [{k: v for k, v in f.items() if k != "desde"} for f in json.loads(m["fases"])] if atual and m["fases"] else None,
                "fases_esperadas": FASES_POR_LIGACAO.get(sessao.get("ligacao")) if m["papel"] == "casa" else None,
                # A última vez que o medidor ficou sem conexão e mandou depois o que mediu (firmware 3.5).
                "recuperacao": self._recuperacao(m, agora),
            })
        return {"medidores": saida, "tarifa_kwh": tarifa, "limite_offline_s": LIMITE_OFFLINE_S,
                "servidor_em": self.p.iso(agora)}

    def _recuperacao(self, m, agora):
        r = json.loads(m["ultima_recuperacao"]) if m["ultima_recuperacao"] else None
        if not r or agora - r["em"] > RECUPERACAO_VISIVEL_S:
            return None
        return {"de": self.p.iso(r["de"]), "ate": self.p.iso(r["ate"]), "em": self.p.iso(r["em"]), "minutos": r["minutos"],
                "perdidos": r["perdidos"]}

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
        ids = self.ids_da_casa(conn, uid)
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
        return self.fantasma_de(sessao["usuario_id"])

    def fantasma_de(self, uid):
        """Consumo fantasma: o que a casa gasta parada (aparelhos em espera, roteador, geladeira...).

        Para cada noite (0h às 5h) dos últimos 7 dias com pelo menos 80% dos intervalos de
        5 minutos medidos, pega o menor intervalo. A mediana dessas noites é a "base" do medidor.
        Usar a mediana evita que uma noite atípica (luz cortada, viagem) distorça o número.
        """
        conn = self.p.db()
        agora = self.p.agora()
        tarifa = self.p.tarifa_de(self._usuario(conn, uid))
        por_medidor, watts_total, noites_usadas, estimado = [], 0.0, 0, False
        dias = [self.p.inicio_do_dia(agora, d) for d in range(0, 8)]
        dias = [d for d in dias if d + NOITE_FIM_H * 3600 <= agora and agora - (d + NOITE_INICIO_H * 3600) <= 7 * 86400 + NOITE_FIM_H * 3600]
        for m in conn.execute("SELECT id, apelido, tipo, papel FROM medidores WHERE usuario_id = ? ORDER BY pareado_em", (uid,)).fetchall():
            minimos = [x for x in (self._minimo_noturno(conn, m["id"], d) for d in dias) if x is not None]
            if len(minimos) >= 2:
                base = statistics.median(minimos)
                if m["papel"] == "casa":          # o de um circuito já está dentro da entrada
                    watts_total += base
                    noites_usadas = max(noites_usadas, len(minimos))
                    estimado = estimado or m["tipo"] == "corrente"
                por_medidor.append({"id": m["id"], "apelido": m["apelido"], "papel": m["papel"], "watts": round(base, 1),
                                    "noites": len(minimos)})
            else:
                por_medidor.append({"id": m["id"], "apelido": m["apelido"], "papel": m["papel"], "watts": None, "noites": len(minimos)})
        ids = [x["id"] for x in por_medidor if x["watts"] is not None and x["papel"] == "casa"]
        if not ids:
            return {"disponivel": False, "medidores": por_medidor,
                    "motivo": "Precisamos de pelo menos 2 noites medidas (entre 0h e 5h) para calcular."}
        kwh_mes = watts_total * 24 * 365 / 12 / 1000       # mês médio: um ano dividido por 12
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

    def _minimo_noturno(self, conn, mid, dia):
        """Menor média de 5 minutos entre 0h e 5h do dia que começa em `dia`; None se a noite não foi medida."""
        inicio, fim = dia + NOITE_INICIO_H * 3600, dia + NOITE_FIM_H * 3600
        medias = [r["soma_w"] / r["n"] for r in conn.execute(
            "SELECT n, soma_w FROM consumo_5min WHERE medidor_id = ? AND inicio >= ? AND inicio < ? AND n > 0",
            (mid, int(inicio), int(fim)))]
        if len(medias) < COBERTURA_MINIMA_NOITE * (fim - inicio) // BALDE_S:
            return None
        return min(medias)

    def base_noturna(self, conn, ids, dias):
        """Consumo em espera (W) somando os medidores, pela mediana das noites dos `dias`. None sem 2 noites medidas."""
        total, algum = 0.0, False
        for mid in ids:
            minimos = [x for x in (self._minimo_noturno(conn, mid, d) for d in dias) if x is not None]
            if len(minimos) >= 2:
                total += statistics.median(minimos)
                algum = True
        return total if algum else None

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
        return self.meta_de(sessao["usuario_id"])

    def meta_de(self, uid):
        """Meta do mês: quanto já gastou, a projeção até o fim do mês e se cabe na meta.

        Projeção = gasto do mês até agora + (média diária dos últimos 7 dias × dias que faltam).
        """
        conn = self.p.db()
        agora = self.p.agora()
        u = self._usuario(conn, uid)
        tarifa = self.p.tarifa_de(u)
        if tarifa is None:
            return {"meta": u["meta_mensal_reais"], "sem_tarifa": True, "status": None}
        ids = self.ids_da_casa(conn, uid)
        inicio_mes, proximo = self.p.inicio_do_mes(agora), self.p.inicio_do_proximo_mes(agora)
        kwh_gasto = self._wh_entre(conn, ids, inicio_mes, agora + 1) / 1000
        gasto = kwh_gasto * tarifa
        dias_medidos = self._dias_com_dados(conn, ids, agora)
        kwh_dia = (self._wh_entre(conn, ids, agora - 7 * 86400, agora + 1) / 1000 / dias_medidos) if dias_medidos else 0.0
        media_dia = kwh_dia * tarifa
        dias_restantes = max(0.0, (proximo - agora) / 86400)
        # A conta de verdade cobra pelo menos o mínimo da ligação e soma a bandeira do mês (veja tarifas.py).
        projecao, conta = self.p.tarifas.ajustar_previsao(conn, u, kwh_gasto + kwh_dia * dias_restantes, tarifa, agora)
        meta = u["meta_mensal_reais"]
        status = None
        if meta:
            status = "acima" if projecao > meta else ("atencao" if projecao > 0.9 * meta else "ok")
        return {
            "meta": meta, "gasto_ate_agora": round(gasto, 2), "media_diaria": round(media_dia, 2),
            "projecao": round(projecao, 2), "dias_restantes": round(dias_restantes, 1),
            "percentual": round(projecao / meta, 3) if meta else None, "status": status,
            "folga_diaria": round((meta - gasto) / dias_restantes, 2) if meta and dias_restantes >= 1 else None,
            "base_de_dias": round(dias_medidos, 1), "tarifa_kwh": tarifa, "sem_tarifa": False, **conta,
        }
