"""Avisos: o MeterWatt conta quando algo merece atenção, sem a pessoa precisar abrir o painel.

Cada aviso aparece dentro do aplicativo e, se a pessoa quiser, também vai por e-mail e como
notificação nos aparelhos em que ela ligou as notificações (notificacoes.py).
A "chave" de cada aviso impede repetição: um medidor que caiu avisa uma vez por queda,
a meta avisa uma vez por mês em cada nível, o consumo fora do normal uma vez por dia.

Os e-mails de aviso levam totais de consumo (kWh e reais). Quem não quiser isso na caixa
de entrada desliga o envio por e-mail na tela Avisos e continua vendo tudo no aplicativo.
Para enviar por outro canal (WhatsApp, por exemplo), o ponto de entrada é `_entregar`.
"""
import logging
import statistics
from datetime import datetime, timedelta

from .erros import ErroDeNegocio

TIPOS = {
    "medidor_offline": {
        "nome": "Medidor sem sinal",
        "descricao": "Quando um medidor fica mais de 30 minutos sem enviar leituras.",
    },
    "meta": {
        "nome": "Meta do mês",
        "descricao": "Quando a previsão da conta passa de 90% da meta e quando passa da meta.",
    },
    "consumo_incomum": {
        "nome": "Consumo fora do normal",
        "descricao": "Quando uma hora gasta mais que o dobro do normal da casa para aquele horário.",
    },
    "resumo_semanal": {
        "nome": "Resumo da semana",
        "descricao": "Toda segunda-feira: quanto a casa gastou, a comparação com a semana anterior e o consumo fantasma.",
    },
    "resumo_mensal": {
        "nome": "Resumo do mês",
        "descricao": "No dia 1º: quanto a casa gastou no mês que passou, a comparação com o anterior e o relatório completo.",
    },
}
log = logging.getLogger("meterwatt.alertas")
OFFLINE_S = 30 * 60
OFFLINE_MAXIMO_S = 3 * 86400       # depois de 3 dias parado, não adianta avisar de novo
INCOMUM_FATOR = 2.0
INCOMUM_MINIMO_WH = 300            # diferença mínima para valer um aviso
INCOMUM_DIAS_DE_BASE = 4
HORA_DO_RESUMO = 8
DIAS_DA_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _n(valor, casas=1):
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _reais(valor):
    return "R$ " + _n(valor, 2)


class Alertas:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ criar e entregar

    def _canais(self, conn, uid, tipo):
        """Por onde o aviso sai além do app. Sem escolha gravada, sai por e-mail e por notificação."""
        linha = conn.execute("SELECT por_email, por_push FROM avisos_config WHERE usuario_id = ? AND tipo = ?", (uid, tipo)).fetchone()
        return {"por_email": True, "por_push": True} if linha is None else {"por_email": bool(linha["por_email"]),
                                                                              "por_push": bool(linha["por_push"])}

    def _por_email(self, conn, uid, tipo):
        return self._canais(conn, uid, tipo)["por_email"]

    def criar(self, uid, tipo, chave, titulo, texto, url=None):
        """Grava o aviso (uma vez por chave) e entrega. Devolve True se era novo. `url`: a tela que o aviso abre."""
        conn = self.p.db()
        with conn:
            novo = conn.execute(
                "INSERT OR IGNORE INTO avisos (usuario_id, tipo, chave, criado_em, titulo, texto) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, tipo, chave, self.p.agora(), titulo, texto)).rowcount == 1
        if novo:
            # Na bandeja do celular, o aviso novo substitui o anterior do mesmo assunto (o mesmo medidor, a meta…).
            tag = f"offline-{chave.split(':')[1]}" if tipo == "medidor_offline" else tipo
            self._entregar(conn, uid, tipo, titulo, texto, tag, url)
        return novo

    def _entregar(self, conn, uid, tipo, titulo, texto, tag=None, url=None):
        canais = self._canais(conn, uid, tipo)
        if canais["por_push"]:
            try:
                self.p.notificacoes.entregar(uid, tipo, titulo, texto, tag, url)
            except Exception:   # notificação que falha não impede o e-mail
                log.exception("Falha ao mandar a notificação do aviso %s", tipo)
        if not canais["por_email"]:
            return
        u = conn.execute("SELECT nome, email FROM usuarios WHERE id = ?", (uid,)).fetchone()
        self.p.enviar_email(u["email"], f"MeterWatt: {titulo}", (
            f"Olá, {u['nome']}.\n\n{texto}\n\nPara ver os detalhes: {self.p.cfg.url_publica}{url or '/#painel'}\n"
            f"Para parar de receber este aviso por e-mail: {self.p.cfg.url_publica}/#avisos\n\nEquipe MeterWatt\n"))

    # ------------------------------------------------------------ verificação periódica

    def verificar(self):
        """Roda a cada 5 minutos (veja __main__.py). Devolve quantos avisos novos criou."""
        conn = self.p.db()
        criados = 0
        usuarios = [r["usuario_id"] for r in conn.execute("SELECT DISTINCT usuario_id FROM medidores")]
        for uid in usuarios:
            if not self.p.consentimento(conn, uid, "consumo"):
                continue
            for regra in (self._offline, self._meta, self._incomum, self._resumo, self._resumo_mensal):
                try:
                    criados += regra(conn, uid)
                except Exception:   # uma regra com problema não cala as outras
                    log.exception("Falha na regra %s", regra.__name__)
        return criados

    def _offline(self, conn, uid):
        agora = self.p.agora()
        n = 0
        for m in conn.execute("SELECT id, apelido, ultima_mensagem FROM medidores WHERE usuario_id = ? AND ultima_gravacao IS NOT NULL",
                              (uid,)).fetchall():
            parado = agora - (m["ultima_mensagem"] or agora)
            if OFFLINE_S < parado < OFFLINE_MAXIMO_S:
                desde = datetime.fromtimestamp(m["ultima_mensagem"], self.p.cfg.zona).strftime("%H:%M de %d/%m")
                n += self.criar(uid, "medidor_offline", f"offline:{m['id']}:{int(m['ultima_mensagem'])}",
                                f"{m['apelido']} está sem sinal",
                                f"O medidor {m['apelido']} não envia leituras desde {desde}. Confira se ele está ligado "
                                "e se o Wi-Fi da casa está funcionando. Enquanto isso, o consumo não está sendo registrado.")
        return n

    def _meta(self, conn, uid):
        m = self.p.medidores.meta_de(uid)
        if not m.get("meta") or m.get("sem_tarifa") or m["status"] not in ("atencao", "acima") or m["base_de_dias"] < 3:
            return 0
        mes = datetime.fromtimestamp(self.p.agora(), self.p.cfg.zona).strftime("%Y-%m")
        if m["status"] == "acima":
            texto = (f"No ritmo dos últimos dias, a conta deste mês fecha em {_reais(m['projecao'])}, "
                     f"{_reais(m['projecao'] - m['meta'])} acima da sua meta de {_reais(m['meta'])}. "
                     f"Já foram {_reais(m['gasto_ate_agora'])}.")
            if m["folga_diaria"] is not None and m["folga_diaria"] > 0:
                texto += f" Para caber na meta, o limite é {_reais(m['folga_diaria'])} por dia até o fim do mês."
            texto += " Na aba Economia dá para simular onde cortar."
            return self.criar(uid, "meta", f"meta:acima:{mes}", "A previsão passou da meta do mês", texto)
        return self.criar(uid, "meta", f"meta:atencao:{mes}", "A conta está chegando perto da meta", (
            f"A previsão para este mês é {_reais(m['projecao'])}, mais de 90% da sua meta de {_reais(m['meta'])}. "
            f"Já foram {_reais(m['gasto_ate_agora'])}."))

    def _wh_da_hora(self, conn, ids, inicio):
        marcadores = ",".join("?" * len(ids))
        linha = conn.execute(f"SELECT SUM(wh) AS wh, COUNT(*) AS n FROM consumo_5min WHERE medidor_id IN ({marcadores}) "
                             "AND inicio >= ? AND inicio < ?", (*ids, inicio, inicio + 3600)).fetchone()
        return (linha["wh"] or 0.0, linha["n"])

    def _incomum(self, conn, uid):
        ids = self.p.medidores.ids_da_casa(conn, uid)      # circuito não soma de novo
        agora = self.p.agora()
        fim = int(agora // 3600 * 3600)          # última hora cheia
        inicio = fim - 3600
        atual, baldes = self._wh_da_hora(conn, ids, inicio)
        if baldes < 10 * len(ids):               # hora incompleta: não compara
            return 0
        base = []
        for d in range(1, 8):
            wh, n = self._wh_da_hora(conn, ids, inicio - d * 86400)
            if n >= 10 * len(ids):
                base.append(wh)
        if len(base) < INCOMUM_DIAS_DE_BASE:
            return 0
        normal = statistics.median(base)
        if atual < INCOMUM_FATOR * normal or atual - normal < INCOMUM_MINIMO_WH:
            return 0
        zona = self.p.cfg.zona
        h1, h2 = (datetime.fromtimestamp(t, zona).strftime("%Hh") for t in (inicio, fim))
        return self.criar(uid, "consumo_incomum", f"incomum:{self.p.dia_local(inicio)}", "Consumo fora do normal", (
            f"Entre {h1} e {h2} a casa gastou {_n(atual / 1000, 2)} kWh. O normal para esse horário, nos últimos dias, "
            f"é perto de {_n(normal / 1000, 2)} kWh. Vale conferir se algum aparelho ficou ligado sem querer."))

    def _cobertura(self, conn, ids, inicio, fim):
        """Fração do período em que o medidor mais presente esteve medindo (0 a 1)."""
        marcadores = ",".join("?" * len(ids))
        maior = conn.execute(f"SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM consumo_5min WHERE medidor_id IN ({marcadores}) "
                             "AND inicio >= ? AND inicio < ? GROUP BY medidor_id)", (*ids, inicio, fim)).fetchone()[0] or 0
        return maior / max(1, (fim - inicio) / 300)

    def _resumo_mensal(self, conn, uid):
        """No dia 1º, a partir das 8h: o mês que passou, com o link do relatório."""
        local = datetime.fromtimestamp(self.p.agora(), self.p.cfg.zona)
        if local.day != 1 or local.hour < HORA_DO_RESUMO:
            return 0
        anterior = (local.date() - timedelta(days=1)).strftime("%Y-%m")
        r = self.p.relatorios.mes({"usuario_id": uid}, anterior)
        if r["kwh"] <= 0:
            return 0
        nome = r["nome"].split(" de ")[0]
        frases = [f"Em {nome}, a casa gastou {_n(r['kwh'])} kWh" + (f", cerca de {_reais(r['custo'])}." if r["custo"] is not None else ".")]
        if r["media_dia"] is not None:
            frases.append(f"Média de {_n(r['media_dia'], 1)} kWh por dia medido.")
        if r["anterior"] and r["anterior"]["variacao"] is not None:
            v = r["anterior"]["variacao"] * 100
            frases.append("Praticamente igual ao mês anterior." if abs(v) < 3 else
                          f"Isso é {_n(abs(v), 0)}% {'a mais' if v > 0 else 'a menos'} por dia que em {r['anterior']['nome'].split(' de ')[0]}.")
        if r["mais_caro"]:
            dia = r["mais_caro"]["dia"]
            frases.append(f"O dia que mais gastou foi {dia[8:]}/{dia[5:7]}, com {_n(r['mais_caro']['kwh'], 1)} kWh.")
        if r["dias_medidos"] < r["dias_no_mes"] - 1:
            frases.append(f"O medidor mediu {r['dias_medidos']} dos {r['dias_no_mes']} dias por inteiro.")
        frases.append("O relatório completo, para imprimir ou baixar, está no app.")
        return self.criar(uid, "resumo_mensal", f"mensal:{anterior}", f"Resumo de {nome}", " ".join(frases), url=f"/#relatorio/{anterior}")

    def _resumo(self, conn, uid):
        zona = self.p.cfg.zona
        agora = self.p.agora()
        local = datetime.fromtimestamp(agora, zona)
        if local.weekday() != 0 or local.hour < HORA_DO_RESUMO:
            return 0
        fim = self.p.inicio_do_dia(agora)                       # segunda-feira, 0h
        inicio = self.p.inicio_do_dia(agora, 7)
        ids = self.p.medidores.ids_da_casa(conn, uid)      # circuito não soma de novo
        wh = self.p.medidores._wh_entre(conn, ids, inicio, fim)
        if wh <= 0:
            return 0
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        tarifa = self.p.tarifa_de(u)
        linhas = [f"Na semana de {self.p.dia_local(inicio)[8:]}/{self.p.dia_local(inicio)[5:7]} a "
                  f"{self.p.dia_local(fim - 3600)[8:]}/{self.p.dia_local(fim - 3600)[5:7]}, a casa gastou {_n(wh / 1000)} kWh"
                  + (f", cerca de {_reais(wh / 1000 * tarifa)}." if tarifa is not None else ".")]
        anterior = self.p.medidores._wh_entre(conn, ids, self.p.inicio_do_dia(agora, 14), inicio)
        # Só compara semanas medidas quase por inteiro: meia semana contra uma inteira dá um número sem sentido.
        if (anterior > 0 and self._cobertura(conn, ids, self.p.inicio_do_dia(agora, 14), inicio) >= 0.8
                and self._cobertura(conn, ids, inicio, fim) >= 0.8):
            variacao = (wh - anterior) / anterior * 100
            if abs(variacao) < 3:
                linhas.append("Praticamente igual à semana anterior.")
            else:
                linhas.append(f"Isso é {_n(abs(variacao), 0)}% {'a mais' if variacao > 0 else 'a menos'} que na semana anterior "
                              f"({_n(anterior / 1000)} kWh).")
        por_dia = []
        for d in range(7, 0, -1):
            a = self.p.inicio_do_dia(agora, d)
            por_dia.append((self.p.medidores._wh_entre(conn, ids, a, self.p.inicio_do_dia(agora, d - 1)), a))
        maior, quando = max(por_dia)
        if maior > 0:
            dia = DIAS_DA_SEMANA[datetime.fromtimestamp(quando + 3600, zona).weekday()]
            linhas.append(f"O dia de maior consumo foi {dia}, com {_n(maior / 1000)} kWh.")
        fantasma = self.p.medidores.fantasma_de(uid)
        if fantasma.get("disponivel"):
            custo = f" ({_reais(fantasma['custo_mes'])} por mês)" if fantasma.get("custo_mes") is not None else ""
            linhas.append(f"Consumo fantasma: {_n(fantasma['watts'], 0)} W sempre ligados{custo}.")
        meta = self.p.medidores.meta_de(uid)
        if meta.get("meta") and not meta.get("sem_tarifa"):
            linhas.append(f"Previsão da conta do mês: {_reais(meta['projecao'])}, para uma meta de {_reais(meta['meta'])}.")
        v = self.p.economia.verificacao_de(uid)
        if v and v["pronto"] and v["total"] and v["total"]["variacao"] is not None:
            desde = datetime.fromisoformat(v["desde"]).strftime("%d/%m")
            variacao = v["total"]["variacao"]
            if abs(variacao) < 0.02:
                linhas.append(f"Plano de economia: desde {desde}, o consumo por dia ficou praticamente igual ao das duas semanas antes.")
            elif variacao < 0:
                reais = v["total"]["reais_mes"]
                linhas.append(f"Plano de economia: desde {desde}, a casa gastou {_n(-variacao * 100, 0)}% menos por dia"
                              + (f", cerca de {_reais(reais)} por mês." if reais else "."))
            else:
                linhas.append(f"Plano de economia: desde {desde}, a casa gastou {_n(variacao * 100, 0)}% mais por dia que nas duas "
                              "semanas antes. Calor, visitas e férias mudam o consumo: vale olhar a aba Economia.")
        return self.criar(uid, "resumo_semanal", f"resumo:{self.p.dia_local(fim)}", "Resumo da semana", " ".join(linhas))

    # ------------------------------------------------------------ telas

    def listar(self, sessao, limite=40):
        conn = self.p.db()
        linhas = conn.execute("SELECT id, tipo, criado_em, titulo, texto, lido_em FROM avisos WHERE usuario_id = ? "
                              "ORDER BY criado_em DESC, id DESC LIMIT ?", (sessao["usuario_id"], limite)).fetchall()
        return {
            "avisos": [{"id": l["id"], "tipo": l["tipo"], "quando": self.p.iso(l["criado_em"]), "titulo": l["titulo"],
                        "texto": l["texto"], "lido": l["lido_em"] is not None} for l in linhas],
            "config": [{"tipo": t, "nome": i["nome"], "descricao": i["descricao"], **self._canais(conn, sessao["usuario_id"], t)}
                       for t, i in TIPOS.items()],
        }

    def nao_lidos(self, conn, uid):
        return conn.execute("SELECT COUNT(*) FROM avisos WHERE usuario_id = ? AND lido_em IS NULL", (uid,)).fetchone()[0]

    def marcar_lidos(self, sessao):
        conn = self.p.db()
        with conn:
            conn.execute("UPDATE avisos SET lido_em = ? WHERE usuario_id = ? AND lido_em IS NULL", (self.p.agora(), sessao["usuario_id"]))

    def configurar(self, sessao, tipo, dados):
        """Liga ou desliga o e-mail e a notificação de um tipo de aviso. Só muda o canal que vier no pedido."""
        escolhas = {k: dados.get(k) for k in ("por_email", "por_push") if k in dados}
        if tipo not in TIPOS or not escolhas or not all(isinstance(v, bool) for v in escolhas.values()):
            raise ErroDeNegocio("Aviso desconhecido.", 400)
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            canais = {**self._canais(conn, uid, tipo), **escolhas}
            conn.execute("INSERT INTO avisos_config (usuario_id, tipo, por_email, por_push) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (usuario_id, tipo) DO UPDATE SET por_email = excluded.por_email, por_push = excluded.por_push",
                         (uid, tipo, int(canais["por_email"]), int(canais["por_push"])))
