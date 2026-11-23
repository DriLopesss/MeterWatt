"""O que cada medidor mede: a entrada (soma no total) ou um circuito dentro da entrada (não soma de novo)."""
import json

import pytest
from conftest import INICIO, conta_pronta, parear, preencher_baldes

DIA = 86400


@pytest.fixture
def ambiente(tmp_path):
    # Vários medidores sem precisar assinar: o plano não é o assunto aqui.
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00", "MW_LIMITE_MEDIDORES_GRATIS": "5"}


def casa_e_cozinha(cliente, plataforma, relogio, dias=9):
    """Entrada de 300 W constantes e um circuito (cozinha) de 100 W, que já passa pela entrada."""
    relogio.t = INICIO + dias * DIA
    conta_pronta(cliente, plataforma)
    casa = parear(cliente, plataforma, "mw-casa01")
    cozinha = parear(cliente, plataforma, "mw-coz001")
    preencher_baldes(plataforma, casa, relogio.t - dias * DIA, relogio.t, 300)
    preencher_baldes(plataforma, cozinha, relogio.t - dias * DIA, relogio.t, 100)
    return casa, cozinha


def papel(cliente, mid, papel, dentro_de=None):
    dados = {"papel": papel} if dentro_de is None else {"papel": papel, "dentro_de": dentro_de}
    return cliente.patch(f"/api/medidores/{mid}", dados)


def medidores(cliente):
    return {m["id"]: m for m in cliente.get("/api/medidores").get_json()["medidores"]}


def test_medidor_novo_mede_a_entrada(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    m = medidores(cliente)[mid]
    assert m["papel"] == "casa" and m["dentro_de"] is None


def test_circuito_nao_soma_de_novo_no_total(cliente, plataforma, relogio):
    casa, cozinha = casa_e_cozinha(cliente, plataforma, relogio)
    ontem = cliente.get("/api/consumo/dias?dias=2").get_json()["dias"][0]["kwh"]
    assert ontem == pytest.approx(9.6)                      # as duas como entrada: 300 W + 100 W, 24 h
    fantasma = cliente.get("/api/fantasma").get_json()["watts"]
    assert fantasma == pytest.approx(400)

    r = papel(cliente, cozinha, "circuito")
    assert r.status_code == 200, r.get_json()
    m = medidores(cliente)
    assert m[cozinha]["papel"] == "circuito" and m[cozinha]["dentro_de"] == casa   # a única entrada, sem precisar dizer
    assert cliente.get("/api/consumo/dias?dias=2").get_json()["dias"][0]["kwh"] == pytest.approx(7.2)
    f = cliente.get("/api/fantasma").get_json()
    assert f["watts"] == pytest.approx(300)
    assert {x["id"]: x["watts"] for x in f["medidores"]} == {casa: 300, cozinha: 100}   # cada um continua com o seu
    meta = cliente.get("/api/meta").get_json()
    assert meta["media_diaria"] == pytest.approx(7.2)        # R$ 1,00 por kWh
    economia = cliente.get("/api/economia").get_json()
    assert economia["consumo_kwh_dia"] == pytest.approx(7.2, rel=0.02)

    # e volta a somar quando deixa de ser circuito
    assert papel(cliente, cozinha, "casa").status_code == 200
    assert medidores(cliente)[cozinha]["dentro_de"] is None
    assert cliente.get("/api/consumo/dias?dias=2").get_json()["dias"][0]["kwh"] == pytest.approx(9.6)


def test_aviso_de_consumo_fora_do_normal_ignora_o_circuito(cliente, plataforma, relogio):
    casa, cozinha = casa_e_cozinha(cliente, plataforma, relogio)
    papel(cliente, cozinha, "circuito")
    # Na última hora, só a cozinha disparou (3 kW). A entrada, que de verdade já contém a cozinha, não mudou
    # nos dados deste teste: o aviso olha só para a entrada e não vê nada fora do normal.
    fim = int(relogio.t // 3600 * 3600)
    preencher_baldes(plataforma, cozinha, fim - 3600, fim, 3000)
    assert plataforma.alertas._incomum(plataforma.db(), 1) == 0
    papel(cliente, cozinha, "casa")
    assert plataforma.alertas._incomum(plataforma.db(), 1) == 1


@pytest.mark.parametrize("dados, codigo", [
    ({"papel": "garagem"}, None),
    ({"papel": "circuito", "dentro_de": "mw-coz001"}, "dentro_de"),      # dentro dele mesmo
    ({"papel": "circuito", "dentro_de": "mw-de-outra"}, "dentro_de"),
    ({}, None),
])
def test_papel_invalido(cliente, plataforma, relogio, dados, codigo):
    casa_e_cozinha(cliente, plataforma, relogio)
    r = cliente.patch("/api/medidores/mw-coz001", dados)
    assert r.status_code == 400 and r.get_json()["codigo"] == codigo


def test_regras_de_circuito(cliente, plataforma, relogio):
    casa, cozinha = casa_e_cozinha(cliente, plataforma, relogio)
    edicula = parear(cliente, plataforma, "mw-edi001")
    # Com duas entradas, é preciso dizer onde o circuito fica.
    r = papel(cliente, cozinha, "circuito")
    assert r.status_code == 400 and r.get_json()["codigo"] == "dentro_de"
    assert papel(cliente, cozinha, "circuito", casa).status_code == 200
    # Circuito dentro de circuito, não: só dentro de uma entrada.
    r = papel(cliente, edicula, "circuito", cozinha)
    assert r.status_code == 400 and r.get_json()["codigo"] == "dentro_de"
    # Uma entrada com circuitos dentro não vira circuito.
    r = papel(cliente, casa, "circuito", edicula)
    assert r.status_code == 400 and r.get_json()["codigo"] == "tem_circuitos"
    # A conta nunca fica sem entrada.
    assert papel(cliente, edicula, "circuito", casa).status_code == 200
    r = papel(cliente, casa, "circuito")
    assert r.status_code == 400 and r.get_json()["codigo"] in ("sem_entrada", "tem_circuitos")
    atividade = cliente.get("/api/privacidade/atividade").get_json()["atividade"]
    assert any(a["descricao"] == "O que o medidor mede mudou" and a["detalhe"] == f"{cozinha}: circuito" for a in atividade)


def test_nome_e_papel_no_mesmo_pedido(cliente, plataforma, relogio):
    casa, cozinha = casa_e_cozinha(cliente, plataforma, relogio)
    r = cliente.patch(f"/api/medidores/{cozinha}", {"apelido": "Cozinha", "papel": "circuito"})
    assert r.status_code == 200
    m = medidores(cliente)[cozinha]
    assert m["apelido"] == "Cozinha" and m["papel"] == "circuito"
    # Nome inválido: nada muda, nem o papel.
    r = cliente.patch(f"/api/medidores/{cozinha}", {"apelido": "", "papel": "casa"})
    assert r.status_code == 400 and medidores(cliente)[cozinha]["papel"] == "circuito"


def test_remover_a_entrada(cliente, plataforma, relogio):
    casa, cozinha = casa_e_cozinha(cliente, plataforma, relogio)
    edicula = parear(cliente, plataforma, "mw-edi001")
    papel(cliente, cozinha, "circuito", casa)
    # Saiu a entrada da cozinha: sobrou uma entrada só, e o circuito passa a ficar nela.
    assert cliente.delete(f"/api/medidores/{casa}").status_code == 200
    assert medidores(cliente)[cozinha]["dentro_de"] == edicula
    # Saiu a última entrada: quem sobrou passa a ser entrada (senão nada somaria).
    assert cliente.delete(f"/api/medidores/{edicula}").status_code == 200
    m = medidores(cliente)[cozinha]
    assert m["papel"] == "casa" and m["dentro_de"] is None


def test_isolamento_e_exportacao(cliente, plataforma, relogio, novo_cliente):
    casa, cozinha = casa_e_cozinha(cliente, plataforma, relogio)
    papel(cliente, cozinha, "circuito")
    outra = novo_cliente()
    conta_pronta(outra, plataforma, email="bia@exemplo.com.br")
    parear(outra, plataforma, "mw-bia001")
    r = papel(outra, "mw-bia001", "circuito", casa)                # a entrada de outra conta não existe para ela
    assert r.status_code == 400 and r.get_json()["codigo"] == "sem_entrada"
    assert outra.patch(f"/api/medidores/{cozinha}", {"papel": "casa"}).status_code == 404
    dados = json.loads(cliente.post("/api/privacidade/exportar").get_data(as_text=True))
    exportados = {m["id"]: (m["mede"], m["dentro_de"]) for m in dados["medidores"]}
    assert exportados == {casa: ("entrada", None), cozinha: ("circuito", casa)}
