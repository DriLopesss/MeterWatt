"""Qualidade da energia: a tensão que chega na casa e as faltas de energia, com data e hora.

Serve para conversar com a distribuidora: pedir a verificação do nível de tensão ou o ressarcimento de um
aparelho queimado (ela pede a data e o horário prováveis do dano; o prazo para pedir é de 5 anos, REN ANEEL
1.000/2021). O MeterWatt NÃO é o medidor oficial: o registro dele ajuda a dar datas, horários e números, e
quem mede para valer é a distribuidora.

- Tensão: média de 10 em 10 minutos, como os registros da ANEEL, em cada fase. Cada média cai numa faixa
  (adequada, precária ou crítica) da tabela da ANEEL para a tensão nominal da casa, 127 V ou 220 V entre fase
  e neutro, descoberta pela mediana do que foi medido. Um intervalo só conta com pelo menos metade das
  leituras (300 de 600). Os números de "quanto tempo em cada faixa" são nossos, não os indicadores oficiais
  DRP e DRC (que a distribuidora calcula com 1.008 leituras do seu próprio aparelho).
- O PZEM dá uma leitura por segundo: afundamentos rápidos (de menos de um segundo) não aparecem.
- Falta de energia: o medidor se alimenta da rede da casa. Se ele some e volta reiniciado (o tempo ligado
  recomeçou), houve uma falta de energia, ou alguém tirou o medidor da tomada, entre a última leitura e a
  hora em que ele voltou a ligar. Só conta a partir de 3 minutos, como os indicadores da ANEEL. Uma queda
  só do Wi-Fi não reinicia o medidor e não vira falta de energia.
- Falta de fase: numa casa com duas ou três fases, uma fase que ficou sem resposta e voltou.

Faixas: PRODIST, Módulo 8, para 220/127 V (adequada 117 a 133 V e 202 a 231 V; precária 110 a 116 / 134 a 135 V
e 191 a 201 / 232 a 233 V; crítica fora disso).
"""
import statistics
from datetime import datetime

JANELA_S = 600
LEITURAS_MINIMAS = 300
FALTA_MINIMA_S = 180
# nominal: (adequada, precária) em volts, entre fase e neutro. Fora da precária é crítica.
FAIXAS = {
    127: {"adequada": (117, 133), "precaria": (110, 135)},
    220: {"adequada": (202, 231), "precaria": (191, 233)},
}
NOMES = {"adequada": "adequada", "precaria": "precária", "critica": "crítica"}


def nominal_de(tensoes):
    """127 ou 220, pela mediana das médias de 10 minutos."""
    return 220 if statistics.median(tensoes) >= 175 else 127


def faixa(v, nominal):
    f = FAIXAS[nominal]
    if f["adequada"][0] <= v <= f["adequada"][1]:
        return "adequada"
    if f["precaria"][0] <= v <= f["precaria"][1]:
        return "precaria"
    return "critica"


class Qualidade:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ gravar (chamado a cada leitura)

    def registrar_tensao(self, conn, mid, agora, tensoes):
        """`tensoes`: {fase: volts} desta leitura."""
        inicio = int(agora // JANELA_S * JANELA_S)
        for fase, v in tensoes.items():
            if v is None or v < 50:
                continue
            conn.execute(
                "INSERT INTO tensao_10min (medidor_id, fase, inicio, n, soma_v, min_v, max_v) VALUES (?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT (medidor_id, fase, inicio) DO UPDATE SET n = n + 1, soma_v = soma_v + excluded.soma_v, "
                "min_v = MIN(min_v, excluded.min_v), max_v = MAX(max_v, excluded.max_v)",
                (mid, fase, inicio, v, v, v))

    def evento(self, conn, mid, tipo, inicio, fim, fase=None, simulado=False):
        conn.execute("INSERT INTO eventos_energia (medidor_id, tipo, fase, inicio, fim, simulado) VALUES (?, ?, ?, ?, ?, ?)",
                     (mid, tipo, fase, inicio, fim, 1 if simulado else 0))

    def ao_ler(self, conn, m, agora, uptime, fases_antes, fases_agora, simulado):
        """Faltas de energia (o medidor voltou reiniciado) e de fase (uma fase voltou a responder)."""
        if uptime is not None and m["uptime_s"] is not None and uptime + 5 < m["uptime_s"] and m["ultima_gravacao"]:
            ligou = agora - uptime
            if ligou - m["ultima_gravacao"] >= FALTA_MINIMA_S:
                self.evento(conn, m["id"], "falta_energia", m["ultima_gravacao"], ligou, simulado=simulado)
        if fases_antes and fases_agora:
            agora_ok = {f["fase"]: f["ok"] for f in fases_agora}
            for f in fases_antes:
                if not f.get("ok") and agora_ok.get(f["fase"]) and agora - f.get("desde", agora) >= FALTA_MINIMA_S:
                    self.evento(conn, m["id"], "falta_fase", f["desde"], agora, fase=f["fase"], simulado=simulado)

    # ------------------------------------------------------------ ler

    def _janelas(self, conn, ids, desde):
        if not ids:
            return []
        marcadores = ",".join("?" * len(ids))
        return conn.execute(f"SELECT medidor_id, fase, inicio, n, soma_v, min_v, max_v FROM tensao_10min WHERE medidor_id IN ({marcadores}) "
                            f"AND inicio >= ? AND n >= ? ORDER BY medidor_id, fase, inicio", (*ids, desde, LEITURAS_MINIMAS)).fetchall()

    def resumo(self, sessao, dias=30):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        dias = max(1, min(90, int(dias)))
        agora = self.p.agora()
        desde = agora - dias * 86400
        zona = self.p.cfg.zona
        medidores = conn.execute("SELECT id, apelido FROM medidores WHERE usuario_id = ? AND tipo = 'pzem' ORDER BY pareado_em",
                                 (uid,)).fetchall()
        nomes = {m["id"]: m["apelido"] for m in medidores}
        por_fase = {}
        for j in self._janelas(conn, list(nomes), desde):
            por_fase.setdefault((j["medidor_id"], j["fase"]), []).append(j)
        saida_medidores, eventos = [], []
        for (mid, fase), janelas in por_fase.items():
            medias = [j["soma_v"] / j["n"] for j in janelas]
            nominal = nominal_de(medias)
            contagem = {"adequada": 0, "precaria": 0, "critica": 0}
            por_dia = {}
            episodio = None
            for j, v in zip(janelas, medias):
                f = faixa(v, nominal)
                contagem[f] += 1
                dia = datetime.fromtimestamp(j["inicio"], zona).date().isoformat()
                d = por_dia.setdefault(dia, {"dia": dia, "min": j["min_v"], "max": j["max_v"], "soma": 0.0, "n": 0})
                d.update(min=min(d["min"], j["min_v"]), max=max(d["max"], j["max_v"]), soma=d["soma"] + v, n=d["n"] + 1)
                # Intervalos críticos seguidos viram um episódio só.
                if f == "critica":
                    if episodio and episodio["fim"] == j["inicio"]:
                        episodio.update(fim=j["inicio"] + JANELA_S, min_v=min(episodio["min_v"], v), max_v=max(episodio["max_v"], v))
                    else:
                        episodio = {"tipo": "tensao_critica", "medidor": nomes[mid], "medidor_id": mid, "fase": fase,
                                    "inicio": j["inicio"], "fim": j["inicio"] + JANELA_S, "min_v": v, "max_v": v, "nominal": nominal}
                        eventos.append(episodio)
            total = sum(contagem.values())
            menor = min(janelas, key=lambda j: j["min_v"])
            maior = max(janelas, key=lambda j: j["max_v"])
            saida_medidores.append({
                "id": mid, "apelido": nomes[mid], "fase": fase, "nominal": nominal, "intervalos": total,
                "faixas": {k: list(v) for k, v in FAIXAS[nominal].items()},
                "fracao": {k: round(v / total, 4) for k, v in contagem.items()},
                "menor": {"v": round(menor["min_v"], 1), "quando": self.p.iso(menor["inicio"])},
                "maior": {"v": round(maior["max_v"], 1), "quando": self.p.iso(maior["inicio"])},
                "dias": [{"dia": d["dia"], "min": round(d["min"], 1), "media": round(d["soma"] / d["n"], 1), "max": round(d["max"], 1)}
                         for d in por_dia.values()],
            })
        for e in conn.execute("SELECT e.*, m.apelido FROM eventos_energia e JOIN medidores m ON m.id = e.medidor_id "
                              "WHERE m.usuario_id = ? AND e.fim >= ? ORDER BY e.inicio", (uid, desde)):
            eventos.append({"tipo": e["tipo"], "medidor": e["apelido"], "medidor_id": e["medidor_id"], "fase": e["fase"],
                            "inicio": e["inicio"], "fim": e["fim"], "simulado": bool(e["simulado"])})
        for e in eventos:
            e["duracao_min"] = round((e["fim"] - e["inicio"]) / 60)
            for k in ("min_v", "max_v"):
                if k in e:
                    e[k] = round(e[k], 1)
        eventos.sort(key=lambda e: e["inicio"], reverse=True)
        for e in eventos:
            e["inicio"], e["fim"] = self.p.iso(e["inicio"]), self.p.iso(e["fim"])
        saida_medidores.sort(key=lambda x: (list(nomes).index(x["id"]), x["fase"]))
        return {"dias": dias, "disponivel": bool(saida_medidores), "medidores": saida_medidores, "eventos": eventos,
                "leituras_minimas": LEITURAS_MINIMAS, "falta_minima_min": FALTA_MINIMA_S // 60}

    # ------------------------------------------------------------ LGPD

    def exportar(self, conn, mid):
        return {
            "tensao_10_minutos": [{"inicio": self.p.iso(r["inicio"]), "fase": r["fase"], "leituras": r["n"],
                                   "tensao_media": round(r["soma_v"] / r["n"], 1), "tensao_minima": r["min_v"], "tensao_maxima": r["max_v"]}
                                  for r in conn.execute("SELECT * FROM tensao_10min WHERE medidor_id = ? ORDER BY inicio, fase", (mid,))],
            "eventos_de_energia": [{"tipo": r["tipo"], "fase": r["fase"], "inicio": self.p.iso(r["inicio"]), "fim": self.p.iso(r["fim"]),
                                    "simulado": bool(r["simulado"])}
                                   for r in conn.execute("SELECT * FROM eventos_energia WHERE medidor_id = ? ORDER BY inicio", (mid,))],
        }

    def apagar(self, conn, mid):
        conn.execute("DELETE FROM tensao_10min WHERE medidor_id = ?", (mid,))
        conn.execute("DELETE FROM eventos_energia WHERE medidor_id = ?", (mid,))
