"""E se eu…?: contas, dados da casa, plano de economia, conferência "deu certo?" e privacidade."""
import json
import os
from datetime import datetime

import pytest

from conftest import INICIO, SENHA, conta_pronta, parear, preencher_baldes
from meterwatt import economia
from meterwatt.economia import ParametroInvalido, kwh_mes, meses_para_pagar, total_kwh_mes, validar

VETORES = json.load(open(os.path.join(os.path.dirname(__file__), "..", "economia_vetores.json"), encoding="utf-8"))
DIA = 86400


# ------------------------------------------------------------ contas (os mesmos casos do JavaScript)

@pytest.mark.parametrize("caso", VETORES["acoes"], ids=lambda c: c["nome"])
def test_acoes_conferem_com_os_vetores(caso):
    if "erro_campo" in caso:
        with pytest.raises(ParametroInvalido) as e:
            validar(caso["tipo"], caso["parametros"])
        assert e.value.campo == caso["erro_campo"]
        return
    p = validar(caso["tipo"], caso["parametros"])
    assert "qualquer" not in p
    assert kwh_mes(caso["tipo"], p) == pytest.approx(caso["kwh_mes"], abs=1e-6)


@pytest.mark.parametrize("caso", VETORES["totais"], ids=lambda c: c["nome"])
def test_totais_conferem_com_os_vetores(caso):
    acoes = [{"tipo": a["tipo"], "parametros": validar(a["tipo"], a["parametros"])} for a in caso["acoes"]]
    assert total_kwh_mes(acoes) == pytest.approx(caso["kwh_mes"], abs=1e-6)


@pytest.mark.parametrize("caso", VETORES["meses_para_pagar"], ids=lambda c: c["nome"])
def test_meses_para_pagar(caso):
    m = meses_para_pagar(caso["preco"], caso["kwh_mes"], caso["tarifa"])
    assert m is None if caso["meses"] is None else m == pytest.approx(caso["meses"], abs=1e-6)


def test_javascript_e_python_tem_os_mesmos_limites():
    texto = open(os.path.join(os.path.dirname(__file__), "..", "..", "src", "meterwatt", "estatico", "economia-calculo.js"),
                 encoding="utf-8").read()
    for tipo, campos in economia.CAMPOS.items():
        for campo, (minimo, maximo) in campos.items():
            assert f"{campo}: [{minimo}, {maximo}]" in texto, f"{tipo}.{campo} diferente no JavaScript"


# ------------------------------------------------------------ apoio

def aparelho(plataforma, mid, potencia, inicios, duracao_s, nome=None):
    """Grava um aparelho do Detetive com ciclos prontos (sem precisar simular segundos)."""
    db = plataforma.db()
    with db:
        aid = db.execute("INSERT INTO aparelhos (medidor_id, nome, potencia_w, n_ciclos, criado_em) VALUES (?, ?, ?, ?, ?)",
                         (mid, nome, potencia, len(inicios), inicios[0])).lastrowid
        db.executemany("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, ?, ?)",
                       [(mid, aid, t, t + duracao_s, potencia, potencia * duracao_s / 3600) for t in inicios])
    return aid


def casa_medida(cliente, plataforma, relogio, detetive=True):
    """Conta com medidor, 9 dias de consumo, e o Detetive com chuveiro, geladeira, TV e micro-ondas."""
    relogio.t = INICIO + 9 * DIA
    conta_pronta(cliente, plataforma)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0})
    mid = parear(cliente, plataforma)
    plataforma.db().execute("UPDATE medidores SET tipo = 'pzem'").connection.commit()
    preencher_baldes(plataforma, mid, relogio.t - 9 * DIA, relogio.t, 250)
    if detetive:
        cliente.put("/api/privacidade/aparelhos", {"aceito": True})
        semana = relogio.t - 7 * DIA + 3600
        ids = {
            "chuveiro": aparelho(plataforma, mid, 5400, [semana + k * 12 * 3600 for k in range(14)], 540),
            "geladeira": aparelho(plataforma, mid, 140, [semana + k * 1800 for k in range(336)], 720),
            "tv": aparelho(plataforma, mid, 110, [semana + 18 * 3600 + k * DIA for k in range(7)], 4 * 3600),
            "micro": aparelho(plataforma, mid, 1300, [semana + 6 * 3600 + k * 12 * 3600 for k in range(14)], 180),
        }
        return mid, ids
    return mid, {}


# ------------------------------------------------------------ dados da casa

def test_sem_medidor_usa_valores_tipicos(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    e = cliente.get("/api/economia").get_json()
    assert e["chuveiro"]["fonte"] == "tipico" and e["chuveiro"]["potencia_w"] == 5500
    assert e["fantasma"]["fonte"] == "tipico" and e["geladeira"]["fonte"] == "tipico"
    assert e["consumo_kwh_dia"] is None and e["aparelhos"] == [] and e["plano"] == [] and e["verificacao"] is None
    assert e["tarifa_kwh"] == 1.0                      # tarifa padrão do servidor de teste


def test_com_medicao_e_detetive_usa_a_casa(cliente, plataforma, relogio):
    _, ids = casa_medida(cliente, plataforma, relogio)
    e = cliente.get("/api/economia").get_json()
    assert e["consumo_kwh_dia"] == pytest.approx(6.0)          # 250 W o dia inteiro
    assert e["fantasma"] == {"watts": 250.0, "fonte": "medido"}
    ch = e["chuveiro"]
    assert ch["fonte"] == "detetive" and ch["potencia_w"] == 5400 and ch["banhos_semana"] == 14 and ch["minutos_banho"] == 9.0
    gel = e["geladeira"]
    assert gel["fonte"] == "detetive" and gel["aparelho_id"] == ids["geladeira"]
    assert gel["kwh_mes"] == pytest.approx(140 * 0.2 * 48 * 365 / 12 / 1000, rel=0.02)
    # o chuveiro e a geladeira têm cartões próprios; o resto vai para "usar menos"
    por_id = {a["id"]: a for a in e["aparelhos"]}
    assert set(por_id) == {ids["tv"], ids["micro"]}
    assert por_id[ids["tv"]]["minutos_dia"] == 240.0 and por_id[ids["micro"]]["minutos_dia"] == 6.0
    assert por_id[ids["micro"]]["rotulo"] == "Aparelho de 1.300 W"


def test_chuveiro_com_nome_tem_preferencia(cliente, plataforma, relogio):
    mid, ids = casa_medida(cliente, plataforma, relogio)
    cliente.patch(f"/api/aparelhos/{ids['micro']}", {"nome": "Chuveiro do fundo"})
    assert cliente.get("/api/economia").get_json()["chuveiro"]["aparelho_id"] == ids["micro"]


def test_sem_autorizacao_de_consumo_nada_medido_entra(cliente, plataforma, relogio):
    casa_medida(cliente, plataforma, relogio)
    cliente.put("/api/privacidade/consumo", {"aceito": False})
    e = cliente.get("/api/economia").get_json()
    assert e["chuveiro"]["fonte"] == "tipico" and e["fantasma"]["fonte"] == "tipico" and e["consumo_kwh_dia"] is None


def test_detetive_desligado_usa_tipicos_so_nos_aparelhos(cliente, plataforma, relogio):
    casa_medida(cliente, plataforma, relogio, detetive=False)
    e = cliente.get("/api/economia").get_json()
    assert e["detetive_ligado"] is False and e["chuveiro"]["fonte"] == "tipico"
    assert e["fantasma"]["fonte"] == "medido" and e["consumo_kwh_dia"] == pytest.approx(6.0)


# ------------------------------------------------------------ plano

BANHO = {"tipo": "banho_curto", "parametros": {"minutos_a_menos": 2, "potencia_w": 5400, "banhos_semana": 14, "minutos_banho": 9}}
VERAO = {"tipo": "chuveiro_verao", "parametros": {"porcentagem": 50, "potencia_w": 5400, "potencia_verao_w": 3000,
                                                  "banhos_semana": 14, "minutos_banho": 9}}


def test_por_no_plano_atualizar_e_tirar(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    r = cliente.post("/api/economia/plano", BANHO)
    assert r.status_code == 200
    plano = r.get_json()["plano"]
    assert len(plano) == 1 and plano[0]["titulo"] == "Banho mais curto" and plano[0]["kwh_mes"] == pytest.approx(10.95)
    desde = plano[0]["desde"]
    # atualizar mantém a data de início
    relogio.avancar(DIA)
    r = cliente.post("/api/economia/plano", {**BANHO, "parametros": {**BANHO["parametros"], "minutos_a_menos": 3}})
    plano = r.get_json()["plano"]
    assert len(plano) == 1 and plano[0]["desde"] == desde and plano[0]["parametros"]["minutos_a_menos"] == 3
    # juntos, banho mais curto e posição verão não somam simplesmente
    total = cliente.post("/api/economia/plano", VERAO).get_json()["plano_total"]
    separado = sum(a["kwh_mes"] for a in cliente.get("/api/economia").get_json()["plano"])
    assert total["kwh_mes"] < separado and total["reais_mes"] == total["kwh_mes"]
    acao = plano[0]["id"]
    assert cliente.delete(f"/api/economia/plano/{acao}").status_code == 200
    assert [a["tipo"] for a in cliente.get("/api/economia").get_json()["plano"]] == ["chuveiro_verao"]
    eventos = [a["detalhe"] for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"] if a["evento"] == "plano_economia"]
    assert "Banho mais curto: fora do plano" in eventos and "Chuveiro na posição verão: no plano" in eventos


@pytest.mark.parametrize("dados, codigo", [
    ({"tipo": "banho_curto", "parametros": {**BANHO["parametros"], "minutos_a_menos": 9}}, "parametro_invalido"),
    ({"tipo": "banho_curto", "parametros": {**BANHO["parametros"], "potencia_w": "5400"}}, "parametro_invalido"),
    ({"tipo": "banho_curto", "parametros": {**BANHO["parametros"], "minutos_a_menos": 0}}, "sem_economia"),
    ({"tipo": "trocar_de_casa", "parametros": {}}, "parametro_invalido"),
    ({"tipo": "usar_menos", "parametros": {"potencia_w": 900, "minutos_dia": 60}}, "parametro_invalido"),   # sem nome nem aparelho
    ({"tipo": "usar_menos", "parametros": {"potencia_w": 900, "minutos_dia": 60}, "nome": "x" * 41}, "parametro_invalido"),
])
def test_plano_recusa_o_que_nao_faz_sentido(cliente, plataforma, dados, codigo):
    conta_pronta(cliente, plataforma)
    r = cliente.post("/api/economia/plano", dados)
    assert r.status_code == 400 and r.get_json()["codigo"] == codigo


def test_usar_menos_com_aparelho_detectado_e_outro(cliente, plataforma, relogio):
    _, ids = casa_medida(cliente, plataforma, relogio)
    r = cliente.post("/api/economia/plano", {"tipo": "usar_menos", "aparelho_id": ids["tv"], "parametros": {"potencia_w": 110, "minutos_dia": 60}})
    assert r.status_code == 200
    r = cliente.post("/api/economia/plano", {"tipo": "usar_menos", "nome": "Ar-condicionado", "parametros": {"potencia_w": 900, "minutos_dia": 60}})
    r = cliente.post("/api/economia/plano", {"tipo": "usar_menos", "nome": "  ar-condicionado ", "parametros": {"potencia_w": 900, "minutos_dia": 90}})
    plano = r.get_json()["plano"]
    # mesmo nome (sem diferença de maiúsculas e espaços): atualiza em vez de duplicar, com a grafia mais nova
    assert [(a["tipo"], a["nome"]) for a in plano] == [("usar_menos", "Aparelho de 110 W"), ("usar_menos", "ar-condicionado")]
    assert plano[1]["parametros"]["minutos_dia"] == 90


def test_isolamento_entre_contas(cliente, plataforma, relogio, novo_cliente):
    _, ids = casa_medida(cliente, plataforma, relogio)
    acao = cliente.post("/api/economia/plano", BANHO).get_json()["plano"][0]["id"]
    bia = conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    assert bia.get("/api/economia").get_json()["plano"] == []
    assert bia.delete(f"/api/economia/plano/{acao}").status_code == 404
    r = bia.post("/api/economia/plano", {"tipo": "usar_menos", "aparelho_id": ids["tv"], "parametros": {"potencia_w": 110, "minutos_dia": 60}})
    assert r.status_code == 404                               # o aparelho é da Ana
    assert len(cliente.get("/api/economia").get_json()["plano"]) == 1


def test_limite_de_acoes_no_plano(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    for i in range(economia.MAXIMO_NO_PLANO):
        assert cliente.post("/api/economia/plano", {"tipo": "usar_menos", "nome": f"Aparelho {i}",
                                                    "parametros": {"potencia_w": 100, "minutos_dia": 10}}).status_code == 200
    r = cliente.post("/api/economia/plano", {"tipo": "usar_menos", "nome": "Mais um", "parametros": {"potencia_w": 100, "minutos_dia": 10}})
    assert r.status_code == 400
    # atualizar um que já está no plano continua valendo
    assert cliente.post("/api/economia/plano", {"tipo": "usar_menos", "nome": "Aparelho 3",
                                                "parametros": {"potencia_w": 100, "minutos_dia": 20}}).status_code == 200


# ------------------------------------------------------------ deu certo?

def plano_desde(plataforma, dias_atras, relogio):
    db = plataforma.db()
    with db:
        db.execute("UPDATE economia_plano SET criada_em = ?", (relogio.t - dias_atras * DIA,))


def test_conferencia_espera_dias_suficientes(cliente, plataforma, relogio):
    mid, _ = casa_medida(cliente, plataforma, relogio)
    cliente.post("/api/economia/plano", BANHO)
    v = cliente.get("/api/economia").get_json()["verificacao"]
    assert v["pronto"] is False and v["dias_depois"] == 0 and v["minimo"] == 5 and v["total"] is None
    plano_desde(plataforma, 3, relogio)
    v = cliente.get("/api/economia").get_json()["verificacao"]
    assert v["pronto"] is False and v["dias_depois"] == 2


def test_conferencia_compara_antes_e_depois(cliente, plataforma, relogio):
    relogio.t = INICIO + 24 * DIA
    conta_pronta(cliente, plataforma)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0})
    cliente.put("/api/privacidade/aparelhos", {"aceito": True})
    mid = parear(cliente, plataforma)
    inicio_plano = plataforma.inicio_do_dia(relogio.t, 8) + 10 * 3600          # 8 dias atrás, 10h
    dia0 = plataforma.inicio_do_dia(inicio_plano)

    def perfil(depois):
        def watts(t):
            h = datetime.fromtimestamp(t, plataforma.cfg.zona).hour
            if h < 5:
                return 60 if depois else 90                                    # espera: 90 W antes, 60 W depois
            return 260 if depois else 310
        return watts
    preencher_baldes(plataforma, mid, dia0 - 15 * DIA, dia0, perfil(False))
    preencher_baldes(plataforma, mid, dia0, relogio.t, perfil(True))
    # chuveiro: 2 banhos de 9 min por dia antes; de 7 min depois
    banhos_antes = [dia0 - d * DIA + h * 3600 for d in range(1, 15) for h in (7, 19)]
    banhos_depois = [dia0 + d * DIA + h * 3600 for d in range(1, 8) for h in (7, 19)]
    aparelho(plataforma, mid, 5400, banhos_antes, 540)
    db = plataforma.db()
    aid = db.execute("SELECT id FROM aparelhos").fetchone()[0]
    with db:
        db.executemany("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, ?, ?)",
                       [(mid, aid, t, t + 420, 5400, 5400 * 420 / 3600) for t in banhos_depois])
    cliente.post("/api/economia/plano", BANHO)
    cliente.post("/api/economia/plano", {"tipo": "fantasma", "parametros": {"watts_cortados": 30, "horas_dia": 24}})
    db = plataforma.db()                  # cada pedido ao site fecha a conexão desta thread ao terminar
    with db:
        db.execute("UPDATE economia_plano SET criada_em = ?", (inicio_plano,))

    v = cliente.get("/api/economia").get_json()["verificacao"]
    assert v["pronto"] is True and v["dias_antes"] == 14 and v["dias_depois"] == 7
    antes = (90 * 5 + 310 * 19) / 1000                   # kWh por dia: 5 horas de madrugada e 19 de dia
    depois = (60 * 5 + 260 * 19) / 1000
    t = v["total"]
    assert t["antes_kwh_dia"] == pytest.approx(antes, abs=0.01) and t["depois_kwh_dia"] == pytest.approx(depois, abs=0.01)
    assert t["variacao"] == pytest.approx((depois - antes) / antes, abs=0.002)
    assert t["reais_mes"] == pytest.approx((antes - depois) * 365 / 12, abs=0.05)
    itens = {i["rotulo"]: i for i in v["itens"]}
    ch = itens["Chuveiro (verão e inverno)"]
    assert ch["antes"] == pytest.approx(1.62) and ch["depois"] == pytest.approx(1.26)
    esp = itens["Consumo em espera"]
    assert (esp["antes"], esp["depois"], esp["unidade"]) == (90, 60, "W")
    # o painel mostra o mesmo
    r = cliente.get("/api/economia/resumo").get_json()
    assert r["acoes"] == 2 and r["verificacao"]["total"] == t


def test_resumo_semanal_conta_como_foi_o_plano(cliente, plataforma, relogio):
    segunda = datetime(2026, 11, 30, 9, 0, tzinfo=plataforma.cfg.zona).timestamp()
    relogio.t = segunda
    conta_pronta(cliente, plataforma)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0})
    mid = parear(cliente, plataforma)
    inicio_plano = plataforma.inicio_do_dia(segunda, 10) + 12 * 3600
    dia0 = plataforma.inicio_do_dia(inicio_plano)
    preencher_baldes(plataforma, mid, dia0 - 15 * DIA, dia0, 300)
    preencher_baldes(plataforma, mid, dia0, segunda, 240)
    cliente.post("/api/economia/plano", BANHO)
    db = plataforma.db()
    with db:
        db.execute("UPDATE economia_plano SET criada_em = ?", (inicio_plano,))
        db.execute("UPDATE medidores SET ultima_mensagem = ?", (segunda,))
    plataforma.alertas.verificar()
    resumo = [a for a in cliente.get("/api/avisos").get_json()["avisos"] if a["tipo"] == "resumo_semanal"][0]["texto"]
    assert f"Plano de economia: desde {datetime.fromtimestamp(inicio_plano, plataforma.cfg.zona):%d/%m}, a casa gastou 20% menos por dia" in resumo
    assert "R$ 43,80 por mês" in resumo                    # 1,44 kWh por dia a menos x 30,4 dias x R$ 1,00


# ------------------------------------------------------------ privacidade

def test_exportar_apagar_consumo_e_excluir_conta(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    cliente.post("/api/economia/plano", BANHO)
    dados = json.loads(cliente.post("/api/privacidade/exportar").data)
    assert dados["plano_de_economia"][0]["acao"] == "Banho mais curto"
    assert dados["plano_de_economia"][0]["parametros"]["banhos_semana"] == 14
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    assert cliente.get("/api/economia").get_json()["plano"] == []
    cliente.post("/api/economia/plano", BANHO)
    assert cliente.post("/api/privacidade/excluir-conta", {"senha": SENHA}).status_code == 200
    assert plataforma.db().execute("SELECT COUNT(*) FROM economia_plano").fetchone()[0] == 0


def test_sem_autorizacao_de_consumo_nao_ha_conferencia(cliente, plataforma, relogio):
    casa_medida(cliente, plataforma, relogio)
    cliente.post("/api/economia/plano", BANHO)
    cliente.put("/api/privacidade/consumo", {"aceito": False})
    e = cliente.get("/api/economia").get_json()
    assert len(e["plano"]) == 1 and e["verificacao"] is None


def test_aviso_de_meta_aponta_para_o_simulador(cliente, plataforma, relogio):
    relogio.t = plataforma.inicio_do_mes(INICIO) + 10 * DIA
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": 50})
    preencher_baldes(plataforma, mid, plataforma.inicio_do_mes(relogio.t), relogio.t, 125)
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (relogio.t,)).connection.commit()
    plataforma.alertas.verificar()
    texto = cliente.get("/api/avisos").get_json()["avisos"][0]["texto"]
    assert "Na aba Economia dá para simular onde cortar." in texto
