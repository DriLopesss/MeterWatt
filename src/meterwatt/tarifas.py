"""A conta de luz mais perto da real: Tarifa Branca, bandeira do mês e o mínimo da conta.

Regras usadas (fontes no fim do arquivo):
- Postos da Tarifa Branca (REN ANEEL 1.000/2021, art. 2º, XXXVIII): ponta = 3 horas seguidas, definidas pela
  distribuidora; intermediário = a hora antes e a hora depois da ponta; fora de ponta = o resto. Sábados,
  domingos, terça de Carnaval, sexta-feira da Paixão, Corpus Christi e os feriados nacionais de 1º/1, 21/4,
  1º/5, 7/9, 12/10, 2/11, 15/11 e 25/12 são fora de ponta o dia inteiro. O 20 de novembro (feriado nacional
  desde a Lei 14.759/2023) não está nessa lista da REN; aqui ele segue a REN, como dia útil.
- Quem pode aderir: baixa tensão (grupo B), menos a subclasse baixa renda e a iluminação pública.
- Mudar leva até 30 dias; voltar para a convencional pode a qualquer hora, mas uma nova adesão à branca só
  depois de 180 dias (REN 1.000, arts. 222 e 223).
- Mínimo da conta (custo de disponibilidade, REN 1.000, arts. 290 e 291): 30 kWh na ligação monofásica (ou
  bifásica a dois fios), 50 kWh na bifásica, 100 kWh na trifásica. A conta cobra o maior entre o consumo e o
  mínimo.
- Bandeira tarifária: acréscimo por kWh consumido, o mesmo na convencional e na branca, então não muda a
  comparação entre as duas. Os valores são sem impostos.

Os preços da Tarifa Branca mudam por distribuidora e a cada reajuste anual. Por isso vêm da pessoa: os quatro
números (convencional e os três postos) copiados da mesma tabela da distribuidora, com ou sem impostos. A
comparação usa as proporções entre eles e, se a pessoa informou quanto paga por kWh de verdade, converte para
esse valor (os impostos sobre a energia são porcentagens, então a proporção entre os postos se mantém).
"""
import functools
import re
from datetime import date, datetime, timedelta

from .erros import ErroDeNegocio

# R$ por 100 kWh, sem impostos. Amarela: ANEEL, 31/07/2026. Vermelha patamares 1 e 2: valores citados em
# 31/07/2026 (Folha de Pernambuco) e 27/10/2026 (Diário do Nordeste). Conferir em gov.br/aneel a cada mudança.
BANDEIRAS = {"verde": 0.0, "amarela": 1.885, "vermelha1": 4.463, "vermelha2": 7.877}
NOMES_BANDEIRA = {"verde": "verde", "amarela": "amarela", "vermelha1": "vermelha patamar 1", "vermelha2": "vermelha patamar 2"}
MINIMO_KWH = {"monofasica": 30, "bifasica": 50, "trifasica": 100}

DIAS_DO_PERFIL = 28              # dias inteiros olhados para montar o dia típico
COBERTURA_DIA = 0.8              # dia que entra no perfil: pelo menos 80% das meias horas medidas
INTERVALOS_MEIA_HORA = 5         # de 6 intervalos de 5 minutos, para a meia hora valer
MINIMO_DIAS = 5                  # dias bons para comparar
MINIMO_DIAS_UTEIS = 3
PONTA_EXEMPLO_MIN = 18 * 60      # quando a pessoa ainda não disse o horário da distribuidora
PONTA_MIN, PONTA_MAX = 15 * 60, 20 * 60   # a ponta começa entre 15h e 20h (o intermediário fecha até meia-noite)
PRECO_MAXIMO = 10.0              # R$ por kWh
APARELHOS_NA_PONTA = 3


# ------------------------------------------------------------------ calendário dos postos

def _pascoa(ano):
    """Domingo de Páscoa no calendário gregoriano (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m7 = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * m7) // 451
    mes, dia = divmod(h + m7 - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


@functools.lru_cache(maxsize=32)
def dias_sem_ponta(ano):
    """Feriados em que a ponta não se aplica (REN 1.000, art. 2º, XXXVIII, a)."""
    p = _pascoa(ano)
    fixos = {date(ano, m, d) for m, d in ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25))}
    return frozenset(fixos | {p - timedelta(days=47), p - timedelta(days=2), p + timedelta(days=60)})


def tem_ponta(dia):
    """Dia útil para a tarifa: de segunda a sexta, fora dos feriados da lista."""
    return dia.weekday() < 5 and dia not in dias_sem_ponta(dia.year)


def posto(minuto_do_dia, inicio_ponta):
    """Posto de um minuto (0 a 1439) num dia com ponta."""
    if inicio_ponta <= minuto_do_dia < inicio_ponta + 180:
        return "ponta"
    if inicio_ponta - 60 <= minuto_do_dia < inicio_ponta or inicio_ponta + 180 <= minuto_do_dia < inicio_ponta + 240:
        return "intermediario"
    return "fora"


def _hora(minutos):
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def _minutos(texto):
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", texto or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def dias_por_mes(hoje):
    """Dias com ponta e sem ponta num mês médio dos próximos 12 meses (conta os feriados de verdade)."""
    uteis = sum(1 for n in range(365) if tem_ponta(hoje + timedelta(days=n)))
    return uteis / 12, (365 - uteis) / 12


def _n(v, casas=2):
    return None if v is None else round(v, casas)


class Tarifas:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ bandeira do mês

    def bandeira_do_mes(self, conn, instante):
        mes = datetime.fromtimestamp(instante, self.p.cfg.zona).strftime("%Y-%m")
        linha = conn.execute("SELECT cor FROM bandeiras WHERE mes = ?", (mes,)).fetchone()
        cor = linha["cor"] if linha else None
        return {"mes": mes, "cor": cor, "nome": NOMES_BANDEIRA.get(cor), "informada": cor is not None,
                "reais_por_100_kwh": BANDEIRAS.get(cor, 0.0)}

    def registrar_bandeira(self, mes, cor):
        """Chamado pelo comando `python -m meterwatt --bandeira AAAA-MM COR`, quando a ANEEL anuncia a do mês."""
        if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", mes or ""):
            raise ValueError("O mês vai no formato AAAA-MM, como 2026-12.")
        if cor not in BANDEIRAS:
            raise ValueError(f"Cor desconhecida. Use uma destas: {', '.join(BANDEIRAS)}.")
        conn = self.p.db()
        with conn:
            conn.execute("INSERT INTO bandeiras (mes, cor, registrada_em) VALUES (?, ?, ?) "
                         "ON CONFLICT(mes) DO UPDATE SET cor = excluded.cor, registrada_em = excluded.registrada_em",
                         (mes, cor, self.p.agora()))
        return {"mes": mes, "cor": cor, "nome": NOMES_BANDEIRA[cor], "reais_por_100_kwh": BANDEIRAS[cor]}

    def bandeira_pendente(self):
        """Lembrete para o log do servidor quando a bandeira do mês ainda não foi registrada."""
        b = self.bandeira_do_mes(self.p.db(), self.p.agora())
        if b["informada"]:
            return None
        return (f"A bandeira tarifária de {b['mes']} ainda não foi registrada: a previsão da conta não soma acréscimo. "
                f"Quando a ANEEL anunciar, rode: python -m meterwatt --bandeira {b['mes']} COR")

    def bandeiras(self):
        return [dict(r) for r in self.p.db().execute("SELECT mes, cor FROM bandeiras ORDER BY mes DESC LIMIT 24")]

    # ------------------------------------------------------------ a conta do mês

    def ajustar_previsao(self, conn, usuario, kwh_mes, tarifa, instante):
        """A previsão em reais com o mínimo da conta e a bandeira do mês. Devolve (reais, detalhes)."""
        minimo = MINIMO_KWH.get(usuario["ligacao"])
        cobrado = max(kwh_mes, minimo) if minimo else kwh_mes
        bandeira = self.bandeira_do_mes(conn, instante)
        adicional = cobrado * bandeira["reais_por_100_kwh"] / 100
        bandeira["adicional"] = round(adicional, 2)
        return cobrado * tarifa + adicional, {
            "kwh_previsto": round(kwh_mes, 1), "bandeira": bandeira,
            "minimo": None if not minimo else {"kwh": minimo, "aplicado": kwh_mes < minimo},
        }

    # ------------------------------------------------------------ preferências

    def salvar(self, sessao, dados, ip=None):
        """Horário de ponta da distribuidora e os quatro preços da tabela dela."""
        if not isinstance(dados, dict):
            raise ErroDeNegocio("Pedido inválido.", 400)
        inicio = dados.get("ponta_inicio")
        if inicio is not None:
            inicio = _minutos(inicio) if isinstance(inicio, str) else None
            if inicio is None or inicio % 30 or not PONTA_MIN <= inicio <= PONTA_MAX:
                raise ErroDeNegocio("O começo da ponta vai de 15:00 a 20:00, de meia em meia hora.", 400)
        nomes = {"convencional": "convencional", "fora": "fora de ponta", "intermediario": "intermediário", "ponta": "ponta"}
        precos = {}
        for chave, nome in nomes.items():
            v = dados.get(chave)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= PRECO_MAXIMO:
                raise ErroDeNegocio(f"O preço {nome} precisa ser um número maior que 0 e até {PRECO_MAXIMO:.0f} (R$ por kWh).", 400)
            precos[chave] = float(v)
        if precos and len(precos) < 4:
            raise ErroDeNegocio("Preencha os quatro preços da mesma tabela, ou deixe os quatro em branco.", 400)
        if precos and precos["ponta"] <= precos["fora"]:
            raise ErroDeNegocio("Na Tarifa Branca, a ponta é mais cara que fora de ponta. Confira se os preços não trocaram de lugar.", 400)
        conn = self.p.db()
        with conn:
            conn.execute("UPDATE usuarios SET ponta_inicio_min = ?, tb_convencional = ?, tb_fora = ?, tb_intermediario = ?, "
                         "tb_ponta = ? WHERE id = ?",
                         (inicio, precos.get("convencional"), precos.get("fora"), precos.get("intermediario"),
                          precos.get("ponta"), sessao["usuario_id"]))
            self.p.auditar(conn, sessao["usuario_id"], "tarifa_branca", None, ip)
        return self.tarifa_branca(sessao)

    # ------------------------------------------------------------ Tarifa Branca: vale a pena?

    def _dias_medidos(self, conn, ids, desde, ate):
        """Meia hora a meia hora, o que a casa gastou em cada dia inteiro do período (só dias bem medidos)."""
        zona = self.p.cfg.zona
        marcadores = ",".join("?" * len(ids))
        dias = {}                    # data -> [wh por meia hora (48)], [{medidor: intervalos} por meia hora]
        for r in conn.execute(f"SELECT medidor_id, inicio, n, wh FROM consumo_5min WHERE medidor_id IN ({marcadores}) "
                              f"AND inicio >= ? AND inicio < ?", (*ids, int(desde), int(ate))):
            local = datetime.fromtimestamp(r["inicio"], zona)
            wh, cobertura = dias.setdefault(local.date(), ([0.0] * 48, [dict() for _ in range(48)]))
            meia = local.hour * 2 + local.minute // 30
            wh[meia] += r["wh"] or 0.0
            if r["n"]:
                cobertura[meia][r["medidor_id"]] = cobertura[meia].get(r["medidor_id"], 0) + 1
        bons = {}
        for dia, (wh, cobertura) in dias.items():
            medidas = [max(c.values(), default=0) >= INTERVALOS_MEIA_HORA for c in cobertura]
            if sum(medidas) >= COBERTURA_DIA * 48:
                bons[dia] = [w / 1000 if ok else None for w, ok in zip(wh, medidas)]
        return bons

    @staticmethod
    def _media(dias):
        """O dia típico: a média de cada meia hora nos dias em que ela foi medida."""
        if not dias:
            return None
        saida = []
        for meia in range(48):
            valores = [d[meia] for d in dias if d[meia] is not None]
            saida.append(sum(valores) / len(valores) if valores else None)
        return saida

    def _aparelhos_na_ponta(self, conn, uid, ids, desde, ate, inicio, uteis_medidos, dias_uteis_mes):
        if not uteis_medidos or not self.p.consentimento(conn, uid, "aparelhos"):
            return []
        from .detetive import palpite
        zona = self.p.cfg.zona
        marcadores = ",".join("?" * len(ids))
        por_aparelho = {}
        for c in conn.execute(f"SELECT c.inicio, c.fim, c.potencia_w, a.id, a.nome, a.potencia_w AS nominal FROM ciclos c "
                              f"JOIN aparelhos a ON a.id = c.aparelho_id WHERE c.medidor_id IN ({marcadores}) "
                              f"AND a.oculto = 0 AND c.fim > ? AND c.inicio < ?", (*ids, desde, ate)):
            dia = datetime.fromtimestamp(c["inicio"], zona).date()
            if dia not in uteis_medidos:
                continue
            base = datetime(dia.year, dia.month, dia.day, tzinfo=zona).timestamp()
            a, b = base + inicio * 60, base + (inicio + 180) * 60
            junto = max(0.0, min(c["fim"], b) - max(c["inicio"], a))
            if junto:
                item = por_aparelho.setdefault(c["id"], {"aparelho": c, "kwh": 0.0, "duracoes": []})
                item["kwh"] += c["potencia_w"] * junto / 3600 / 1000
                item["duracoes"].append((c["fim"] - c["inicio"]) / 60)
        saida = []
        for item in por_aparelho.values():
            a = item["aparelho"]
            kwh_mes = item["kwh"] / len(uteis_medidos) * dias_uteis_mes
            if kwh_mes < 1:
                continue
            potencia = f"{round(a['nominal']):,}".replace(",", ".")
            media = sum(item["duracoes"]) / len(item["duracoes"])
            saida.append({"id": a["id"], "nome": a["nome"] or f"Aparelho de {potencia} W",
                          "palpite": None if a["nome"] else palpite(a["nominal"], media), "kwh_mes": round(kwh_mes, 1)})
        return sorted(saida, key=lambda x: -x["kwh_mes"])[:APARELHOS_NA_PONTA]

    def tarifa_branca(self, sessao):
        """Quanto a casa pagaria na Tarifa Branca e na convencional, com o jeito de usar das últimas 4 semanas."""
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        exemplo = u["ponta_inicio_min"] is None
        inicio = PONTA_EXEMPLO_MIN if exemplo else u["ponta_inicio_min"]
        postos = [posto(meia * 30, inicio) for meia in range(48)]
        precos = None
        if u["tb_convencional"] is not None:
            precos = {"convencional": u["tb_convencional"], "fora": u["tb_fora"],
                      "intermediario": u["tb_intermediario"], "ponta": u["tb_ponta"]}
        cabecalho = {
            "ponta": {"inicio": _hora(inicio), "fim": _hora(inicio + 180), "exemplo": exemplo},
            "intermediario": [{"inicio": _hora(inicio - 60), "fim": _hora(inicio)},
                              {"inicio": _hora(inicio + 180), "fim": _hora((inicio + 240) % 1440)}],
            "postos": postos, "precos": precos, "tarifa_kwh": u["tarifa_kwh"],
        }
        ids = self.p.medidores.ids_da_casa(conn, uid)
        if not ids:
            return {**cabecalho, "disponivel": False, "motivo": "Nenhum medidor pareado.", "dias_medidos": 0}
        ate, desde = self.p.inicio_do_dia(agora), self.p.inicio_do_dia(agora, DIAS_DO_PERFIL)
        dias = self._dias_medidos(conn, ids, desde, ate)
        uteis = {d: v for d, v in dias.items() if tem_ponta(d)}
        outros = {d: v for d, v in dias.items() if not tem_ponta(d)}
        if len(dias) < MINIMO_DIAS or len(uteis) < MINIMO_DIAS_UTEIS:
            return {**cabecalho, "disponivel": False, "dias_medidos": len(dias),
                    "motivo": (f"Precisa de pelo menos {MINIMO_DIAS} dias inteiros medidos, {MINIMO_DIAS_UTEIS} deles dias úteis. "
                               f"Até agora: {len(dias)}.")}

        perfil_util = self._media(list(uteis.values()))
        perfil_outros = self._media(list(outros.values()))
        # Meia hora sem nenhuma medição num tipo de dia: usa a do outro tipo (ou zero). É raro com dias bons.
        for meia in range(48):
            if perfil_util[meia] is None:
                perfil_util[meia] = (perfil_outros or [None] * 48)[meia] or 0.0
        if perfil_outros is None:
            perfil_outros = list(perfil_util)              # sem fim de semana medido: o dia útil faz as vezes
        perfil_outros = [v if v is not None else perfil_util[m] for m, v in enumerate(perfil_outros)]

        hoje = datetime.fromtimestamp(agora, self.p.cfg.zona).date()
        dias_uteis_mes, outros_dias_mes = dias_por_mes(hoje)
        kwh = {"fora": 0.0, "intermediario": 0.0, "ponta": 0.0}
        for meia in range(48):
            kwh[postos[meia]] += perfil_util[meia] * dias_uteis_mes
        kwh["fora"] += sum(perfil_outros) * outros_dias_mes
        total = sum(kwh.values())
        resultado = {
            **cabecalho, "disponivel": True,
            "dias_medidos": len(dias), "dias": {"uteis": len(uteis), "outros": len(outros)},
            "perfil": {"dia_util": [_n(v, 4) for v in perfil_util],
                       "fim_de_semana": [_n(v, 4) for v in perfil_outros] if outros else None},
            "mes": {"dias_uteis": round(dias_uteis_mes, 1), "outros_dias": round(outros_dias_mes, 1),
                    "kwh": {k: round(v, 1) for k, v in {**kwh, "total": total}.items()},
                    "percentual": {k: round(v / total, 3) if total else 0.0 for k, v in kwh.items()}},
            "custo": None, "empate": None, "fator": None,
            "aparelhos_na_ponta": self._aparelhos_na_ponta(conn, uid, ids, desde, ate, inicio, set(uteis), dias_uteis_mes),
        }
        minimo = MINIMO_KWH.get(u["ligacao"])
        resultado["minimo"] = None if not minimo else {"kwh": minimo, "abaixo": total < minimo}
        if precos:
            # Os preços da tabela viram o que a pessoa paga de verdade (com impostos), se ela informou a tarifa.
            fator = u["tarifa_kwh"] / precos["convencional"] if u["tarifa_kwh"] else 1.0
            postos_reais = {k: kwh[k] * precos[k] * fator for k in kwh}
            convencional = total * precos["convencional"] * fator
            branca = sum(postos_reais.values())
            resultado["fator"] = round(fator, 3)
            resultado["custo"] = {"convencional": round(convencional, 2), "branca": round(branca, 2),
                                  "diferenca": round(branca - convencional, 2),
                                  "postos": {k: round(v, 2) for k, v in postos_reais.items()}}
            if branca > convencional:
                # Cada kWh que sai da ponta para fora dela barateia (ponta - fora) na branca.
                passar = (branca - convencional) / (fator * (precos["ponta"] - precos["fora"]))
                resultado["empate"] = {"kwh_mes": round(passar, 1), "kwh_por_dia_util": round(passar / dias_uteis_mes, 2),
                                       "cabe_na_ponta": passar <= kwh["ponta"]}
        return resultado


# Fontes:
# - REN ANEEL 1.000/2021 (art. 2º, XXXVIII; arts. 222, 223, 224, 290, 291): https://www.legisweb.com.br/legislacao/?id=490988
# - ANEEL, perguntas frequentes sobre a Tarifa Branca:
#   https://www.gov.br/aneel/pt-br/assuntos/tarifas/tarifa-branca/perguntas-frequentes-sobre-tarifa-branca
# - ANEEL, modalidades tarifárias (quem pode): https://www.gov.br/aneel/pt-br/assuntos/tarifas/entenda-a-tarifa/modalidades-tarifarias
# - ANEEL, bandeira amarela em agosto de 2026 (R$ 1,885 a cada 100 kWh):
#   https://www.gov.br/aneel/pt-br/assuntos/noticias/2026-defeso-eleitoral/bandeira-tarifaria-continua-amarela-em-agosto
