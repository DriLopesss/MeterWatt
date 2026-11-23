"""Gráficos do painel: perfil do dia (/api/consumo/horas) e para onde vai agora (/api/agora/partes)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import conta_pronta, leitura_pzem, parear, preencher_baldes

ZONA = ZoneInfo("America/Sao_Paulo")
DIA = 86400


def local(t):
    return datetime.fromtimestamp(t, ZONA)


def em(ano, mes, dia, hora=0, minuto=0):
    return datetime(ano, mes, dia, hora, minuto, tzinfo=ZONA).timestamp()


def rotina(t):
    """Dia útil: 200 W o dia todo e o chuveiro (2 kW a mais) das 19h às 20h. Fim de semana: 500 W das 10h às 12h."""
    d = local(t)
    if d.weekday() < 5:
        return 2200 if d.hour == 19 else 200
    return 700 if 10 <= d.hour < 12 else 200


@pytest.fixture
def ambiente(tmp_path):
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00", "MW_LIMITE_MEDIDORES_GRATIS": "5"}


def casa(cliente, plataforma, relogio, agora, dias=9, watts=rotina, mid="mw-casa01"):
    relogio.t = agora
    conta_pronta(cliente, plataforma)
    parear(cliente, plataforma, mid)
    preencher_baldes(plataforma, mid, agora - dias * DIA, agora, watts)
    return mid


def ao_vivo(plataforma, mid, watts, energia=1000.0):
    assert plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(watts, energia)) == "gravada"


# ------------------------------------------------------------ perfil do dia

def test_sem_medidor(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    h = cliente.get("/api/consumo/horas").get_json()
    assert h["hoje"]["horas"] == [None] * 24 and h["normal"] is None and h["pico"] is None and h["faixa"] is None
    assert len(h["mapa"]) == 14 and all(d["kwh"] == 0 for d in h["mapa"])


def test_hoje_normal_e_horario_mais_pesado(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 12, 30)                    # quinta-feira, meio-dia e meia
    casa(cliente, plataforma, relogio, agora)
    h = cliente.get("/api/consumo/horas").get_json()
    assert h["hora_atual"] == 12 and h["hoje"]["dia"] == "2026-11-19"
    hoje = h["hoje"]["horas"]
    assert hoje[:12] == [pytest.approx(0.2)] * 12        # horas cheias: 200 W por uma hora
    assert hoje[12] == pytest.approx(0.1)                # a hora que está correndo: meia hora até agora
    assert hoje[13:] == [None] * 11                      # o resto do dia ainda não aconteceu
    normal = h["normal"]
    assert normal["tipo"] == "util" and normal["dias"] == 7          # dias úteis desde o dia 10 (desse, só as horas medidas)
    assert normal["horas"][19] == pytest.approx(2.2) and normal["horas"][3] == pytest.approx(0.2)
    assert h["pico"]["hora"] == 19 and h["pico"]["kwh"] == pytest.approx(2.2) and h["pico"]["custo"] == pytest.approx(2.2)
    assert h["pico"]["custo_mes"] == pytest.approx(2.2 * 365 / 12 * 5 / 7, rel=0.01)
    assert h["comparacao"] == {"horas": 12, "hoje_kwh": pytest.approx(2.4), "normal_kwh": pytest.approx(2.4)}
    assert h["faixa"]["base_w"] == pytest.approx(200) and h["faixa"]["alto_w"] >= h["faixa"]["base_w"]
    mapa = {d["dia"]: d for d in h["mapa"]}
    assert len(h["mapa"]) == 14 and h["mapa"][-1]["dia"] == "2026-11-19"
    assert mapa["2026-11-15"]["dia_da_semana"] == 6 and mapa["2026-11-15"]["horas"][10] == pytest.approx(0.7)   # domingo
    assert mapa["2026-11-08"]["horas"] == [None] * 24 and mapa["2026-11-08"]["kwh"] == 0                       # antes do medidor
    assert mapa["2026-11-18"]["kwh"] == pytest.approx(0.2 * 23 + 2.2)


def test_fim_de_semana_compara_com_fim_de_semana(cliente, plataforma, relogio):
    agora = em(2026, 11, 22, 15)                         # domingo
    casa(cliente, plataforma, relogio, agora, dias=12)
    h = cliente.get("/api/consumo/horas").get_json()
    assert h["normal"]["tipo"] == "fim_de_semana" and h["normal"]["dias"] == 3       # 14, 15 e 21 de novembro
    assert h["normal"]["horas"][10] == pytest.approx(0.7) and h["normal"]["horas"][19] == pytest.approx(0.2)
    assert h["pico"]["hora"] in (10, 11)


def test_hora_com_buraco_fica_vazia(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 12, 30)
    mid = casa(cliente, plataforma, relogio, agora)
    db = plataforma.db()
    with db:   # ontem, das 15h às 15h30, o medidor ficou sem Wi-Fi: só 6 dos 12 intervalos
        db.execute("DELETE FROM consumo_5min WHERE medidor_id = ? AND inicio >= ? AND inicio < ?",
                   (mid, em(2026, 11, 18, 15), em(2026, 11, 18, 15, 30)))
    h = cliente.get("/api/consumo/horas").get_json()
    ontem = h["mapa"][-2]
    assert ontem["horas"][15] is None and ontem["horas"][14] == pytest.approx(0.2)


def test_circuito_nao_entra_no_perfil(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 12, 30)
    casa(cliente, plataforma, relogio, agora)
    cozinha = parear(cliente, plataforma, "mw-coz001")
    preencher_baldes(plataforma, cozinha, agora - 9 * DIA, agora, 100)
    assert cliente.get("/api/consumo/horas").get_json()["hoje"]["horas"][3] == pytest.approx(0.3)
    cliente.patch(f"/api/medidores/{cozinha}", {"papel": "circuito"})
    assert cliente.get("/api/consumo/horas").get_json()["hoje"]["horas"][3] == pytest.approx(0.2)


def test_perfil_so_da_propria_conta(cliente, plataforma, relogio, novo_cliente):
    casa(cliente, plataforma, relogio, em(2026, 11, 19, 12, 30))
    outra = novo_cliente()
    conta_pronta(outra, plataforma, email="bia@exemplo.com.br")
    h = outra.get("/api/consumo/horas").get_json()
    assert h["normal"] is None and all(v is None for v in h["hoje"]["horas"])
    assert cliente.get("/api/consumo/horas?dias=abc").status_code == 200
    assert len(cliente.get("/api/consumo/horas?dias=400").get_json()["mapa"]) == 31


# ------------------------------------------------------------ para onde vai agora

def noites(plataforma, mid, agora, watts=120):
    """Três madrugadas com o mínimo em `watts`: é o que fica sempre ligado."""
    preencher_baldes(plataforma, mid, agora - 3 * DIA, agora - 60, watts)


def test_sem_medidor_vivo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    r = cliente.get("/api/agora/partes").get_json()
    assert r["disponivel"] is False and r["motivo"] == "Nenhum medidor pareado."
    parear(cliente, plataforma)
    assert "enviando leituras" in cliente.get("/api/agora/partes").get_json()["motivo"]


def test_sem_detetive_mostra_o_sempre_ligado_e_o_resto(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 20)
    relogio.t = agora
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    noites(plataforma, mid, agora)
    ao_vivo(plataforma, mid, 900)
    r = cliente.get("/api/agora/partes").get_json()
    assert r["disponivel"] and r["fonte"] == "base" and r["total"] == 900 and r["unidade"] == "W"
    assert [(p["tipo"], p["watts"]) for p in r["partes"]] == [("base", 120), ("resto", 780)]
    assert r["custo_por_hora"] == pytest.approx(0.9) and r["partes"][1]["custo_por_hora"] == pytest.approx(0.78)


def aparelho(plataforma, mid, potencia, n=4, nome=None):
    db = plataforma.db()
    with db:
        aid = db.execute("INSERT INTO aparelhos (medidor_id, nome, potencia_w, n_ciclos, criado_em) VALUES (?, ?, ?, ?, ?)",
                         (mid, nome, potencia, n, plataforma.agora() - DIA)).lastrowid
        db.executemany("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, ?, ?)",
                       [(mid, aid, plataforma.agora() - DIA + k * 3600, plataforma.agora() - DIA + k * 3600 + 480, potencia, potencia * 480 / 3600)
                        for k in range(n)])
    return aid


def degrau(plataforma, mid, delta, ha_s):
    db = plataforma.db()
    with db:
        db.execute("INSERT INTO degraus_abertos (medidor_id, instante, delta_w) VALUES (?, ?, ?)", (mid, plataforma.agora() - ha_s, delta))


def test_com_detetive_mostra_os_aparelhos_ligados(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 20)
    relogio.t = agora
    conta_pronta(cliente, plataforma, aparelhos=True)
    mid = parear(cliente, plataforma)
    noites(plataforma, mid, agora)
    chuveiro = aparelho(plataforma, mid, 5400)
    geladeira = aparelho(plataforma, mid, 140, nome="Geladeira")
    aparelho(plataforma, mid, 1300)                     # micro-ondas: conhecido, mas não está ligado
    degrau(plataforma, mid, 145, 600)                   # a geladeira ligou há 10 minutos
    degrau(plataforma, mid, 5380, 120)                  # o chuveiro, há 2 minutos
    degrau(plataforma, mid, 700, 60)                    # algo que o Detetive ainda não conhece
    ao_vivo(plataforma, mid, 6400)
    r = cliente.get("/api/agora/partes").get_json()
    assert r["fonte"] == "detetive"
    partes = {p.get("id", p["tipo"]): p for p in r["partes"]}
    assert partes[chuveiro]["nome"] == "Aparelho de 5.400 W" and "chuveiro" in partes[chuveiro]["palpite"]
    assert partes[chuveiro]["watts"] == pytest.approx(5380)
    assert partes[geladeira]["nome"] == "Geladeira" and partes[geladeira]["palpite"] is None
    assert partes["base"]["watts"] == 120
    assert partes["resto"]["watts"] == pytest.approx(6400 - 5380 - 145 - 120)      # inclui o desconhecido
    assert [p["tipo"] for p in r["partes"]] == ["aparelho", "aparelho", "base", "resto"]


def test_degrau_que_nao_cabe_ja_desligou(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 20)
    relogio.t = agora
    conta_pronta(cliente, plataforma, aparelhos=True)
    mid = parear(cliente, plataforma)
    noites(plataforma, mid, agora)
    aparelho(plataforma, mid, 5400)
    degrau(plataforma, mid, 5400, 3 * 3600)             # o desligar se perdeu: a casa gasta 300 W, não cabe
    ao_vivo(plataforma, mid, 300)
    r = cliente.get("/api/agora/partes").get_json()
    assert [(p["tipo"], p["watts"]) for p in r["partes"]] == [("base", 120), ("resto", 180)]


def test_sem_autorizacao_do_detetive_nao_mostra_aparelhos(cliente, plataforma, relogio):
    agora = em(2026, 11, 19, 20)
    relogio.t = agora
    conta_pronta(cliente, plataforma)                   # sem o Detetive
    mid = parear(cliente, plataforma)
    aparelho(plataforma, mid, 5400)
    degrau(plataforma, mid, 5400, 60)
    ao_vivo(plataforma, mid, 5600)
    r = cliente.get("/api/agora/partes").get_json()
    assert r["fonte"] == "base" and [p["tipo"] for p in r["partes"]] == ["resto"]     # sem noites: nem o sempre ligado


def test_circuitos_e_balanco(cliente, plataforma, relogio):
    relogio.t = em(2026, 11, 19, 20)
    conta_pronta(cliente, plataforma)
    casa_id = parear(cliente, plataforma, "mw-casa01")
    cozinha = parear(cliente, plataforma, "mw-coz001")
    cliente.patch(f"/api/medidores/{cozinha}", {"apelido": "Cozinha", "papel": "circuito"})
    ao_vivo(plataforma, casa_id, 1000)
    ao_vivo(plataforma, cozinha, 300)
    r = cliente.get("/api/agora/partes").get_json()
    assert r["fonte"] == "circuitos" and r["total"] == 1000 and r["aviso"] is None
    assert [(p["nome"], p["watts"]) for p in r["partes"]] == [("Cozinha", 300), ("Resto da casa", 700)]
    relogio.avancar(2)
    ao_vivo(plataforma, casa_id, 1000, 1001)
    ao_vivo(plataforma, cozinha, 1500, 1001)            # o circuito passou da entrada: algo está errado na instalação
    r = cliente.get("/api/agora/partes").get_json()
    assert r["aviso"] and r["partes"][-1]["watts"] == 0


def test_dois_imoveis(cliente, plataforma, relogio):
    relogio.t = em(2026, 11, 19, 20)
    conta_pronta(cliente, plataforma)
    casa_id = parear(cliente, plataforma, "mw-casa01")
    edicula = parear(cliente, plataforma, "mw-edi001")
    ao_vivo(plataforma, casa_id, 1000)
    ao_vivo(plataforma, edicula, 250)
    r = cliente.get("/api/agora/partes").get_json()
    assert r["fonte"] == "imoveis" and r["total"] == 1250
    assert sorted(p["watts"] for p in r["partes"]) == [250, 1000]
