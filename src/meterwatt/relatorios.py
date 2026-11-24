"""Relatório do mês: o que a casa gastou num mês, dia a dia, comparado com o mês anterior.

Tudo sai do consumo de 5 em 5 minutos das entradas (os circuitos não somam de novo). Limites honestos:
- Um dia "medido" tem pelo menos 80% dos intervalos de 5 minutos. As médias (por dia, por dia da semana, por hora)
  usam só esses dias; o total do mês soma tudo o que foi medido e diz quantos dias faltaram.
- A comparação com o mês anterior é pela média por dia, e só quando os dois meses têm pelo menos 7 dias medidos:
  meio mês contra um mês inteiro daria um número sem sentido.
- O custo é a energia vezes a tarifa informada, mais a bandeira do mês (sem os impostos sobre ela). Não é a conta:
  ela tem outro período de leitura, iluminação pública e impostos do mês.
- A retenção apaga o consumo de 5 em 5 minutos depois de 90 dias (730 com o histórico longo): meses mais antigos
  aparecem vazios.
"""
import calendar
import re
import statistics
from datetime import date, datetime, time as hora, timedelta

from .erros import ErroDeNegocio

COBERTURA_DIA = 0.8
DIAS_PARA_COMPARAR = 7
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def nome_do_mes(ano, mes):
    return f"{MESES[mes - 1]} de {ano}"


class Relatorios:
    def __init__(self, plataforma):
        self.p = plataforma

    def _meia_noite(self, dia):
        return datetime.combine(dia, hora(0), tzinfo=self.p.cfg.zona).timestamp()

    def _dias(self, conn, ids, primeiro, ultimo):
        """Por dia local: kWh, cobertura (0 a 1) e kWh por hora. `ultimo` exclusivo."""
        zona = self.p.cfg.zona
        saida = {}
        d = primeiro
        while d < ultimo:
            saida[d] = {"wh": 0.0, "baldes": set(), "horas": [0.0] * 24}
            d += timedelta(days=1)
        if not ids:
            return saida
        marcadores = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT inicio, n, wh FROM consumo_5min WHERE medidor_id IN ({marcadores}) AND inicio >= ? AND inicio < ?",
                              (*ids, self._meia_noite(primeiro), self._meia_noite(ultimo))):
            local = datetime.fromtimestamp(r["inicio"], zona)
            item = saida.get(local.date())
            if item is None:
                continue
            item["wh"] += r["wh"] or 0.0
            item["horas"][local.hour] += (r["wh"] or 0.0)
            if r["n"]:
                item["baldes"].add(r["inicio"])
        for d, item in saida.items():
            inteiro = (self._meia_noite(d + timedelta(days=1)) - self._meia_noite(d)) / 300
            item["cobertura"] = min(1.0, len(item["baldes"]) / inteiro)
        return saida

    def disponiveis(self, sessao):
        """Os meses que têm alguma medição, do mais novo para o mais antigo (para escolher na tela)."""
        conn = self.p.db()
        ids = self.p.medidores.ids_da_casa(conn, sessao["usuario_id"])
        hoje = datetime.fromtimestamp(self.p.agora(), self.p.cfg.zona).date()
        if not ids:
            return {"meses": [], "atual": hoje.strftime("%Y-%m")}
        marcadores = ",".join("?" * len(ids))
        primeiro = conn.execute(f"SELECT MIN(inicio) FROM consumo_5min WHERE medidor_id IN ({marcadores})", ids).fetchone()[0]
        meses = []
        if primeiro is not None:
            d = datetime.fromtimestamp(primeiro, self.p.cfg.zona).date().replace(day=1)
            while d <= hoje:
                meses.append({"mes": d.strftime("%Y-%m"), "nome": nome_do_mes(d.year, d.month)})
                d = (d + timedelta(days=32)).replace(day=1)
        return {"meses": meses[::-1], "atual": hoje.strftime("%Y-%m")}

    def mes(self, sessao, mes):
        if not isinstance(mes, str) or not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", mes):
            raise ErroDeNegocio("O mês vai no formato AAAA-MM, como 2026-11.", 400)
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        hoje = datetime.fromtimestamp(agora, self.p.cfg.zona).date()
        ano, m = map(int, mes.split("-"))
        primeiro = date(ano, m, 1)
        if primeiro > hoje:
            raise ErroDeNegocio("Esse mês ainda não começou.", 400)
        fim_do_mes = primeiro + timedelta(days=calendar.monthrange(ano, m)[1])
        ultimo = min(fim_do_mes, hoje + timedelta(days=1))
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        tarifa = self.p.tarifa_de(u)
        ids = self.p.medidores.ids_da_casa(conn, uid)
        dias = self._dias(conn, ids, primeiro, ultimo)
        bons = {d: v for d, v in dias.items() if v["cobertura"] >= COBERTURA_DIA and d != hoje}
        kwh = sum(v["wh"] for v in dias.values()) / 1000
        bandeira = self.p.tarifas.bandeira_do_mes(conn, self._meia_noite(primeiro) + 3600)
        custo = None if tarifa is None else round(kwh * tarifa + kwh * bandeira["reais_por_100_kwh"] / 100, 2)

        def kwh_dia(v):
            return round(v["wh"] / 1000, 3)
        saida = {
            "mes": mes, "nome": nome_do_mes(ano, m), "parcial": ultimo < fim_do_mes, "tarifa_kwh": tarifa,
            "dias_no_mes": (fim_do_mes - primeiro).days, "dias_medidos": len(bons), "kwh": round(kwh, 2), "custo": custo,
            "bandeira": bandeira if bandeira["informada"] else None,
            "dias": [{"dia": d.isoformat(), "dia_da_semana": d.weekday(), "kwh": kwh_dia(v),
                      "custo": None if tarifa is None else round(v["wh"] / 1000 * tarifa, 2),
                      "inteiro": d in bons} for d, v in dias.items()],
            "media_dia": None, "mais_caro": None, "mais_barato": None, "semana": None, "horas": None, "pico": None,
            "anterior": None, "fantasma": None, "aparelhos": [],
        }
        if bons:
            medias = {d: v["wh"] / 1000 for d, v in bons.items()}
            saida["media_dia"] = round(statistics.mean(medias.values()), 2)
            caro = max(medias, key=medias.get)
            barato = min(medias, key=medias.get)
            saida["mais_caro"] = {"dia": caro.isoformat(), "kwh": round(medias[caro], 2)}
            saida["mais_barato"] = {"dia": barato.isoformat(), "kwh": round(medias[barato], 2)}
            uteis = [k for d, k in medias.items() if d.weekday() < 5]
            fins = [k for d, k in medias.items() if d.weekday() >= 5]
            saida["semana"] = {"dia_util": round(statistics.mean(uteis), 2) if uteis else None,
                               "fim_de_semana": round(statistics.mean(fins), 2) if fins else None}
            horas = [round(sum(v["horas"][h] for v in bons.values()) / len(bons) / 1000, 3) for h in range(24)]
            saida["horas"] = horas
            pico = max(range(24), key=lambda h: horas[h])
            saida["pico"] = {"hora": pico, "kwh": horas[pico]}
            # Consumo em espera do mês: a mediana dos mínimos das madrugadas medidas.
            noites = [self._meia_noite(d) for d in bons]
            base = self.p.medidores.base_noturna(conn, ids, noites)
            if base is not None:
                kwh_base = base * 24 * len(bons) / 1000
                saida["fantasma"] = {"watts": round(base, 1), "kwh": round(kwh_base, 1),
                                     "fatia": round(kwh_base / sum(medias.values()), 3) if medias else None}
        # Mês anterior, pela média por dia.
        inicio_ant = (primeiro - timedelta(days=1)).replace(day=1)
        ant = self._dias(conn, ids, inicio_ant, primeiro)
        bons_ant = [v["wh"] / 1000 for v in ant.values() if v["cobertura"] >= COBERTURA_DIA]
        if len(bons_ant) >= DIAS_PARA_COMPARAR and len(bons) >= DIAS_PARA_COMPARAR:
            media_ant = statistics.mean(bons_ant)
            saida["anterior"] = {"mes": inicio_ant.strftime("%Y-%m"), "nome": nome_do_mes(inicio_ant.year, inicio_ant.month),
                                 "media_dia": round(media_ant, 2),
                                 "variacao": round(saida["media_dia"] / media_ant - 1, 4) if media_ant else None}
        # Aparelhos que mais gastaram (Detetive), só com a autorização dele.
        if ids and self.p.consentimento(conn, uid, "aparelhos"):
            from .detetive import palpite
            marcadores = ",".join("?" * len(ids))
            for a in conn.execute(f"SELECT a.id, a.nome, a.potencia_w, SUM(c.wh) AS wh, COUNT(*) AS vezes, AVG(c.fim - c.inicio) AS duracao "
                                  f"FROM ciclos c JOIN aparelhos a ON a.id = c.aparelho_id WHERE c.medidor_id IN ({marcadores}) "
                                  f"AND a.oculto = 0 AND c.inicio >= ? AND c.inicio < ? GROUP BY a.id ORDER BY wh DESC LIMIT 5",
                                  (*ids, self._meia_noite(primeiro), self._meia_noite(ultimo))):
                potencia = f"{round(a['potencia_w']):,}".replace(",", ".")
                saida["aparelhos"].append({
                    "nome": a["nome"] or f"Aparelho de {potencia} W",
                    "palpite": None if a["nome"] else palpite(a["potencia_w"], (a["duracao"] or 0) / 60),
                    "kwh": round(a["wh"] / 1000, 1), "vezes": a["vezes"],
                    "custo": None if tarifa is None else round(a["wh"] / 1000 * tarifa, 2)})
        return saida
