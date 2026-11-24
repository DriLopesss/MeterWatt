"""Qualidade da energia: tensão de 10 em 10 minutos nas faixas da ANEEL, faltas de energia e de fase."""
import json

from conftest import SENHA, conta_pronta, entrar, parear

JANELA = 600


def leitura(plataforma, mid, v, uptime=None, **extra):
    corpo = {"sensor_ok": True, "tensao_v": v, "corrente_a": 1.0, "potencia_w": 100.0, "energia_wh": 10, "fp": 0.9,
             "frequencia_hz": 60.0, **extra}
    if uptime is not None:
        corpo["uptime_s"] = uptime
    return plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(corpo).encode())


def janela(plataforma, relogio, mid, v, segundos=JANELA, uptime=None):
    """Uma leitura por segundo durante `segundos` (começando no início de uma janela de 10 minutos)."""
    for s in range(segundos):
        leitura(plataforma, mid, v, None if uptime is None else uptime + s)
        relogio.avancar(1)


def test_faixas_da_aneel_e_nominal_pela_mediana(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-tensao1")
    relogio.t = (relogio.t // JANELA + 1) * JANELA
    for v in (127.0, 125.0, 114.0, 108.0, 107.5, 128.0):   # adequada, adequada, precária, crítica, crítica, adequada
        janela(plataforma, relogio, mid, v)
    janela(plataforma, relogio, mid, 140.0, segundos=200)    # menos da metade das leituras: não conta
    r = cliente.get("/api/qualidade?dias=7").get_json()
    q = r["medidores"][0]
    assert q["nominal"] == 127 and q["fase"] == 1 and q["intervalos"] == 6
    assert q["fracao"] == {"adequada": 0.5, "precaria": 0.1667, "critica": 0.3333}
    assert q["faixas"]["adequada"] == [117, 133] and q["menor"]["v"] == 107.5
    criticos = [e for e in r["eventos"] if e["tipo"] == "tensao_critica"]
    assert len(criticos) == 1 and criticos[0]["duracao_min"] == 20 and criticos[0]["min_v"] == 107.5   # dois intervalos seguidos
    # Aviso de tensão crítica: uma vez por dia.
    assert plataforma.alertas.verificar() >= 1
    avisos = [a for a in cliente.get("/api/avisos").get_json()["avisos"] if a["tipo"] == "tensao"]
    assert len(avisos) == 1 and "107,5 V" in avisos[0]["titulo"] and "baixa" in avisos[0]["titulo"]
    assert plataforma.alertas.verificar() == 0


def test_casa_de_220_v(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-tensao2")
    relogio.t = (relogio.t // JANELA + 1) * JANELA
    for v in (221.0, 232.5, 236.0):
        janela(plataforma, relogio, mid, v, segundos=400)
        relogio.avancar(200)
    q = cliente.get("/api/qualidade").get_json()["medidores"][0]
    assert q["nominal"] == 220 and q["fracao"] == {"adequada": 0.3333, "precaria": 0.3333, "critica": 0.3333}


def test_falta_de_energia_pelo_reinicio(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-tensao1")
    assert leitura(plataforma, mid, 127, uptime=5000) == "gravada"
    caiu = relogio.t
    # 40 minutos depois volta, reiniciado há 30 s: faltou energia de "caiu" até a hora em que ligou.
    relogio.avancar(40 * 60)
    assert leitura(plataforma, mid, 127, uptime=30) == "gravada"
    eventos = [e for e in cliente.get("/api/qualidade").get_json()["eventos"] if e["tipo"] == "falta_energia"]
    assert len(eventos) == 1 and eventos[0]["duracao_min"] == round((40 * 60 - 30) / 60)
    assert eventos[0]["inicio"] == plataforma.iso(caiu)
    assert plataforma.alertas.verificar() >= 1
    aviso = next(a for a in cliente.get("/api/avisos").get_json()["avisos"] if a["tipo"] == "energia")
    assert "Faltou energia por 40 minutos" == aviso["titulo"] and "5 anos" in aviso["texto"]
    # Reinício rápido (menos de 3 minutos) ou só o Wi-Fi caindo (o tempo ligado continua): não é falta de energia.
    relogio.avancar(60)
    leitura(plataforma, mid, 127, uptime=10)
    relogio.avancar(30 * 60)
    leitura(plataforma, mid, 127, uptime=10 + 30 * 60 + 60)
    assert len([e for e in cliente.get("/api/qualidade").get_json()["eventos"] if e["tipo"] == "falta_energia"]) == 1


def test_falta_de_fase_quando_ela_volta(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-bifa01")

    def fases(ok2):
        return {"sensor_ok": True, "tensao_v": 127, "corrente_a": 2, "potencia_w": 200, "energia_wh": 10, "frequencia_hz": 60,
                "fases": [{"fase": 1, "ok": True, "tensao_v": 127.0, "corrente_a": 1, "potencia_w": 100, "energia_wh": 5, "fp": 0.9},
                          {"fase": 2, "ok": True, "tensao_v": 221.0, "corrente_a": 1, "potencia_w": 100, "energia_wh": 5, "fp": 0.9}
                          if ok2 else {"fase": 2, "ok": False, "erro": "sem_resposta"}]}
    pub = lambda c: plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(c).encode())  # noqa: E731
    pub(fases(True))
    relogio.avancar(1)
    pub(fases(False))
    relogio.avancar(10 * 60)
    pub(fases(False))
    relogio.avancar(1)
    pub(fases(True))
    eventos = [e for e in cliente.get("/api/qualidade").get_json()["eventos"] if e["tipo"] == "falta_fase"]
    assert len(eventos) == 1 and eventos[0]["fase"] == 2 and eventos[0]["duracao_min"] == 10
    # A tensão de cada fase é registrada separada.
    db = plataforma.db()
    assert {r[0] for r in db.execute("SELECT fase FROM tensao_10min WHERE medidor_id = ?", (mid,))} == {1, 2}


def test_copia_apagar_e_retencao(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-tensao1")
    leitura(plataforma, mid, 127, uptime=5000)
    relogio.avancar(3600)
    leitura(plataforma, mid, 127, uptime=10)
    copia = cliente.post("/api/privacidade/exportar").get_json()["medidores"][0]
    assert copia["tensao_10_minutos"][0]["tensao_media"] == 127 and copia["eventos_de_energia"][0]["tipo"] == "falta_energia"
    relogio.avancar(200 * 86400)
    leitura(plataforma, mid, 127, uptime=10 + 200 * 86400)
    apagados = plataforma.privacidade.reter()
    assert apagados["tensao_10min"] >= 1 and apagados["eventos_energia"] == 1
    entrar(cliente)                                          # a sessão venceu nesses 200 dias
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    db = plataforma.db()
    assert db.execute("SELECT COUNT(*) FROM tensao_10min").fetchone()[0] == 0
