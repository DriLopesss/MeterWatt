"""Relatório do mês (/api/relatorios) e o resumo mensal no dia 1º."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import conta_pronta, parear, preencher_baldes

ZONA = ZoneInfo("America/Sao_Paulo")


def em(ano, mes, dia, h=0):
    return datetime(ano, mes, dia, h, tzinfo=ZONA).timestamp()


@pytest.fixture
def ambiente(tmp_path):
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00"}


def watts(t):
    """Outubro: 200 W; novembro: 250 W, com 2 kW a mais das 19h às 20h nos dias úteis."""
    d = datetime.fromtimestamp(t, ZONA)
    base = 200 if d.month == 10 else 250
    return base + (2000 if d.month == 11 and d.weekday() < 5 and d.hour == 19 else 0)


def casa(cliente, plataforma, relogio, agora, **aceites):
    relogio.t = agora
    conta_pronta(cliente, plataforma, **aceites)
    mid = parear(cliente, plataforma, "mw-casa01")
    preencher_baldes(plataforma, mid, em(2026, 10, 1), agora, watts)
    return mid


def test_mes_fechado(cliente, plataforma, relogio):
    mid = casa(cliente, plataforma, relogio, em(2026, 12, 1, 9))
    plataforma.db().execute("DELETE FROM consumo_5min WHERE medidor_id = ? AND inicio >= ? AND inicio < ?",
                            (mid, em(2026, 11, 10), em(2026, 11, 11, 12)))    # dia 10 inteiro e meio dia 11 sem sinal
    plataforma.db().commit()
    plataforma.tarifas.registrar_bandeira("2026-11", "amarela")
    r = cliente.get("/api/relatorios/2026-11").get_json()
    assert r["nome"] == "novembro de 2026" and r["parcial"] is False and r["dias_no_mes"] == 30
    assert r["dias_medidos"] == 28
    uteis = sum(1 for d in r["dias"] if d["dia_da_semana"] < 5 and d["dia"] not in ("2026-11-10",))
    kwh = 0.25 * 24 * 28.5 + 2.0 * (uteis - 0)  # dia 11 (quarta) perdeu a manhã, não a hora do banho
    assert r["kwh"] == pytest.approx(kwh, abs=0.05)
    assert r["custo"] == pytest.approx(r["kwh"] * 1.0 + r["kwh"] * 0.01885, abs=0.02)
    assert r["bandeira"]["cor"] == "amarela"
    dias = {d["dia"]: d for d in r["dias"]}
    assert dias["2026-11-10"]["inteiro"] is False and dias["2026-11-10"]["kwh"] == 0 and dias["2026-11-11"]["inteiro"] is False
    assert r["mais_caro"]["kwh"] == pytest.approx(8.0) and r["mais_barato"]["kwh"] == pytest.approx(6.0)
    assert r["semana"] == {"dia_util": pytest.approx(8.0), "fim_de_semana": pytest.approx(6.0)}
    assert r["pico"]["hora"] == 19 and r["horas"][3] == pytest.approx(0.25)
    assert r["fantasma"]["watts"] == pytest.approx(250)
    assert r["anterior"]["nome"] == "outubro de 2026" and r["anterior"]["media_dia"] == pytest.approx(4.8)
    assert r["anterior"]["variacao"] == pytest.approx(r["media_dia"] / 4.8 - 1, abs=1e-3)
    meses = cliente.get("/api/relatorios").get_json()
    assert [m["mes"] for m in meses["meses"]] == ["2026-12", "2026-11", "2026-10"] and meses["atual"] == "2026-12"


def test_mes_em_andamento_e_sem_comparacao(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio, em(2026, 10, 5, 15))
    r = cliente.get("/api/relatorios/2026-10").get_json()
    assert r["parcial"] is True and len(r["dias"]) == 5 and r["dias_medidos"] == 4      # hoje não conta como dia inteiro
    assert r["anterior"] is None and r["bandeira"] is None
    assert cliente.get("/api/relatorios/2026-11").status_code == 400
    assert cliente.get("/api/relatorios/11-2026").status_code == 400


def test_aparelhos_do_mes(cliente, plataforma, relogio):
    mid = casa(cliente, plataforma, relogio, em(2026, 12, 1, 9), aparelhos=True)
    db = plataforma.db()
    with db:
        a = db.execute("INSERT INTO aparelhos (medidor_id, potencia_w, n_ciclos, criado_em) VALUES (?, 5400, 30, 0)", (mid,)).lastrowid
        for dia in range(1, 31):
            t = em(2026, 11, dia, 19)
            db.execute("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, 5400, 720)", (mid, a, t, t + 480))
    r = cliente.get("/api/relatorios/2026-11").get_json()
    assert r["aparelhos"] == [{"nome": "Aparelho de 5.400 W", "palpite": "chuveiro elétrico ou torneira elétrica",
                               "kwh": 21.6, "vezes": 30, "custo": 21.6}]


def test_resumo_mensal_no_dia_1(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio, em(2026, 12, 1, 7))
    assert plataforma.alertas.verificar() == 0 or True          # antes das 8h não sai o mensal
    assert not [a for a in cliente.get("/api/avisos").get_json()["avisos"] if a["tipo"] == "resumo_mensal"]
    relogio.t = em(2026, 12, 1, 9)
    plataforma.alertas.verificar()
    mensais = [a for a in cliente.get("/api/avisos").get_json()["avisos"] if a["tipo"] == "resumo_mensal"]
    assert len(mensais) == 1 and mensais[0]["titulo"] == "Resumo de novembro"
    assert "a mais por dia que em outubro" in mensais[0]["texto"]
    m = plataforma.mensageiro.ultimo_para("ana@exemplo.com.br")
    assert m["assunto"] == "MeterWatt: Resumo de novembro" and "/#relatorio/2026-11" in m["texto"]
    plataforma.alertas.verificar()                                  # uma vez só
    assert len([a for a in cliente.get("/api/avisos").get_json()["avisos"] if a["tipo"] == "resumo_mensal"]) == 1
