"""Detetive de aparelhos: degraus de potência, ciclos, agrupamento e privacidade."""
import random

from conftest import SENHA, conta_pronta, leitura_pzem, parear
from meterwatt.detetive import Detector, palpite


def serie(detector, valores, t0=0.0):
    eventos = []
    for i, w in enumerate(valores):
        e = detector.observar(t0 + i, w)
        if e:
            eventos.append((round(e[0] - t0), round(e[1])))
    return eventos


def test_detector_acha_ligar_e_desligar():
    eventos = serie(Detector(), [100] * 10 + [1400] * 20 + [100] * 10)
    assert eventos == [(10, 1300), (30, -1300)]


def test_detector_ignora_ruido_e_variacao_lenta():
    rng = random.Random(1)
    assert serie(Detector(), [200 + rng.uniform(-6, 6) for _ in range(300)]) == []
    assert serie(Detector(), [200 + i * 0.5 for i in range(300)]) == []        # sobe 150 W em 5 minutos, sem degrau


def test_detector_degrau_pequeno_nao_conta():
    assert serie(Detector(), [300] * 10 + [325] * 10) == []


def test_detector_com_carga_alta_exige_degrau_maior():
    # com 5.500 W ligados, 60 W de diferença cabem no ruído; 400 W não
    assert serie(Detector(), [5500] * 10 + [5560] * 10) == []
    assert serie(Detector(), [5500] * 10 + [5900] * 10) == [(10, 400)]


def test_detector_recomeca_depois_de_um_buraco():
    d = Detector()
    for i in range(10):
        d.observar(i, 100)
    assert serie(d, [900] * 10, t0=600) == []            # 10 minutos sem leitura: não inventa um degrau


def test_palpites():
    assert "chuveiro" in palpite(5400, 8)
    assert "micro-ondas" in palpite(1300, 3)
    assert "geladeira" in palpite(140, 15)


def mandar_serie(plataforma, relogio, mid, valores):
    contador = 1000.0
    for w in valores:
        relogio.avancar(1)
        contador += w / 3600
        plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(w, round(contador, 2)))


def ciclos_de_geladeira(n, base=80, potencia=140, ligada=40, desligada=40, rng=None):
    rng = rng or random.Random(7)
    valores = [base] * 10
    for _ in range(n):
        valores += [base + potencia * rng.uniform(0.98, 1.02) for _ in range(ligada)]
        valores += [base + rng.uniform(-2, 2) for _ in range(desligada)]
    return valores


def test_desligado_por_padrao_nao_guarda_nada(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(4))
    db = plataforma.db()
    assert db.execute("SELECT COUNT(*) FROM ciclos").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM degraus_abertos").fetchone()[0] == 0
    r = cliente.get("/api/aparelhos").get_json()
    assert r["autorizado"] is False and r["aparelhos"] == []


def test_aparelho_aparece_depois_de_tres_ciclos(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    assert cliente.put("/api/privacidade/aparelhos", {"aceito": True}).status_code == 200
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0})
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(2))
    r = cliente.get("/api/aparelhos").get_json()
    assert r["aparelhos"] == [] and r["em_estudo"] == 1
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(2)[10:])
    r = cliente.get("/api/aparelhos").get_json()
    assert len(r["aparelhos"]) == 1
    a = r["aparelhos"][0]
    assert 130 <= a["potencia_w"] <= 150 and a["vezes_na_semana"] == 4 and a["nome"] is None
    assert "geladeira" in a["palpite"] and a["ligado_agora"] is False and a["custo_mes"] is not None
    assert 0.5 < a["duracao_media_min"] < 0.8


def test_dois_aparelhos_ao_mesmo_tempo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    valores = [100] * 10
    for _ in range(3):
        valores += [240] * 20 + [1540] * 15 + [240] * 20 + [100] * 20      # geladeira liga, micro-ondas liga e desliga, geladeira desliga
    mandar_serie(plataforma, relogio, mid, valores)
    potencias = sorted(a["potencia_w"] for a in cliente.get("/api/aparelhos").get_json()["aparelhos"])
    assert potencias == [140, 1300]


def test_ligado_agora(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(3) + [80 + 140] * 10)
    assert cliente.get("/api/aparelhos").get_json()["aparelhos"][0]["ligado_agora"] is True


def test_dar_nome_e_ocultar(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(3))
    ap = cliente.get("/api/aparelhos").get_json()["aparelhos"][0]["id"]
    assert cliente.patch(f"/api/aparelhos/{ap}", {"nome": "  Geladeira  "}).status_code == 200
    assert cliente.get("/api/aparelhos").get_json()["aparelhos"][0]["nome"] == "Geladeira"
    assert cliente.patch(f"/api/aparelhos/{ap}", {"nome": ""}).status_code == 400
    bia = conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    assert bia.patch(f"/api/aparelhos/{ap}", {"nome": "meu"}).status_code == 404
    assert cliente.patch(f"/api/aparelhos/{ap}", {"oculto": True}).status_code == 200
    assert cliente.get("/api/aparelhos").get_json()["aparelhos"] == []


def test_revogar_apaga_tudo_e_exportar_inclui(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(3))
    import json
    dados = json.loads(cliente.post("/api/privacidade/exportar").data)
    assert len(dados["aparelhos_detectados"][0]["ciclos"]) == 3
    assert cliente.put("/api/privacidade/aparelhos", {"aceito": False}).status_code == 200
    db = plataforma.db()
    for tabela in ("aparelhos", "ciclos", "degraus_abertos"):
        assert db.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0] == 0, tabela
    assert mid not in plataforma.detetive.detectores


def test_apagar_consumo_e_remover_medidor_apagam_os_aparelhos(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(3))
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    assert plataforma.db().execute("SELECT COUNT(*) FROM ciclos").fetchone()[0] == 0
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(3))
    assert cliente.delete(f"/api/medidores/{mid}").status_code == 200
    assert plataforma.db().execute("SELECT COUNT(*) FROM aparelhos").fetchone()[0] == 0
    assert mid not in plataforma.detetive.detectores


def test_retencao_dos_ciclos(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    mandar_serie(plataforma, relogio, mid, ciclos_de_geladeira(3))
    relogio.avancar(91 * 86400)
    assert plataforma.privacidade.reter()["ciclos_de_aparelhos"] == 3
    assert plataforma.db().execute("SELECT COUNT(*) FROM aparelhos").fetchone()[0] == 0


def test_consumo_por_mes_divide_pelo_tempo_em_que_o_detetive_olhou(cliente, plataforma, relogio):
    """Um aparelho usado 3 vezes nos últimos 2 dias, com o Detetive olhando a casa há 7 dias, gasta por
    mês o que gastou em 7 dias, e não 3,5 vezes isso (dividir pelos 2 dias desde o primeiro uso dele)."""
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    agora = relogio.t
    db = plataforma.db()
    with db:
        gel = db.execute("INSERT INTO aparelhos (medidor_id, potencia_w, n_ciclos, criado_em) VALUES (?, 140, 336, ?)",
                         (mid, agora - 7 * 86400)).lastrowid
        db.executemany("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, 140, 28)",
                       [(mid, gel, agora - 7 * 86400 + 60 + k * 1800, agora - 7 * 86400 + 780 + k * 1800) for k in range(330)])
        ferro = db.execute("INSERT INTO aparelhos (medidor_id, potencia_w, n_ciclos, criado_em) VALUES (?, 1200, 3, ?)",
                           (mid, agora - 2 * 86400)).lastrowid
        db.executemany("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, 1200, 200)",
                       [(mid, ferro, agora - 2 * 86400 + k * 3600, agora - 2 * 86400 + k * 3600 + 600) for k in range(3)])
    aparelhos = {a["id"]: a for a in cliente.get("/api/aparelhos").get_json()["aparelhos"]}
    a = aparelhos[ferro]
    assert a["vezes_na_semana"] == 3 and a["dias_observados"] == 7.0
    assert a["kwh_mes"] == round(0.6 / 7 * 365 / 12, 1)               # 600 Wh em 7 dias
