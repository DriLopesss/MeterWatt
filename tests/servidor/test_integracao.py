"""Integração com o Home Assistant: chave só de leitura e um total de energia que só cresce."""
import json

import pytest
from conftest import SENHA, conta_pronta, leitura_pzem, parear


@pytest.fixture
def ambiente(tmp_path):
    # Dois medidores sem precisar assinar: o plano não é o assunto aqui.
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00", "MW_LIMITE_MEDIDORES_GRATIS": "5"}


def estado(app_cliente, chave):
    return app_cliente.get("/api/integracao/estado", headers={"Authorization": f"Bearer {chave}"})


def test_chave_le_o_estado_e_o_total_so_cresce(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-casa01")
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": None})
    r = cliente.post("/api/integracao", {"nome": "Home Assistant da sala"})
    assert r.status_code == 201
    chave = r.get_json()["chave"]
    assert chave.startswith("mwha_") and r.get_json()["chaves"][0]["nome"] == "Home Assistant da sala"
    assert "chave" not in cliente.get("/api/integracao").get_json()["chaves"][0]       # a chave só aparece uma vez
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(600, 10_000))
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(600, 10_250))
    # Sem cookie: quem chama é o Home Assistant.
    ha = cliente.http.application.test_client()
    r = estado(ha, chave)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    e = r.get_json()
    assert e["potencia_w"] == 600 and e["energia_total_kwh"] == 0.25 and e["kwh_hoje"] == 0.25 and e["custo_hoje"] == 0.25
    assert e["online"] is True and e["medidores"] == 1
    # Minutos atrasados também somam no total.
    relogio.avancar(120)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(
        {"tipo": "atrasadas", "uptime_agora": 200, "minutos": [{"u": 150, "n": 60, "w": 600, "min": 600, "max": 600, "e": [10_260]}]}).encode())
    assert estado(ha, chave).get_json()["energia_total_kwh"] == 0.26
    # A retenção apaga o consumo antigo, mas o total não diminui.
    plataforma.db().execute("DELETE FROM consumo_5min")
    plataforma.db().commit()
    assert estado(ha, chave).get_json()["energia_total_kwh"] == 0.26


def test_circuito_nao_soma_no_total(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    casa = parear(cliente, plataforma, "mw-casa01")
    cozinha = parear(cliente, plataforma, "mw-coz001")
    assert cliente.patch(f"/api/medidores/{cozinha}", {"papel": "circuito"}).status_code == 200
    for mid in (casa, cozinha):
        plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(300, 1000))
    relogio.avancar(1)
    for mid in (casa, cozinha):
        plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(300, 1100))
    chave = cliente.post("/api/integracao", {"nome": "HA"}).get_json()["chave"]
    e = estado(cliente.http.application.test_client(), chave).get_json()
    assert e["energia_total_kwh"] == 0.1 and e["potencia_w"] == 300 and e["medidores"] == 1


def test_chave_errada_cancelada_e_limites(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    ha = cliente.http.application.test_client()
    for cab in (None, "Bearer", "Bearer mwha_curta", "Basic abc", "Bearer mwha_" + "x" * 43):
        r = ha.get("/api/integracao/estado", headers={"Authorization": cab} if cab else {})
        assert r.status_code == 401 and r.get_json()["codigo"] == "chave_invalida"
    for nome in ("", " ", "x" * 41, None):
        assert cliente.post("/api/integracao", {"nome": nome}).status_code == 400
    chaves = [cliente.post("/api/integracao", {"nome": f"HA {i}"}).get_json() for i in range(3)]
    assert cliente.post("/api/integracao", {"nome": "HA 4"}).status_code == 400
    assert estado(ha, chaves[0]["chave"]).status_code == 200
    assert cliente.delete(f"/api/integracao/{chaves[0]['id']}").status_code == 200
    assert estado(ha, chaves[0]["chave"]).status_code == 401
    assert estado(ha, chaves[1]["chave"]).status_code == 200
    # A chave não serve para mais nada da conta.
    assert ha.get("/api/conta/eu", headers={"Authorization": f"Bearer {chaves[1]['chave']}"}).status_code == 401
    copia = cliente.post("/api/privacidade/exportar").get_json()
    assert [c["nome"] for c in copia["chaves_de_integracao"]] == ["HA 1", "HA 2"] and "chave_hash" not in json.dumps(copia)


def test_apagar_consumo_zera_o_total(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-casa01")
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(600, 10_000))
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(600, 10_100))
    chave = cliente.post("/api/integracao", {"nome": "HA"}).get_json()["chave"]
    ha = cliente.http.application.test_client()
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    assert estado(ha, chave).get_json()["energia_total_kwh"] == 0
