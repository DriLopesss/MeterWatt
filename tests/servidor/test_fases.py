"""Casa com duas ou três fases: um PZEM por fase, o medidor manda a soma e cada fase (firmware 3.4)."""
import json

from conftest import conta_pronta, parear


def fase(n, w, wh, v=127.0, fp=0.95):
    return {"fase": n, "ok": True, "tensao_v": v, "corrente_a": round(w / v / fp, 3), "potencia_w": w, "energia_wh": wh, "fp": fp}


def sem(n, erro="sem_resposta"):
    return {"fase": n, "ok": False, "erro": erro}


def leitura(plataforma, mid, fases, relogio=None, segundos=1):
    if relogio:
        relogio.avancar(segundos)
    lidas = [f for f in fases if f["ok"]]
    corpo = {"fw": "3.4.0", "sensor": "pzem004t-v3", "sensor_ok": bool(lidas), "fases": fases, "frequencia_hz": 60.0, "rssi": -60}
    if lidas:   # o total de cima, como o firmware manda (servidores antigos usam só ele)
        corpo.update(potencia_w=sum(f["potencia_w"] for f in lidas), energia_wh=sum(f["energia_wh"] for f in lidas),
                     tensao_v=127.0, corrente_a=sum(f["corrente_a"] for f in lidas), fp=0.9)
    else:
        corpo["erro_sensor"] = "sem_resposta"
    return plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(corpo).encode())


def energia_wh(plataforma, mid):
    return plataforma.db().execute("SELECT SUM(wh) FROM consumo_5min WHERE medidor_id = ?", (mid,)).fetchone()[0] or 0


def medidor(cliente, mid):
    return next(m for m in cliente.get("/api/medidores").get_json()["medidores"] if m["id"] == mid)


def test_soma_das_fases_e_cada_uma(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-bifa01")
    assert leitura(plataforma, mid, [fase(1, 2750, 1000), fase(2, 2750, 2000)]) == "gravada"
    m = medidor(cliente, mid)
    assert m["potencia"] == 5500 and m["sensor_ok"] is True
    assert [f["fase"] for f in m["fases"]] == [1, 2] and m["fases"][0]["potencia_w"] == 2750 and "energia_wh" not in m["fases"][0]
    assert abs(m["fp"] - 0.95) < 0.001                   # ativa ÷ soma das aparentes
    # Energia contada fase a fase.
    assert leitura(plataforma, mid, [fase(1, 2750, 1010), fase(2, 2750, 2005)], relogio) == "gravada"
    assert energia_wh(plataforma, mid) == 15


def test_fase_que_some_e_volta_nao_perde_energia(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-bifa01")
    leitura(plataforma, mid, [fase(1, 500, 1000), fase(2, 300, 2000)])
    # A fase 2 some: o total passa a ser só a fase 1, e ela fica marcada com desde quando.
    assert leitura(plataforma, mid, [fase(1, 500, 1004), sem(2)], relogio) == "gravada"
    m = medidor(cliente, mid)
    assert m["potencia"] == 500 and m["fases"][1] == {"fase": 2, "ok": False, "erro": "sem_resposta"}
    desde = json.loads(plataforma.db().execute("SELECT fases FROM medidores").fetchone()[0])[1]["desde"]
    assert leitura(plataforma, mid, [fase(1, 500, 1008), sem(2)], relogio, 30) == "gravada"
    assert json.loads(plataforma.db().execute("SELECT fases FROM medidores").fetchone()[0])[1]["desde"] == desde
    # Volta: o que a fase 2 mediu no intervalo entra (contador 2000 -> 2006), sem confundir com contador zerado.
    assert leitura(plataforma, mid, [fase(1, 500, 1010), fase(2, 300, 2006)], relogio) == "gravada"
    assert energia_wh(plataforma, mid) == 10 + 6
    # Nenhuma fase: sem número, mas a lista fica (para a tela dizer o que houve).
    assert leitura(plataforma, mid, [sem(1), sem(2, "crc_invalido")], relogio) == "sensor_sem_leitura"
    m = medidor(cliente, mid)
    assert m["sensor_ok"] is False and m["potencia"] is None and [f["ok"] for f in m["fases"]] == [False, False]


def test_lista_de_fases_estranha_vale_o_total(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-bifa01")
    for estranha in ([fase(1, 100, 10)], [fase(1, 100, 10), fase(1, 100, 10)], [fase(1, 100, 10), {"fase": 4, "ok": True}],
                     "fases", [fase(1, 100, 10), fase(2, 100, 10), fase(3, 100, 10), {"fase": 3}]):
        corpo = {"fw": "3.4.0", "sensor_ok": True, "tensao_v": 127.0, "corrente_a": 1.0, "potencia_w": 120.0, "energia_wh": 50,
                 "fases": estranha}
        relogio.avancar(1)
        assert plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(corpo).encode()) == "gravada"
        m = medidor(cliente, mid)
        assert m["potencia"] == 120 and m["fases"] is None


def test_ligacao_da_conta_e_fases_do_medidor(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-mono01")
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(
        {"sensor_ok": True, "tensao_v": 127.0, "corrente_a": 1.0, "potencia_w": 120.0, "energia_wh": 50}).encode())
    assert medidor(cliente, mid)["fases_esperadas"] is None              # ligação não informada
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": None, "ligacao": "bifasica"})
    m = medidor(cliente, mid)
    assert m["fases_esperadas"] == 2 and m["fases"] is None              # a tela avisa: mede 1 de 2 fases


def test_aviso_de_fase_sem_resposta(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-bifa01")
    leitura(plataforma, mid, [fase(1, 500, 1000), fase(2, 300, 2000)])
    for _ in range(5):                                     # 4 minutos sem a fase 2: ainda não
        leitura(plataforma, mid, [fase(1, 500, 1000), sem(2)], relogio, 60)
    assert plataforma.alertas.verificar() == 0
    leitura(plataforma, mid, [fase(1, 500, 1000), sem(2)], relogio, 61)
    assert plataforma.alertas.verificar() == 1
    assert plataforma.alertas.verificar() == 0             # uma vez por queda
    aviso = cliente.get("/api/avisos").get_json()["avisos"][0]
    assert aviso["tipo"] == "fase" and "Fase 2" in aviso["titulo"]
    # As duas mudas: é problema do medidor, não de uma fase (não avisa de novo por fase).
    leitura(plataforma, mid, [sem(1), sem(2)], relogio, 600)
    assert plataforma.alertas.verificar() == 0


def test_apagar_consumo_apaga_as_fases(cliente, plataforma):
    from conftest import SENHA
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-bifa01")
    leitura(plataforma, mid, [fase(1, 500, 1000), fase(2, 300, 2000)])
    copia = cliente.post("/api/privacidade/exportar").get_json()
    assert copia["medidores"][0]["ultima_leitura_por_fase"][1]["potencia_w"] == 300
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    r = plataforma.db().execute("SELECT fases, contadores_fases FROM medidores").fetchone()
    assert r["fases"] is None and r["contadores_fases"] is None
