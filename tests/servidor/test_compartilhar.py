"""Compartilhar a casa (/api/casa): convite por e-mail, só leitura, só o painel e o relatório, sem o Detetive."""
import pytest
from conftest import conta_pronta, leitura_pzem, parear, preencher_baldes

ANA, BIA, CAIO = "ana@exemplo.com.br", "bia@exemplo.com.br", "caio@exemplo.com.br"


@pytest.fixture
def casa(cliente, novo_cliente, plataforma, relogio):
    """Ana tem o medidor e convida a Bia. Devolve (ana, bia, id da Ana)."""
    conta_pronta(cliente, plataforma, aparelhos=True)
    mid = parear(cliente, plataforma, "mw-casa01")
    preencher_baldes(plataforma, mid, relogio.t - 3 * 86400, relogio.t, 300)
    assert plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(300, 10.0)) == "gravada"
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.1, "meta_mensal_reais": 150})
    bia = novo_cliente()
    conta_pronta(bia, plataforma, email=BIA)
    return cliente, bia


def convidar(plataforma, ana, email=BIA):
    r = ana.post("/api/casa/convites", {"email": email})
    assert r.status_code == 201, r.get_json()
    m = plataforma.mensageiro.ultimo_para(email)
    return m["texto"].split("/#compartilhar/")[1].split()[0]


def test_convite_aceito_e_so_leitura(casa, plataforma):
    ana, bia = casa
    token = convidar(plataforma, ana)
    assert ana.get("/api/casa").get_json()["convites"][0]["email"] == BIA
    r = bia.post("/api/casa/aceitar", {"token": token})
    assert r.status_code == 200 and r.get_json()["nome"] == "Ana"
    dono = r.get_json()["dono_id"]
    assert bia.get("/api/conta/eu").get_json()["casas"][0]["nome"] == "Ana"
    estado = ana.get("/api/casa").get_json()
    assert [p["email"] for p in estado["pessoas"]] == [BIA] and estado["convites"] == []
    ver = {"X-MW-Casa": str(dono)}
    # O que a Bia lê é o da Ana: medidores, meta (com a tarifa e a meta da Ana), consumo, relatório.
    assert [m["id"] for m in bia.get("/api/medidores", headers=ver).get_json()["medidores"]] == ["mw-casa01"]
    assert bia.get("/api/medidores").get_json()["medidores"] == []                      # sem o cabeçalho: a própria conta
    meta = bia.get("/api/meta", headers=ver).get_json()
    assert meta["meta"] == 150 and meta["tarifa_kwh"] == 1.1
    for url in ("/api/fantasma", "/api/consumo/dias?dias=14", "/api/consumo/horas?dias=14", "/api/agora/partes",
                "/api/relatorios", "/api/medidores/mw-casa01/historico?periodo=24h"):
        assert bia.get(url, headers=ver).status_code == 200, url
    # Nada além disso, e nada de mudar.
    for url in ("/api/aparelhos", "/api/economia", "/api/avisos", "/api/privacidade", "/api/conta/eu", "/api/economia/tarifa-branca"):
        r = bia.get(url, headers=ver)
        assert r.status_code == 403 and r.get_json()["codigo"] == "so_leitura", url
    r = bia.put("/api/conta/preferencias", {"tarifa_kwh": 9}, headers=ver)
    assert r.status_code == 403
    r = bia.patch("/api/medidores/mw-casa01", {"apelido": "x"}, headers=ver)
    assert r.status_code == 403
    assert ana.get("/api/conta/eu").get_json()["tarifa_kwh"] == 1.1
    # O Detetive da Ana não aparece para a Bia.
    assert bia.get("/api/agora/partes", headers=ver).get_json()["fonte"] == "base"


def test_cabecalho_de_casa_sem_acesso(casa, plataforma):
    _ana, bia = casa
    r = bia.get("/api/medidores", headers={"X-MW-Casa": "1"})
    assert r.status_code == 403 and r.get_json()["codigo"] == "sem_acesso"
    assert bia.get("/api/medidores", headers={"X-MW-Casa": "abc"}).status_code == 403


def test_convite_so_para_o_email_convidado(casa, novo_cliente, plataforma):
    ana, bia = casa
    token = convidar(plataforma, ana)
    caio = novo_cliente()
    conta_pronta(caio, plataforma, email=CAIO)
    r = caio.post("/api/casa/aceitar", {"token": token})
    assert r.status_code == 403 and r.get_json()["codigo"] == "outro_email"
    assert ana.post("/api/casa/aceitar", {"token": token}).status_code == 403
    assert bia.post("/api/casa/aceitar", {"token": "x" * 43}).get_json()["codigo"] == "convite_invalido"


def test_convite_vence_e_reenviar_troca_o_link(casa, plataforma, relogio):
    ana, bia = casa
    velho = convidar(plataforma, ana)
    novo = convidar(plataforma, ana)
    assert velho != novo and len(ana.get("/api/casa").get_json()["convites"]) == 1
    assert bia.post("/api/casa/aceitar", {"token": velho}).get_json()["codigo"] == "convite_invalido"
    plataforma.db().execute("UPDATE convites_casa SET expira_em = ?", (relogio.t - 1,))      # passaram os 7 dias
    plataforma.db().commit()
    assert bia.post("/api/casa/aceitar", {"token": novo}).get_json()["codigo"] == "convite_invalido"
    assert ana.get("/api/casa").get_json()["convites"] == []      # o e-mail da pessoa sai com o convite vencido


@pytest.mark.parametrize("email, trecho", [("sem-arroba", "e-mail válido"), (ANA, "seu próprio")])
def test_validacao(casa, plataforma, email, trecho):
    ana, _ = casa
    r = ana.post("/api/casa/convites", {"email": email})
    assert r.status_code == 400 and trecho in r.get_json()["erro"]


def test_limite_de_pessoas(casa, plataforma):
    ana, _ = casa
    for i in range(5):
        assert ana.post("/api/casa/convites", {"email": f"p{i}@exemplo.com.br"}).status_code == 201
    r = ana.post("/api/casa/convites", {"email": "p9@exemplo.com.br"})
    assert r.status_code == 400 and "até 5" in r.get_json()["erro"]
    cid = ana.get("/api/casa").get_json()["convites"][0]["id"]
    assert ana.delete(f"/api/casa/convites/{cid}").status_code == 200
    assert ana.post("/api/casa/convites", {"email": "p9@exemplo.com.br"}).status_code == 201


def test_tirar_acesso_sair_atividade_e_copia(casa, plataforma):
    ana, bia = casa
    dono = bia.post("/api/casa/aceitar", {"token": convidar(plataforma, ana)}).get_json()["dono_id"]
    bia_id = ana.get("/api/casa").get_json()["pessoas"][0]["id"]
    copia = ana.post("/api/privacidade/exportar").get_json()
    assert copia["compartilhamento"]["quem_ve_a_minha_casa"][0]["email"] == BIA
    assert bia.post("/api/privacidade/exportar").get_json()["compartilhamento"]["casas_que_eu_vejo"][0]["nome"] == "Ana"
    assert ana.delete(f"/api/casa/pessoas/{bia_id}").status_code == 200
    assert bia.get("/api/medidores", headers={"X-MW-Casa": str(dono)}).status_code == 403
    atividade = [a["descricao"] for a in bia.get("/api/privacidade/atividade").get_json()["atividade"]]
    assert "A dona de uma casa tirou o seu acesso" in atividade and "Você passou a ver a casa de outra pessoa" in atividade
    # de novo, e agora a Bia sai sozinha
    bia.post("/api/casa/aceitar", {"token": convidar(plataforma, ana)})
    assert bia.delete(f"/api/casa/casas/{dono}").status_code == 200
    assert ana.get("/api/casa").get_json()["pessoas"] == []
    assert bia.delete(f"/api/casa/casas/{dono}").status_code == 404


def test_excluir_a_conta_apaga_os_acessos(casa, plataforma):
    from conftest import SENHA
    ana, bia = casa
    token = convidar(plataforma, ana)
    dono = bia.post("/api/casa/aceitar", {"token": token}).get_json()["dono_id"]
    convidar(plataforma, ana, "caio@exemplo.com.br")
    assert ana.post("/api/privacidade/excluir-conta", {"senha": SENHA}).status_code == 200
    db = plataforma.db()
    assert db.execute("SELECT COUNT(*) FROM acessos_casa").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM convites_casa").fetchone()[0] == 0
    assert bia.get("/api/conta/eu").get_json()["casas"] == []
    r = bia.get("/api/medidores", headers={"X-MW-Casa": str(dono)})
    assert r.status_code == 403 and r.get_json()["codigo"] == "sem_acesso"
