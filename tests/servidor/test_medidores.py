"""Pareamento, leituras, isolamento entre contas, consumo fantasma e meta do mês."""
import json

from conftest import INICIO, conta_pronta, leitura_pzem, parear


def mandar(plataforma, relogio, mid, potencia, contador, segundos=1.0, **extra):
    relogio.avancar(segundos)
    return plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(potencia, contador, **extra))


def test_medidor_desconhecido_nao_grava_nada(plataforma, relogio):
    assert mandar(plataforma, relogio, "mw-estranho", 100, 10) == "desconhecido"
    assert plataforma.db().execute("SELECT COUNT(*) FROM leituras").fetchone()[0] == 0


def test_pareamento_e_leituras(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    assert plataforma.publicados[-1] == (f"meterwatt/{mid}/comando", "pareado")
    contador = 1000.0
    for _ in range(120):
        contador += 0.05                                # 180 W durante 1 s ≈ 0,05 Wh
        assert mandar(plataforma, relogio, mid, 180, contador) == "gravada"
    lista = cliente.get("/api/medidores").get_json()
    m = lista["medidores"][0]
    assert m["online"] and m["potencia"] == 180 and m["unidade"] == "W" and m["sensor_ok"]
    assert abs(m["kwh_hoje"] - 119 * 0.05 / 1000) < 1e-4   # a primeira leitura só marca o contador
    assert m["custo_hoje"] == round(m["kwh_hoje"] * 1.0, 2)
    h = cliente.get(f"/api/medidores/{mid}/historico?periodo=2h").get_json()
    assert len(h["pontos"]) == 120 and h["unidade"] == "W"
    relogio.avancar(30)
    assert cliente.get("/api/medidores").get_json()["medidores"][0]["online"] is False


def test_codigo_errado_expirado_e_limite(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    codigo = cliente.post("/api/medidores/codigo").get_json()["codigo"]
    mqtt = plataforma.medidores.processar_mqtt
    assert mqtt("meterwatt/mw-1/pareamento", b'{"codigo":"AAAA-AAAA"}') == "recusado"
    relogio.avancar(16 * 60)
    assert mqtt("meterwatt/mw-1/pareamento", json.dumps({"codigo": codigo}).encode()) == "recusado"
    for _ in range(3):
        assert cliente.post("/api/medidores/codigo").status_code == 201
    assert cliente.post("/api/medidores/codigo").status_code == 429


def test_adivinhar_codigo_bloqueia_o_aparelho(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    codigo = cliente.post("/api/medidores/codigo").get_json()["codigo"]
    mqtt = plataforma.medidores.processar_mqtt
    for _ in range(10):
        mqtt("meterwatt/mw-ladrao/pareamento", b'{"codigo":"BBBB-BBBB"}')
    assert mqtt("meterwatt/mw-ladrao/pareamento", json.dumps({"codigo": codigo}).encode()) == "recusado"


def test_medidor_de_outra_conta_nao_e_roubado(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    bia = conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    codigo = bia.post("/api/medidores/codigo").get_json()["codigo"]
    assert plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/pareamento", json.dumps({"codigo": codigo}).encode()) == "recusado"
    assert bia.get("/api/medidores").get_json()["medidores"] == []
    # e ela não vê, não renomeia e não remove o medidor da Ana (404, como se não existisse)
    assert bia.get(f"/api/medidores/{mid}/historico").status_code == 404
    assert bia.patch(f"/api/medidores/{mid}", {"apelido": "meu"}).status_code == 404
    assert bia.delete(f"/api/medidores/{mid}").status_code == 404
    assert len(cliente.get("/api/medidores").get_json()["medidores"]) == 1


def test_sem_autorizacao_de_consumo_nao_pareia_nem_grava(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    assert mandar(plataforma, relogio, mid, 100, 10) == "gravada"
    assert cliente.put("/api/privacidade/consumo", {"aceito": False}).status_code == 200
    assert mandar(plataforma, relogio, mid, 100, 11) == "sem_autorizacao"
    assert cliente.post("/api/medidores/codigo").status_code == 403


def test_mensagens_estranhas_sao_ignoradas(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    mqtt = plataforma.medidores.processar_mqtt
    relogio.avancar(1)
    assert mqtt(f"meterwatt/{mid}/telemetria", b"x" * 3000) == "ignorada"
    assert mqtt(f"meterwatt/{mid}/telemetria", b"\xff\xfe") == "ignorada"
    assert mqtt(f"meterwatt/{mid}/telemetria", b"[1,2]") == "ignorada"
    assert mqtt("meterwatt/../telemetria", b"{}") == "ignorada"
    assert mqtt(f"meterwatt/{mid}/telemetria", b'{"tensao_v": "220"}') == "invalida"
    assert mandar(plataforma, relogio, mid, 99999, 1) == "invalida"            # potência fora da faixa
    assert mandar(plataforma, relogio, mid, 100, 1, segundos=1) == "gravada"
    assert mandar(plataforma, relogio, mid, 100, 1, segundos=0.1) == "rapida_demais"


def test_sensor_sem_leitura(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    r = plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria",
                                            b'{"sensor_ok": false, "erro_sensor": "sem_resposta", "rssi": -70}')
    assert r == "sensor_sem_leitura"
    m = cliente.get("/api/medidores").get_json()["medidores"][0]
    assert m["sensor_ok"] is False and m["erro_sensor"] == "sem_resposta" and m["potencia"] is None


def test_contador_zerado_nao_gera_consumo_negativo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    mandar(plataforma, relogio, mid, 100, 5000)
    mandar(plataforma, relogio, mid, 100, 5001)
    mandar(plataforma, relogio, mid, 100, 0)             # módulo zerado
    mandar(plataforma, relogio, mid, 100, 2)
    assert abs(cliente.get("/api/medidores").get_json()["medidores"][0]["kwh_hoje"] - 0.003) < 1e-9


def test_remover_apaga_os_dados(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    mandar(plataforma, relogio, mid, 100, 1)
    assert cliente.patch(f"/api/medidores/{mid}", {"apelido": "  Geladeira  "}).status_code == 200
    assert cliente.get("/api/medidores").get_json()["medidores"][0]["apelido"] == "Geladeira"
    assert cliente.patch(f"/api/medidores/{mid}", {"apelido": "x" * 41}).status_code == 400
    assert cliente.delete(f"/api/medidores/{mid}").status_code == 200
    db = plataforma.db()
    assert db.execute("SELECT COUNT(*) FROM leituras").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM consumo_5min").fetchone()[0] == 0
    assert plataforma.publicados[-1] == (f"meterwatt/{mid}/comando", "despareado")


def _preencher_baldes(plataforma, mid, inicio, fim, watts_por_hora):
    """Grava direto no banco o consumo de 5 em 5 minutos (dias de histórico sem esperar dias)."""
    db = plataforma.db()
    t = int(inicio // 300 * 300)
    with db:
        while t < fim:
            w = watts_por_hora(t)
            db.execute("INSERT INTO consumo_5min (medidor_id, inicio, n, soma_w, min_w, max_w, wh) VALUES (?, ?, 300, ?, ?, ?, ?)",
                       (mid, t, w * 300, w, w, w * 300 / 3600))
            t += 300


def test_consumo_fantasma(cliente, plataforma, relogio):
    relogio.t = INICIO + 6 * 86400
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    assert cliente.get("/api/fantasma").get_json()["disponivel"] is False
    from datetime import datetime
    zona = plataforma.cfg.zona

    def perfil(t):
        h = datetime.fromtimestamp(t, zona).hour
        return 60 + (3 if (t // 300) % 2 else 0) if h < 5 else 400   # madrugada ~60 W, dia 400 W
    _preencher_baldes(plataforma, mid, relogio.t - 7 * 86400, relogio.t, perfil)
    f = cliente.get("/api/fantasma").get_json()
    assert f["disponivel"] and f["watts"] == 60 and f["noites"] >= 6
    assert f["kwh_mes"] == round(60 * 24 * 30 / 1000, 1) and f["custo_mes"] == f["kwh_mes"]
    assert 0.15 < f["fatia_do_consumo"] < 0.25


def test_fantasma_ignora_noite_incompleta(cliente, plataforma, relogio):
    relogio.t = INICIO + 3 * 86400
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    # só 1 hora de cada madrugada: cobertura menor que 80%
    for d in range(1, 4):
        inicio = plataforma.inicio_do_dia(relogio.t, d)
        _preencher_baldes(plataforma, mid, inicio, inicio + 3600, lambda t: 50)
    assert cliente.get("/api/fantasma").get_json()["disponivel"] is False


def test_meta_do_mes(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    assert cliente.get("/api/meta").get_json()["meta"] is None
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": 95})
    relogio.t = plataforma.inicio_do_mes(INICIO) + 10 * 86400       # dia 11, meio-dia não importa
    _preencher_baldes(plataforma, mid, plataforma.inicio_do_mes(relogio.t), relogio.t, lambda t: 125)   # 3 kWh/dia
    m = cliente.get("/api/meta").get_json()
    assert m["gasto_ate_agora"] == 30.0 and m["media_diaria"] == 3.0
    assert abs(m["projecao"] - 90.0) < 0.01                         # novembro tem 30 dias
    assert m["status"] == "atencao"                                 # 90 de 95: passou de 90% da meta
    assert m["folga_diaria"] == round((95 - 30) / 20, 2)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": 80})
    assert cliente.get("/api/meta").get_json()["status"] == "acima"


def test_meta_sem_tarifa(ambiente, relogio):
    from conftest import Cliente
    from meterwatt import config
    from meterwatt.mensageiro import MensageiroMemoria
    from meterwatt.plataforma import Plataforma
    from meterwatt.web import criar_app
    del ambiente["MW_TARIFA_PADRAO"]
    p = Plataforma(config.carregar(ambiente), MensageiroMemoria(), relogio=relogio)
    p.publicar = lambda *a: None
    c = conta_pronta(Cliente(criar_app(p)), p)
    assert c.get("/api/meta").get_json()["sem_tarifa"] is True
    parear(c, p)
    p.medidores.processar_mqtt("meterwatt/mw-a1b2c3/telemetria", leitura_pzem(100, 1))
    assert c.get("/api/medidores").get_json()["medidores"][0]["custo_hoje"] is None


def test_consumo_por_dia(cliente, plataforma, relogio):
    relogio.t = INICIO + 3 * 86400
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    _preencher_baldes(plataforma, mid, plataforma.inicio_do_dia(relogio.t, 2), plataforma.inicio_do_dia(relogio.t), lambda t: 100)
    dias = cliente.get("/api/consumo/dias?dias=3").get_json()["dias"]
    assert [d["kwh"] for d in dias] == [2.4, 2.4, 0.0]
