"""Conferir com a conta de luz (/api/contas-de-luz): medido contra faturado e a tarifa de verdade."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import conta_pronta, parear, preencher_baldes

ZONA = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 24, 12, tzinfo=ZONA).timestamp()


def em(dia):
    return datetime.fromisoformat(dia).replace(tzinfo=ZONA).timestamp()


@pytest.fixture
def ambiente(tmp_path):
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00"}


def casa(cliente, plataforma, relogio, watts=250):
    """Medidor ligado de 15/10 até agora, 250 W o tempo todo: 6 kWh por dia."""
    relogio.t = AGORA
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-casa01")
    preencher_baldes(plataforma, mid, em("2026-10-15"), AGORA, watts)
    return mid


def test_conta_que_bate(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    r = cliente.post("/api/contas-de-luz", {"inicio": "2026-10-20", "fim": "2026-11-19", "kwh": 182, "total": 189.28})
    assert r.status_code == 201, r.get_json()
    c = r.get_json()
    assert c["dias"] == 30 and c["medido_kwh"] == pytest.approx(180.0) and c["cobertura"] == 1.0
    assert c["diferenca"] == pytest.approx((180 - 182) / 182, abs=1e-4) and c["situacao"] == "bate"
    assert c["tarifa_efetiva"] == pytest.approx(1.04)
    lista = cliente.get("/api/contas-de-luz").get_json()
    assert [x["id"] for x in lista["contas"]] == [c["id"]]
    # usar a tarifa da conta
    assert cliente.post(f"/api/contas-de-luz/{c['id']}/usar-tarifa").get_json() == {"tarifa_kwh": 1.04}
    assert cliente.get("/api/conta/eu").get_json()["tarifa_kwh"] == 1.04
    atividade = [a["descricao"] for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"]]
    assert "Conta de luz guardada para conferir" in atividade
    copia = cliente.post("/api/privacidade/exportar").get_json()
    assert copia["contas_de_luz"][0]["kwh"] == 182 and "id" not in copia["contas_de_luz"][0]


def test_diferenca_grande_e_dias_sem_sinal(cliente, plataforma, relogio):
    mid = casa(cliente, plataforma, relogio)
    plataforma.db().execute("DELETE FROM consumo_5min WHERE medidor_id = ? AND inicio >= ? AND inicio < ?",
                            (mid, em("2026-11-01"), em("2026-11-07")))       # 6 dias sem sinal
    plataforma.db().commit()
    c = cliente.post("/api/contas-de-luz", {"inicio": "2026-10-20", "fim": "2026-11-19", "kwh": 240, "total": 250}).get_json()
    assert c["cobertura"] == pytest.approx(0.8) and c["medido_kwh"] == pytest.approx(144.0)
    assert c["medido_ajustado"] == pytest.approx(180.0) and c["situacao"] == "diferente"


def test_pouca_medicao(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    c = cliente.post("/api/contas-de-luz", {"inicio": "2026-09-25", "fim": "2026-10-25", "kwh": 180}).get_json()
    assert c["situacao"] == "pouca_medicao" and c["diferenca"] is None and c["tarifa_efetiva"] is None
    r = cliente.post(f"/api/contas-de-luz/{c['id']}/usar-tarifa")
    assert r.status_code == 400 and "sem o total" in r.get_json()["erro"]


@pytest.mark.parametrize("corpo, trecho", [
    ({"inicio": "2026-11-19", "fim": "2026-10-20", "kwh": 100}, "depois da leitura anterior"),
    ({"inicio": "2026-10-20", "fim": "2026-11-30", "kwh": 100}, "depois de hoje"),
    ({"inicio": "2026-08-01", "fim": "2026-11-19", "kwh": 100}, "no máximo 62"),
    ({"inicio": "20/10/2026", "fim": "2026-11-19", "kwh": 100}, "AAAA-MM-DD"),
    ({"inicio": "2026-02-30", "fim": "2026-11-19", "kwh": 100}, "não existe"),
    ({"inicio": "2026-10-20", "fim": "2026-11-19", "kwh": 0}, "consumo"),
    ({"inicio": "2026-10-20", "fim": "2026-11-19", "kwh": 180, "total": -5}, "total"),
])
def test_validacao(cliente, plataforma, relogio, corpo, trecho):
    relogio.t = AGORA
    conta_pronta(cliente, plataforma)
    r = cliente.post("/api/contas-de-luz", corpo)
    assert r.status_code == 400 and trecho in r.get_json()["erro"]


def test_periodos_que_se_cruzam_apagar_e_apagar_consumo(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    c = cliente.post("/api/contas-de-luz", {"inicio": "2026-10-20", "fim": "2026-11-19", "kwh": 180}).get_json()
    r = cliente.post("/api/contas-de-luz", {"inicio": "2026-11-10", "fim": "2026-11-20", "kwh": 60})
    assert r.status_code == 400 and "parte desse período" in r.get_json()["erro"]
    assert cliente.post("/api/contas-de-luz", {"inicio": "2026-11-19", "fim": "2026-11-23", "kwh": 24}).status_code == 201
    assert cliente.delete(f"/api/contas-de-luz/{c['id']}").status_code == 200
    assert cliente.delete(f"/api/contas-de-luz/{c['id']}").status_code == 404
    assert len(cliente.get("/api/contas-de-luz").get_json()["contas"]) == 1
    from conftest import SENHA
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    assert cliente.get("/api/contas-de-luz").get_json()["contas"] == []


def test_conta_de_outra_pessoa(cliente, novo_cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    c = cliente.post("/api/contas-de-luz", {"inicio": "2026-10-20", "fim": "2026-11-19", "kwh": 180, "total": 190}).get_json()
    outro = novo_cliente()
    conta_pronta(outro, plataforma, email="bia@exemplo.com.br")
    assert outro.delete(f"/api/contas-de-luz/{c['id']}").status_code == 404
    assert outro.post(f"/api/contas-de-luz/{c['id']}/usar-tarifa").status_code == 404
    assert outro.get("/api/contas-de-luz").get_json()["contas"] == []
