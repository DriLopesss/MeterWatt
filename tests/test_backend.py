"""Testes do backend. Rodar na raiz do repositório:  python -m pytest tests -q"""
import importlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("TARIFA_KWH", "1,00")
    monkeypatch.setenv("ARQUIVO_CSV", str(tmp_path / "leituras.csv"))
    monkeypatch.delenv("CORS_ORIGENS", raising=False)
    import dashboard
    modulo = importlib.reload(dashboard)
    return modulo


def pzem(**extra):
    base = {"sensor": "pzem004t-v3", "sensor_ok": True, "tensao_v": 127.3, "corrente_a": 4.321,
            "potencia_w": 548.2, "energia_wh": 1000, "frequencia_hz": 60.0, "fp": 0.98,
            "rssi": -60, "uptime_s": 10, "falhas_leitura": 0, "fw": "3.0.0"}
    base.update(extra)
    return base


T0 = datetime(2026, 11, 11, 12, 0, 0).timestamp()


def test_leitura_do_pzem(backend):
    r = backend.processar_leitura("mw-A", pzem(), instante=T0)
    assert r["tipo"] == "pzem" and r["sensor_ok"]
    assert r["tensao_v"] == 127.3 and r["potencia_w"] == 548.2 and r["fp"] == 0.98
    assert r["potencia_aparente_va"] == pytest.approx(127.3 * 4.321, abs=0.1)
    assert r["energia_fonte"] == "medidor" and r["energia_contador_kwh"] == 1.0
    assert r["kwh_hoje"] == 0          # a primeira leitura só marca o ponto de partida
    assert r["watts"] == 548.2         # campo antigo traz potência ativa no PZEM


def test_consumo_vem_do_contador_do_modulo(backend):
    backend.processar_leitura("mw-A", pzem(energia_wh=1000), instante=T0)
    backend.processar_leitura("mw-A", pzem(energia_wh=1250), instante=T0 + 5)
    r = backend.processar_leitura("mw-A", pzem(energia_wh=1500), instante=T0 + 10)
    assert r["kwh_hoje"] == pytest.approx(0.5)
    assert r["custo_hoje"] == pytest.approx(0.5)      # tarifa de R$ 1,00 no teste
    assert r["custo_por_hora"] is None or r["custo_por_hora"] >= 0


def test_contador_zerado_nao_gera_consumo_negativo(backend):
    backend.processar_leitura("mw-A", pzem(energia_wh=5000), instante=T0)
    backend.processar_leitura("mw-A", pzem(energia_wh=5100), instante=T0 + 5)
    r = backend.processar_leitura("mw-A", pzem(energia_wh=3), instante=T0 + 10)   # zerado
    assert r["kwh_hoje"] == pytest.approx(0.1)
    r = backend.processar_leitura("mw-A", pzem(energia_wh=13), instante=T0 + 15)
    assert r["kwh_hoje"] == pytest.approx(0.11)


def test_virada_do_dia_zera_o_consumo_de_hoje(backend):
    fim = datetime(2026, 11, 11, 23, 59, 50).timestamp()
    backend.processar_leitura("mw-A", pzem(energia_wh=1000), instante=fim)
    backend.processar_leitura("mw-A", pzem(energia_wh=1400), instante=fim + 5)
    r = backend.processar_leitura("mw-A", pzem(energia_wh=1410), instante=fim + 20)  # já é dia 12
    with backend.trava:
        e = backend.dispositivos["mw-A"]
        assert e["dia"] == "2026-11-12" and e["wh_hoje"] == pytest.approx(10)
    assert r["kvah_acumulado"] == pytest.approx(0.41)


def test_falha_do_sensor_apaga_medidas_antigas(backend):
    backend.processar_leitura("mw-A", pzem(), instante=T0)
    r = backend.processar_leitura(
        "mw-A", {"sensor_ok": False, "erro_sensor": "sem_resposta", "falhas_leitura": 1}, instante=T0 + 5)
    assert r["sensor_ok"] is False and r["erro_sensor"] == "sem_resposta"
    assert r["tensao_v"] is None and r["potencia_w"] is None and r["corrente_a"] is None
    assert r["energia_contador_kwh"] == 1.0   # o contador já lido continua valendo


def test_firmware_do_acs712_continua_funcionando(backend):
    v2 = {"irms_a": 2.0, "va": 254.0, "offset_mv": 1650, "rssi": -55, "uptime_s": 5}
    backend.processar_leitura("mw-B", v2, instante=T0)
    r = backend.processar_leitura("mw-B", v2, instante=T0 + 3600)   # buraco grande: não integra
    assert r["kwh_hoje"] == 0
    r = backend.processar_leitura("mw-B", v2, instante=T0 + 3630)
    assert r["tipo"] == "corrente" and r["energia_fonte"] == "estimada"
    assert r["potencia_w"] is None and r["watts"] == 254.0 and r["irms_a"] == 2.0
    assert r["kwh_hoje"] == pytest.approx(254.0 * 30 / 3600 / 1000, abs=1e-4)   # arredondado a 0,1 Wh


@pytest.mark.parametrize("ruim", [
    {"tensao_v": "127", "corrente_a": 1, "potencia_w": 1},
    {"tensao_v": float("nan"), "corrente_a": 1, "potencia_w": 1},
    {"tensao_v": 127, "corrente_a": -5, "potencia_w": 100},
    {"tensao_v": 127, "corrente_a": True, "potencia_w": 100},
    {"potencia_w": 100},
    {},
])
def test_leituras_invalidas_sao_recusadas(backend, ruim):
    with pytest.raises(ValueError):
        backend.processar_leitura("mw-A", ruim, instante=T0)


def test_valor_fora_da_faixa_nao_vira_medida(backend):
    r = backend.processar_leitura("mw-A", pzem(fp=7.5, frequencia_hz=600), instante=T0)
    assert r["fp"] is None and r["frequencia_hz"] is None and r["potencia_w"] == 548.2


class Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, payload if isinstance(payload, bytes) else payload.encode()


def test_mensagens_mqtt_e_csv(backend):
    import json
    backend.ao_receber(None, None, Msg("meterwatt/mw-A/telemetria", json.dumps(pzem(simulado=True))))
    backend.ao_receber(None, None, Msg("meterwatt/mw-A/telemetria", "isto nao e json"))
    backend.ao_receber(None, None, Msg("meterwatt/<script>/telemetria", json.dumps(pzem())))
    backend.ao_receber(None, None, Msg("outra/mw-A/telemetria", json.dumps(pzem())))
    backend.ao_receber(None, None, Msg("meterwatt/mw-A/telemetria", b"\xff\xfe"))
    backend.ao_receber(None, None, Msg("meterwatt/mw-A/status", "offline"))
    with backend.trava:
        assert list(backend.dispositivos) == ["mw-A"]
        assert backend.dispositivos["mw-A"]["status_mqtt"] == "offline"
    linhas = open(backend.ARQUIVO_CSV, encoding="utf-8").read().strip().splitlines()
    assert linhas[0].split(",") == backend.CAMPOS_CSV and len(linhas) == 2
    assert "True" in linhas[1].split(",")[-1]


def test_api_e_painel(backend):
    cliente = backend.app.test_client()
    assert cliente.get("/get").status_code == 404
    vazio = cliente.get("/api/dispositivos").get_json()
    assert vazio["dispositivos"] == [] and vazio["tarifa_kwh"] == 1.0

    backend.processar_leitura("mw-A", pzem())
    backend.processar_leitura("mw-A", pzem(potencia_w=600.0, energia_wh=1001))
    d = cliente.get("/api/dispositivos").get_json()["dispositivos"][0]
    assert d["dispositivo"] == "mw-A" and d["online"] is True and d["potencia_w"] == 600.0
    assert cliente.get("/get").get_json()["watts"] == 600.0
    assert len(cliente.get("/dispositivos").get_json()) == 1

    h = cliente.get("/api/historico/mw-A?minutos=10").get_json()
    assert h["unidade"] == "W" and [p[1] for p in h["pontos"]] == [548.2, 600.0]
    assert cliente.get("/api/historico/nao-existe").status_code == 404
    assert cliente.get("/api/historico/..%2F..%2Fetc").status_code == 404

    pagina = cliente.get("/")
    assert pagina.status_code == 200 and b"MeterWatt" in pagina.data
    assert "default-src 'self'" in pagina.headers["Content-Security-Policy"]
    assert pagina.headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in pagina.headers


def test_medidor_some_do_ar(backend):
    backend.processar_leitura("mw-A", pzem(), instante=T0)
    with backend.trava:
        r = backend._resumo("mw-A", backend.dispositivos["mw-A"], agora=T0 + 60)
    assert r["online"] is False and r["segundos_desde_leitura"] == 60 and r["custo_por_hora"] is None


def test_consumo_de_hoje_sobrevive_a_reinicio(backend):
    import csv
    agora = datetime.now(timezone.utc)
    with open(backend.ARQUIVO_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=backend.CAMPOS_CSV)
        w.writeheader()
        for wh in (2000, 2300, 2900):
            w.writerow({"recebido_em": agora.isoformat(timespec="seconds"), "dispositivo": "mw-A",
                        "energia_wh": wh, "sensor_ok": True})
        w.writerow({"recebido_em": agora.isoformat(timespec="seconds"), "dispositivo": "mw-A",
                    "sensor_ok": False})
    assert backend.recuperar_consumo_de_hoje() == 3
    r = backend.processar_leitura("mw-A", pzem(energia_wh=3000))
    assert r["kwh_hoje"] == pytest.approx(1.0)


def test_csv_de_versao_antiga_e_guardado(backend, tmp_path):
    with open(backend.ARQUIVO_CSV, "w", encoding="utf-8") as f:
        f.write("recebido_em,dispositivo,irms_a,va\n2026-11-01T00:00:00+00:00,mw-B,1,127\n")
    backend.preparar_csv()
    assert not os.path.exists(backend.ARQUIVO_CSV)
    assert len([n for n in os.listdir(tmp_path) if "antigo" in n]) == 1


def test_texto_estranho_nao_chega_ao_csv(backend):
    r = backend.processar_leitura("mw-A", pzem(sensor="=cmd|' /C calc'!A0", fw="<b>x</b>"), instante=T0)
    assert r["sensor"] is None and r["fw"] is None
    r = backend.processar_leitura(
        "mw-A", {"sensor_ok": False, "erro_sensor": "<img src=x onerror=alert(1)>"}, instante=T0 + 5)
    assert r["erro_sensor"] == "desconhecido"
