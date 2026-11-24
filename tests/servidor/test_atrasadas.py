"""Minutos medidos sem conexão e mandados depois (firmware 3.5): cada um no seu horário, sem contar duas vezes."""
import json

from conftest import SENHA, conta_pronta, leitura_pzem, parear

BALDE = 300


def ao_vivo(plataforma, mid, w, wh):
    return plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(w, wh))


def atrasadas(plataforma, mid, uptime_agora, minutos, perdidos=0):
    corpo = {"tipo": "atrasadas", "uptime_agora": uptime_agora, "perdidos": perdidos, "minutos": minutos}
    return plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(corpo).encode())


def minuto(u, w, e, n=60):
    return {"u": u, "n": n, "w": w, "min": w, "max": w, "e": e if isinstance(e, list) else [e]}


def baldes(plataforma, mid):
    return {r["inicio"]: (r["n"], r["wh"]) for r in plataforma.db().execute(
        "SELECT inicio, n, wh FROM consumo_5min WHERE medidor_id = ? ORDER BY inicio", (mid,))}


def test_queda_de_30_minutos_volta_nos_horarios_certos(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-queda1")
    relogio.t = (relogio.t // BALDE + 1) * BALDE                     # começa num balde redondo
    inicio = relogio.t
    assert ao_vivo(plataforma, mid, 600, 10_000) == "gravada"
    # 30 minutos sem conexão a 600 W = 10 Wh por minuto. A conexão volta no minuto 31.
    relogio.avancar(31 * 60)
    uptime_agora = 5000
    minutos = [minuto(uptime_agora - (31 - k) * 60 + 59, 600, 10_000 + 10 * k) for k in range(1, 31)]
    for i in range(0, 30, 8):                                        # como o firmware: 8 por mensagem
        assert atrasadas(plataforma, mid, uptime_agora, minutos[i:i + 8]) == "atrasadas"
    b = baldes(plataforma, mid)
    # Os 6 baldes de 5 minutos da queda têm leituras e a energia de cada um, sem nada amontoado no fim.
    da_queda = [b[inicio + k * BALDE] for k in range(6)]
    assert all(n >= 240 for n, _ in da_queda[1:])
    assert sum(wh for _, wh in b.values()) == 300                    # 30 minutos × 10 Wh, nem mais nem menos
    assert max(wh for _, wh in b.values()) <= 50 + 0.001
    # E a leitura ao vivo seguinte conta só o que veio depois do último minuto guardado.
    relogio.avancar(1)
    assert ao_vivo(plataforma, mid, 600, 10_302) == "gravada"
    assert sum(wh for _, wh in baldes(plataforma, mid).values()) == 302
    m = next(x for x in cliente.get("/api/medidores").get_json()["medidores"] if x["id"] == mid)
    assert m["recuperacao"]["minutos"] == 30 and m["recuperacao"]["perdidos"] == 0


def test_mensagem_repetida_nao_conta_duas_vezes(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-queda1")
    ao_vivo(plataforma, mid, 600, 10_000)
    relogio.avancar(10 * 60)
    lote = [minuto(1000 + k * 60, 600, 10_000 + 10 * (k + 1)) for k in range(8)]
    assert atrasadas(plataforma, mid, 1000 + 8 * 60, lote) == "atrasadas"
    antes = baldes(plataforma, mid)
    relogio.avancar(2)
    assert atrasadas(plataforma, mid, 1000 + 8 * 60 + 2, lote) == "atrasadas"   # a mesma mensagem de novo
    assert baldes(plataforma, mid) == antes


def test_duas_fases_e_perdidos(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-queda2")
    fases = [{"fase": 1, "ok": True, "tensao_v": 127, "corrente_a": 3, "potencia_w": 300, "energia_wh": 100, "fp": 0.9},
             {"fase": 2, "ok": True, "tensao_v": 127, "corrente_a": 2, "potencia_w": 200, "energia_wh": 500, "fp": 0.9}]
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", json.dumps(
        {"sensor_ok": True, "tensao_v": 127, "corrente_a": 5, "potencia_w": 500, "energia_wh": 600, "frequencia_hz": 60,
         "fases": fases}).encode())
    relogio.avancar(5 * 60)
    lote = [minuto(200, 500, [105, 503]), minuto(260, 300, [110, None]), minuto(320, 500, [115, 507])]
    assert atrasadas(plataforma, mid, 330, lote, perdidos=4) == "atrasadas"
    assert sum(wh for _, wh in baldes(plataforma, mid).values()) == 15 + 7     # fase 1: 15 Wh; fase 2: 7 Wh
    m = next(x for x in cliente.get("/api/medidores").get_json()["medidores"] if x["id"] == mid)
    assert m["recuperacao"]["perdidos"] == 4


def test_recusa_o_que_nao_faz_sentido(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-queda1")
    ao_vivo(plataforma, mid, 600, 10_000)
    ruins = [[], [minuto(10, 600, 1)] * 21, [{"u": 10}], [minuto(10, 99999, 1)], [minuto(10, 600, "x")],
             [minuto(10, 600, [1, 2, 3, 4])], [minuto(999, 600, 1)]]          # u depois do uptime de agora
    for lote in ruins:
        assert atrasadas(plataforma, mid, 500, lote) == "invalida", lote
    # Mais antigo que 13 horas, ou antes de o medidor ser pareado: ignorado.
    assert atrasadas(plataforma, mid, 100_000, [minuto(100, 600, 10_010)]) == "atrasadas"
    relogio.avancar(60)
    assert atrasadas(plataforma, mid, 10_000, [minuto(100, 600, 10_010)]) == "atrasadas"
    assert sum(n for n, _ in baldes(plataforma, mid).values()) == 1
    # Medidor de outra pessoa (não pareado): nada entra.
    assert atrasadas(plataforma, "mw-outro", 500, [minuto(400, 600, 1)]) == "desconhecido"


def test_apagar_consumo_apaga_o_resumo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma, "mw-queda1")
    ao_vivo(plataforma, mid, 600, 10_000)
    relogio.avancar(120)
    atrasadas(plataforma, mid, 200, [minuto(100, 600, 10_010), minuto(160, 600, 10_020)])
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    assert plataforma.db().execute("SELECT ultima_recuperacao FROM medidores").fetchone()[0] is None
