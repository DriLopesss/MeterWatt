"""Conferir com a conta de luz: o que a distribuidora cobrou contra o que o MeterWatt mediu no mesmo período.

A pessoa digita da conta: o começo e o fim do período de leitura, os kWh faturados e o total em reais. Daqui sai:
- a diferença entre o medido e o faturado (confere o medidor e a instalação: um circuito fora do medidor, uma
  calibração errada, dias sem sinal);
- a tarifa de verdade, total ÷ kWh, que já inclui impostos, bandeira e o que mais vier na conta (iluminação
  pública, por exemplo). A pessoa pode passar a usá-la nas contas do app.

O medido é guardado junto da conta no momento em que ela entra: o consumo de 5 em 5 minutos some com a retenção
(90 dias), e a conferência não pode sumir junto. Datas no fuso do servidor; o período vai da 0h do primeiro dia até
a 0h do dia da leitura atual (a leitura da distribuidora acontece em alguma hora desse dia: alguns kWh de
diferença vêm daí).
"""
import re
from datetime import date, datetime, time as hora

from .erros import ErroDeNegocio, NaoEncontrado

BATE = 0.03                   # até 3% de diferença: o medidor confere com a distribuidora
PERTO = 0.10
COBERTURA_MINIMA = 0.5        # com menos da metade do período medido, não dá para comparar
MAXIMO_CONTAS = 36
DIAS_MAXIMO = 62


def _data(texto, nome):
    if not isinstance(texto, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        raise ErroDeNegocio(f"A data de {nome} vai no formato AAAA-MM-DD.", 400)
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ErroDeNegocio(f"A data de {nome} não existe no calendário.", 400) from None


def _numero(v, nome, maximo):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= maximo:
        raise ErroDeNegocio(f"{nome} precisa ser um número maior que 0.", 400)
    return float(v)


class ContasDeLuz:
    def __init__(self, plataforma):
        self.p = plataforma

    def _instante(self, dia):
        return datetime.combine(dia, hora(0), tzinfo=self.p.cfg.zona).timestamp()

    def _medido(self, conn, uid, inicio, fim):
        """kWh das entradas no período e a fração dos intervalos de 5 minutos que foram medidos."""
        ids = self.p.medidores.ids_da_casa(conn, uid)
        if not ids:
            return 0.0, 0.0, False
        a, b = self._instante(inicio), self._instante(fim)
        marcadores = ",".join("?" * len(ids))
        wh, baldes = conn.execute(f"SELECT SUM(wh), COUNT(DISTINCT inicio) FROM consumo_5min WHERE medidor_id IN ({marcadores}) "
                                  f"AND inicio >= ? AND inicio < ? AND n > 0", (*ids, a, b)).fetchone()
        estimado = any(r["tipo"] == "corrente" for r in conn.execute(
            f"SELECT tipo FROM medidores WHERE id IN ({marcadores})", ids))
        return (wh or 0.0) / 1000, min(1.0, (baldes or 0) / ((b - a) / 300)), estimado

    def _resumo(self, r):
        dias = (date.fromisoformat(r["fim"]) - date.fromisoformat(r["inicio"])).days
        saida = {"id": r["id"], "inicio": r["inicio"], "fim": r["fim"], "dias": dias, "kwh": r["kwh"], "total": r["total_reais"],
                 "tarifa_efetiva": round(r["total_reais"] / r["kwh"], 4) if r["total_reais"] else None,
                 "medido_kwh": round(r["medido_kwh"], 1), "cobertura": round(r["cobertura"], 3), "estimado": bool(r["estimado"]),
                 "criada_em": self.p.iso(r["criada_em"])}
        if r["cobertura"] < COBERTURA_MINIMA:
            saida.update(situacao="pouca_medicao", medido_ajustado=None, diferenca=None)
            return saida
        # Dias sem sinal: completa o período com a média do que foi medido (e diz isso na tela).
        ajustado = r["medido_kwh"] / r["cobertura"]
        diferenca = (ajustado - r["kwh"]) / r["kwh"]
        saida.update(medido_ajustado=round(ajustado, 1), diferenca=round(diferenca, 4),
                     situacao="bate" if abs(diferenca) <= BATE else "perto" if abs(diferenca) <= PERTO else "diferente")
        return saida

    def listar(self, sessao):
        conn = self.p.db()
        linhas = conn.execute("SELECT * FROM contas_luz WHERE usuario_id = ? ORDER BY fim DESC", (sessao["usuario_id"],)).fetchall()
        u = conn.execute("SELECT tarifa_kwh FROM usuarios WHERE id = ?", (sessao["usuario_id"],)).fetchone()
        return {"contas": [self._resumo(r) for r in linhas], "tarifa_kwh": u["tarifa_kwh"],
                "limites": {"bate": BATE, "perto": PERTO}}

    def adicionar(self, sessao, dados, ip=None):
        if not isinstance(dados, dict):
            raise ErroDeNegocio("Pedido inválido.", 400)
        inicio, fim = _data(dados.get("inicio"), "início"), _data(dados.get("fim"), "fim")
        hoje = datetime.fromtimestamp(self.p.agora(), self.p.cfg.zona).date()
        if not inicio < fim:
            raise ErroDeNegocio("A leitura atual vem depois da leitura anterior.", 400)
        if fim > hoje:
            raise ErroDeNegocio("A leitura atual não pode ser depois de hoje.", 400)
        if (fim - inicio).days > DIAS_MAXIMO:
            raise ErroDeNegocio(f"Uma conta cobre no máximo {DIAS_MAXIMO} dias. Confira as datas de leitura.", 400)
        kwh = _numero(dados.get("kwh"), "O consumo (kWh)", 100000)
        total = None if dados.get("total") is None else _numero(dados.get("total"), "O total da conta (R$)", 1000000)
        conn = self.p.db()
        uid = sessao["usuario_id"]
        if conn.execute("SELECT COUNT(*) FROM contas_luz WHERE usuario_id = ?", (uid,)).fetchone()[0] >= MAXIMO_CONTAS:
            raise ErroDeNegocio(f"Já há {MAXIMO_CONTAS} contas guardadas. Apague uma antiga para pôr esta.", 400)
        if conn.execute("SELECT 1 FROM contas_luz WHERE usuario_id = ? AND inicio < ? AND fim > ?",
                        (uid, fim.isoformat(), inicio.isoformat())).fetchone():
            raise ErroDeNegocio("Já há uma conta com parte desse período. Apague a outra se ela estava errada.", 400)
        medido, cobertura, estimado = self._medido(conn, uid, inicio, fim)
        with conn:
            nova = conn.execute("INSERT INTO contas_luz (usuario_id, inicio, fim, kwh, total_reais, medido_kwh, cobertura, estimado, criada_em) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (uid, inicio.isoformat(), fim.isoformat(), kwh, total, medido, cobertura, int(estimado), self.p.agora())).lastrowid
            self.p.auditar(conn, uid, "conta_luz", f"{inicio.isoformat()} a {fim.isoformat()}", ip)
        return self._resumo(conn.execute("SELECT * FROM contas_luz WHERE id = ?", (nova,)).fetchone())

    def _da_pessoa(self, conn, uid, cid):
        r = conn.execute("SELECT * FROM contas_luz WHERE id = ? AND usuario_id = ?", (cid, uid)).fetchone()
        if not r:
            raise NaoEncontrado("Conta de luz não encontrada.")
        return r

    def apagar(self, sessao, cid, ip=None):
        conn = self.p.db()
        r = self._da_pessoa(conn, sessao["usuario_id"], cid)
        with conn:
            conn.execute("DELETE FROM contas_luz WHERE id = ?", (cid,))
            self.p.auditar(conn, sessao["usuario_id"], "conta_luz_apagada", f"{r['inicio']} a {r['fim']}", ip)

    def usar_tarifa(self, sessao, cid, ip=None):
        """A tarifa de verdade da conta (total ÷ kWh) passa a ser a tarifa do app."""
        conn = self.p.db()
        r = self._da_pessoa(conn, sessao["usuario_id"], cid)
        if not r["total_reais"]:
            raise ErroDeNegocio("Essa conta foi guardada sem o total em reais.", 400)
        tarifa = round(r["total_reais"] / r["kwh"], 4)
        if not 0 < tarifa <= 10:
            raise ErroDeNegocio("O total dividido pelos kWh dá uma tarifa fora do comum. Confira os números da conta.", 400)
        with conn:
            conn.execute("UPDATE usuarios SET tarifa_kwh = ? WHERE id = ?", (tarifa, sessao["usuario_id"]))
            self.p.auditar(conn, sessao["usuario_id"], "preferencias", f"tarifa da conta de {r['fim']}", ip)
        return {"tarifa_kwh": tarifa}

    def exportar(self, conn, uid):
        return [{k: v for k, v in self._resumo(r).items() if k != "id"}
                for r in conn.execute("SELECT * FROM contas_luz WHERE usuario_id = ? ORDER BY fim", (uid,))]

    def apagar_tudo(self, conn, uid):
        conn.execute("DELETE FROM contas_luz WHERE usuario_id = ?", (uid,))
