"""Entrada por um navegador novo: e-mail "foi você?" para a dona da conta."""
from conftest import SENHA, ativar_2fa, conta_pronta, entrar

from meterwatt.seguranca import codigo_totp

ANA = "ana@exemplo.com.br"
ASSUNTO = "Entrada na sua conta por um navegador novo"


def avisos(plataforma):
    return [m for m in plataforma.mensageiro.enviados if m["para"] == ANA and m["assunto"] == ASSUNTO]


def test_primeira_entrada_nao_avisa_e_navegador_conhecido_tambem_nao(cliente, plataforma):
    conta_pronta(cliente, plataforma)                       # primeira entrada da conta
    assert avisos(plataforma) == []
    cliente.post("/api/conta/sair")
    assert entrar(cliente).status_code == 200               # o mesmo navegador (o cookie ficou)
    assert avisos(plataforma) == []


def test_navegador_novo_avisa(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    outro = novo_cliente()
    r = outro.http.post("/api/conta/entrar", json={"email": ANA, "senha": SENHA},
                        headers={**outro._cab(), "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"})
    assert r.status_code == 200
    [m] = avisos(plataforma)
    assert "Chrome no Android" in m["texto"] and "/#esqueci" in m["texto"] and "Se foi você" in m["texto"]
    atividade = [a["evento"] for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"]]
    assert "entrada_aparelho_novo" in atividade
    copia = cliente.post("/api/privacidade/exportar").get_json()
    assert len(copia["navegadores_de_entrada"]) == 2 and "marca" not in copia["navegadores_de_entrada"][0]
    # Na segunda vez, o navegador já é conhecido.
    outro.post("/api/conta/sair")
    entrar(outro)
    assert len(avisos(plataforma)) == 1


def test_com_duas_etapas_avisa_so_depois_do_codigo(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    segredo, _ = ativar_2fa(cliente, relogio)
    outro = novo_cliente()
    entrar(outro)
    assert avisos(plataforma) == []                         # só a senha não basta para entrar: ainda não avisa
    relogio.avancar(31)
    assert outro.post("/api/conta/2fa", {"codigo": codigo_totp(segredo, relogio())}).status_code == 200
    assert len(avisos(plataforma)) == 1


def test_cookie_inventado_conta_como_navegador_novo(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    outro = novo_cliente()
    outro.http.set_cookie("mw_aparelho", "a" * 43)
    entrar(outro)
    assert len(avisos(plataforma)) == 1
