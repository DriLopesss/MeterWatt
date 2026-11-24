"""A conta de luz mais perto da real: Tarifa Branca (/api/economia/tarifa-branca), bandeira do mês e o mínimo
da conta na meta do mês."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import conta_pronta, parear, preencher_baldes

from meterwatt import tarifas
from meterwatt.__main__ import main

ZONA = ZoneInfo("America/Sao_Paulo")
DIA = 86400
AGORA = datetime(2026, 11, 23, 12, 0, tzinfo=ZONA).timestamp()      # segunda-feira
UTEIS_MES, OUTROS_MES = tarifas.dias_por_mes(date(2026, 11, 23))
PRECOS = {"convencional": 0.90, "fora": 0.75, "intermediario": 1.10, "ponta": 1.73}


def local(t):
    return datetime.fromtimestamp(t, ZONA)


def rotina(hora_do_banho=19):
    """Dia com ponta: 200 W o dia todo e o chuveiro (2 kW a mais) numa hora. Os outros dias: 500 W a mais das 10h às 12h."""
    def watts(t):
        d = local(t)
        if tarifas.tem_ponta(d.date()):
            return 2200 if d.hour == hora_do_banho else 200
        return 700 if 10 <= d.hour < 12 else 200
    return watts


@pytest.fixture
def ambiente(tmp_path):
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00"}


def casa(cliente, plataforma, relogio, dias=28, watts=None, **aceites):
    relogio.t = AGORA
    conta_pronta(cliente, plataforma, **aceites)
    mid = parear(cliente, plataforma, "mw-casa01")
    preencher_baldes(plataforma, mid, plataforma.inicio_do_dia(AGORA, dias), AGORA, watts or rotina())
    return mid


# ------------------------------------------------------------ calendário

def test_pascoa_e_dias_sem_ponta():
    assert [tarifas._pascoa(a) for a in (2024, 2025, 2026, 2027)] == [
        date(2024, 3, 31), date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28)]
    feriados = tarifas.dias_sem_ponta(2026)
    assert date(2026, 2, 17) in feriados              # terça de Carnaval
    assert date(2026, 4, 3) in feriados               # sexta-feira da Paixão
    assert date(2026, 6, 4) in feriados               # Corpus Christi
    assert date(2026, 11, 2) in feriados and date(2026, 12, 25) in feriados
    assert date(2026, 11, 20) not in feriados         # fora da lista da REN 1.000
    assert not tarifas.tem_ponta(date(2026, 11, 21)) and not tarifas.tem_ponta(date(2026, 11, 2))
    assert tarifas.tem_ponta(date(2026, 11, 23))
    uteis, outros = tarifas.dias_por_mes(date(2026, 11, 23))
    assert uteis + outros == pytest.approx(365 / 12) and 20.5 < uteis < 21.5


def test_postos_de_meia_em_meia_hora():
    p = lambda h, m=0: tarifas.posto(h * 60 + m, 17 * 60 + 30)           # ponta das 17h30 às 20h30
    assert [p(16, 29), p(16, 30), p(17, 29), p(17, 30), p(20, 29), p(20, 30), p(21, 29), p(21, 30)] == [
        "fora", "intermediario", "intermediario", "ponta", "ponta", "intermediario", "intermediario", "fora"]


# ------------------------------------------------------------ Tarifa Branca

def test_sem_medidor_e_poucos_dias(cliente, plataforma, relogio):
    relogio.t = AGORA
    conta_pronta(cliente, plataforma)
    r = cliente.get("/api/economia/tarifa-branca").get_json()
    assert r["disponivel"] is False and r["motivo"] == "Nenhum medidor pareado."
    assert r["ponta"] == {"inicio": "18:00", "fim": "21:00", "exemplo": True}
    assert r["intermediario"] == [{"inicio": "17:00", "fim": "18:00"}, {"inicio": "21:00", "fim": "22:00"}]
    mid = parear(cliente, plataforma, "mw-casa01")
    preencher_baldes(plataforma, mid, plataforma.inicio_do_dia(AGORA, 4), AGORA, rotina())
    r = cliente.get("/api/economia/tarifa-branca").get_json()
    assert r["disponivel"] is False and r["dias_medidos"] == 4 and "pelo menos 5 dias" in r["motivo"]


def test_divide_o_mes_pelos_postos(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    r = cliente.get("/api/economia/tarifa-branca").get_json()
    assert r["disponivel"] is True and r["dias_medidos"] == 28
    assert r["dias"] == {"uteis": 19, "outros": 9}        # 26/10 a 22/11: 8 dias de fim de semana e o 2 de novembro
    util = r["perfil"]["dia_util"]
    assert len(util) == 48 and util[0] == pytest.approx(0.1) and util[38] == pytest.approx(1.1)   # 19h às 19h30, banho
    assert r["perfil"]["fim_de_semana"][20] == pytest.approx(0.35)
    kwh = r["mes"]["kwh"]
    assert kwh["ponta"] == pytest.approx(2.6 * UTEIS_MES, abs=0.05)                # 18h às 21h: 3 h × 0,2 + o banho
    assert kwh["intermediario"] == pytest.approx(0.4 * UTEIS_MES, abs=0.05)
    assert kwh["fora"] == pytest.approx(3.8 * UTEIS_MES + 5.8 * OUTROS_MES, abs=0.05)
    assert kwh["total"] == pytest.approx(6.8 * UTEIS_MES + 5.8 * OUTROS_MES, abs=0.05)
    assert sum(r["mes"]["percentual"].values()) == pytest.approx(1, abs=0.002)
    assert r["custo"] is None and r["empate"] is None and r["aparelhos_na_ponta"] == []


def test_branca_mais_cara_e_quanto_tirar_da_ponta(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    assert cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.17}).status_code == 200
    r = cliente.put("/api/conta/tarifa-branca", {"ponta_inicio": "18:00", **PRECOS})
    assert r.status_code == 200, r.get_json()
    r = r.get_json()
    assert r["ponta"]["exemplo"] is False and r["fator"] == pytest.approx(1.3)
    k = {"fora": 3.8 * UTEIS_MES + 5.8 * OUTROS_MES, "intermediario": 0.4 * UTEIS_MES, "ponta": 2.6 * UTEIS_MES}
    convencional = sum(k.values()) * 0.90 * 1.3
    branca = (k["fora"] * 0.75 + k["intermediario"] * 1.10 + k["ponta"] * 1.73) * 1.3
    assert r["custo"]["convencional"] == pytest.approx(convencional, abs=0.05)
    assert r["custo"]["branca"] == pytest.approx(branca, abs=0.05)
    assert r["custo"]["diferenca"] == pytest.approx(branca - convencional, abs=0.05) and branca > convencional
    passar = (branca - convencional) / (1.3 * (1.73 - 0.75))
    assert r["empate"]["kwh_mes"] == pytest.approx(passar, abs=0.1)
    assert r["empate"]["kwh_por_dia_util"] == pytest.approx(passar / UTEIS_MES, abs=0.01)
    assert r["empate"]["cabe_na_ponta"] is True
    # A atividade da conta registra a mudança; a cópia dos dados leva o horário e os preços.
    atividade = cliente.get("/api/privacidade/atividade").get_json()
    assert any(a["descricao"] == "Horário e preços da Tarifa Branca alterados" for a in atividade["atividade"])
    copia = cliente.post("/api/privacidade/exportar").get_json()
    assert copia["conta"]["tarifa_branca"] == {"ponta_inicio": "18:00", "convencional": 0.9, "fora_de_ponta": 0.75,
                                               "intermediario": 1.1, "ponta": 1.73}


def test_banho_depois_da_ponta_faz_a_branca_compensar(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio, watts=rotina(hora_do_banho=22))
    cliente.put("/api/conta/tarifa-branca", {"ponta_inicio": "18:00", **PRECOS})
    r = cliente.get("/api/economia/tarifa-branca").get_json()
    assert r["fator"] == 1.0                              # sem a tarifa da conta, os preços da tabela valem como estão
    assert r["custo"]["diferenca"] < 0 and r["empate"] is None
    assert r["mes"]["kwh"]["ponta"] == pytest.approx(0.6 * UTEIS_MES, abs=0.05)


def test_ponta_das_17h30(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    r = cliente.put("/api/conta/tarifa-branca", {"ponta_inicio": "17:30"}).get_json()
    assert r["ponta"] == {"inicio": "17:30", "fim": "20:30", "exemplo": False}
    assert r["intermediario"] == [{"inicio": "16:30", "fim": "17:30"}, {"inicio": "20:30", "fim": "21:30"}]
    assert r["postos"][35] == "ponta" and r["postos"][34] == "intermediario" and r["postos"][41] == "intermediario"
    assert r["mes"]["kwh"]["ponta"] == pytest.approx(2.6 * UTEIS_MES, abs=0.05)   # o banho das 19h continua na ponta


def test_aparelhos_que_pesam_na_ponta(cliente, plataforma, relogio):
    mid = casa(cliente, plataforma, relogio, aparelhos=True)
    db = plataforma.db()
    with db:
        chuveiro = db.execute("INSERT INTO aparelhos (medidor_id, potencia_w, n_ciclos, criado_em) VALUES (?, 5400, 40, ?)",
                              (mid, AGORA)).lastrowid
        tv = db.execute("INSERT INTO aparelhos (medidor_id, nome, potencia_w, n_ciclos, criado_em) VALUES (?, 'TV da sala', 120, 40, ?)",
                        (mid, AGORA)).lastrowid
        for n in range(1, 29):
            inicio = plataforma.inicio_do_dia(AGORA, n)
            if not tarifas.tem_ponta(local(inicio).date()):
                continue
            banho = inicio + 19 * 3600
            db.execute("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, 5400, 900)",
                       (mid, chuveiro, banho, banho + 600))
            tele = inicio + 20 * 3600 + 30 * 60                          # 20h30 às 22h30: só meia hora na ponta
            db.execute("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, 120, 240)",
                       (mid, tv, tele, tele + 7200))
    r = cliente.get("/api/economia/tarifa-branca").get_json()
    na_ponta = r["aparelhos_na_ponta"]
    assert [a["nome"] for a in na_ponta] == ["Aparelho de 5.400 W", "TV da sala"]
    assert na_ponta[0]["kwh_mes"] == pytest.approx(5.4 / 6 * UTEIS_MES, abs=0.1)
    assert na_ponta[0]["palpite"] == "chuveiro elétrico ou torneira elétrica" and na_ponta[1]["palpite"] is None
    assert na_ponta[1]["kwh_mes"] == pytest.approx(0.12 / 2 * UTEIS_MES, abs=0.1)


def test_aparelhos_na_ponta_so_com_a_autorizacao(cliente, plataforma, relogio):
    mid = casa(cliente, plataforma, relogio)
    db = plataforma.db()
    with db:
        a = db.execute("INSERT INTO aparelhos (medidor_id, potencia_w, n_ciclos, criado_em) VALUES (?, 5400, 40, ?)", (mid, AGORA)).lastrowid
        banho = plataforma.inicio_do_dia(AGORA, 1) - 4 * DIA + 19 * 3600              # quinta, 19/11
        db.execute("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, 5400, 900)",
                   (mid, a, banho, banho + 600))
    assert cliente.get("/api/economia/tarifa-branca").get_json()["aparelhos_na_ponta"] == []


@pytest.mark.parametrize("corpo, trecho", [
    ({"ponta_inicio": "14:00"}, "15:00 a 20:00"),
    ({"ponta_inicio": "18:15"}, "meia em meia hora"),
    ({"ponta_inicio": "18h"}, "15:00 a 20:00"),
    ({"convencional": 0.9, "fora": 0.75}, "quatro preços"),
    ({**PRECOS, "fora": 0}, "maior que 0"),
    ({**PRECOS, "ponta": "1,73"}, "maior que 0"),
    ({**PRECOS, "ponta": 0.5}, "trocaram de lugar"),
])
def test_validacao(cliente, plataforma, relogio, corpo, trecho):
    relogio.t = AGORA
    conta_pronta(cliente, plataforma)
    r = cliente.put("/api/conta/tarifa-branca", corpo)
    assert r.status_code == 400 and trecho in r.get_json()["erro"]


def test_limpar_os_precos(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio)
    cliente.put("/api/conta/tarifa-branca", {"ponta_inicio": "19:00", **PRECOS})
    r = cliente.put("/api/conta/tarifa-branca", {}).get_json()
    assert r["precos"] is None and r["custo"] is None and r["ponta"]["exemplo"] is True


# ------------------------------------------------------------ bandeira e mínimo na previsão da conta

def test_meta_com_bandeira_do_mes(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio, dias=10)
    meta = cliente.get("/api/meta").get_json()
    assert meta["bandeira"]["mes"] == "2026-11" and meta["bandeira"]["informada"] is False
    assert meta["bandeira"]["adicional"] == 0 and meta["minimo"] is None
    sem_bandeira = meta["projecao"]
    assert plataforma.tarifas.registrar_bandeira("2026-11", "amarela")["reais_por_100_kwh"] == 1.885
    meta = cliente.get("/api/meta").get_json()
    assert meta["bandeira"] == {"mes": "2026-11", "cor": "amarela", "nome": "amarela", "informada": True,
                                "reais_por_100_kwh": 1.885, "adicional": pytest.approx(meta["kwh_previsto"] * 0.01885, abs=0.01)}
    assert meta["projecao"] == pytest.approx(sem_bandeira + meta["bandeira"]["adicional"], abs=0.02)
    plataforma.tarifas.registrar_bandeira("2026-11", "verde")                 # a ANEEL corrigiu: vale a última
    assert cliente.get("/api/meta").get_json()["bandeira"]["cor"] == "verde"
    assert plataforma.tarifas.bandeira_pendente() is None


def test_minimo_da_ligacao(cliente, plataforma, relogio):
    casa(cliente, plataforma, relogio, dias=10, watts=lambda t: 50)            # 1,2 kWh por dia
    assert cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "ligacao": "trifasica"}).status_code == 200
    assert cliente.get("/api/conta/eu").get_json()["ligacao"] == "trifasica"
    meta = cliente.get("/api/meta").get_json()
    assert meta["kwh_previsto"] < 100 and meta["minimo"] == {"kwh": 100, "aplicado": True}
    assert meta["projecao"] == pytest.approx(100.0)
    # Sem mandar a ligação (versão antiga do app), ela continua como estava.
    assert cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0}).status_code == 200
    assert cliente.get("/api/conta/eu").get_json()["ligacao"] == "trifasica"
    assert cliente.put("/api/conta/preferencias", {"ligacao": "bifasica-3"}).status_code == 400
    assert cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "ligacao": None}).status_code == 200
    assert cliente.get("/api/meta").get_json()["minimo"] is None


def test_registrar_bandeira_pela_linha_de_comando(ambiente, monkeypatch, capsys):
    for k, v in ambiente.items():
        monkeypatch.setenv(k, v)
    assert main(["--bandeira", "2026-12", "vermelha1"]) == 0
    assert "vermelha patamar 1 (R$ 4,463 a cada 100 kWh" in capsys.readouterr().out
    assert main(["--bandeiras"]) == 0
    assert "2026-12  vermelha1" in capsys.readouterr().out
    assert main(["--bandeira", "12/2026", "amarela"]) == 2
    assert main(["--bandeira", "2026-12", "roxa"]) == 2
