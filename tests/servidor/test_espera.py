"""Lista de espera da página de lançamento."""
from conftest import ORIGEM  # noqa: F401


def entrar(cliente, email, **extra):
    return cliente.post("/api/espera", {"email": email, "aceite": True, **extra})


def token_de(plataforma, email):
    texto = plataforma.mensageiro.ultimo_para(email)["texto"]
    return texto.split("/#fila/")[1].split()[0]


def test_precisa_de_autorizacao_e_email_valido(cliente):
    assert cliente.post("/api/espera", {"email": "ana@exemplo.com.br"}).status_code == 400
    assert cliente.post("/api/espera", {"email": "ana@exemplo.com.br", "aceite": "sim"}).status_code == 400
    assert entrar(cliente, "isso-nao-e-email").status_code == 400


def test_entrar_confirmar_e_posicao(cliente, plataforma, relogio):
    assert entrar(cliente, "Ana@Exemplo.com.br ", origem="instagram").status_code == 202
    assert plataforma.espera.total() == 0                  # só conta depois de confirmar
    r = cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, "ana@exemplo.com.br")}).get_json()
    assert r["posicao"] == 1 and r["total"] == 1 and r["indicados"] == 0 and "/#convite/" in r["link_de_convite"]
    assert cliente.get("/api/publico/info").get_json()["na_lista_de_espera"] == 1
    assert cliente.post("/api/espera/confirmar", {"token": "x" * 40}).status_code == 400
    assert plataforma.db().execute("SELECT origem FROM lista_espera").fetchone()[0] == "instagram"


def test_resposta_igual_para_email_repetido_e_link_nao_muda(cliente, plataforma, relogio):
    entrar(cliente, "ana@exemplo.com.br")
    primeiro = token_de(plataforma, "ana@exemplo.com.br")
    enviados = len(plataforma.mensageiro.enviados)
    r = entrar(cliente, "ana@exemplo.com.br")
    assert r.status_code == 202 and len(plataforma.mensageiro.enviados) == enviados      # menos de 2 min: não reenvia
    relogio.avancar(180)
    r2 = entrar(cliente, "ana@exemplo.com.br")
    assert r2.get_json() == r.get_json() and len(plataforma.mensageiro.enviados) == enviados + 1
    assert token_de(plataforma, "ana@exemplo.com.br") == primeiro          # ninguém invalida o link de outra pessoa
    assert plataforma.db().execute("SELECT COUNT(*) FROM lista_espera").fetchone()[0] == 1


def test_indicar_amigos_sobe_na_fila(cliente, plataforma, relogio):
    for nome in ("ana", "bia", "caio"):
        entrar(cliente, f"{nome}@exemplo.com.br")
        relogio.avancar(10)
        cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, f"{nome}@exemplo.com.br")})
    caio = cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, "caio@exemplo.com.br")}).get_json()
    assert caio["posicao"] == 3
    convite = caio["link_de_convite"].split("/#convite/")[1]
    entrar(cliente, "dani@exemplo.com.br", convite=convite)
    assert cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, "caio@exemplo.com.br")}).get_json()["posicao"] == 3   # amiga ainda não confirmou
    cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, "dani@exemplo.com.br")})
    caio = cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, "caio@exemplo.com.br")}).get_json()
    assert caio["posicao"] == 1 and caio["indicados"] == 1 and caio["total"] == 4
    lista = plataforma.espera.exportar_confirmados()
    assert [l["email"].split("@")[0] for l in lista] == ["caio", "ana", "bia", "dani"]
    # convite inventado não quebra nem conta
    entrar(cliente, "edu@exemplo.com.br", convite="zzzzzzzz")
    assert plataforma.db().execute("SELECT indicado_por FROM lista_espera WHERE email = 'edu@exemplo.com.br'").fetchone()[0] is None


def test_sair_apaga_o_registro(cliente, plataforma):
    entrar(cliente, "ana@exemplo.com.br")
    token = token_de(plataforma, "ana@exemplo.com.br")
    assert "/#sair-da-fila/" + token in plataforma.mensageiro.ultimo_para("ana@exemplo.com.br")["texto"]
    cliente.post("/api/espera/confirmar", {"token": token})
    assert cliente.post("/api/espera/sair", {"token": token}).status_code == 200
    assert plataforma.db().execute("SELECT COUNT(*) FROM lista_espera").fetchone()[0] == 0
    assert cliente.post("/api/espera/sair", {"token": token}).status_code == 400


def test_quem_nao_confirma_e_apagado_em_7_dias(cliente, plataforma, relogio):
    entrar(cliente, "ana@exemplo.com.br")
    entrar(cliente, "bia@exemplo.com.br")
    cliente.post("/api/espera/confirmar", {"token": token_de(plataforma, "bia@exemplo.com.br")})
    relogio.avancar(8 * 86400)
    assert plataforma.privacidade.reter()["lista_de_espera"] == 1
    assert [l["email"] for l in plataforma.espera.exportar_confirmados()] == ["bia@exemplo.com.br"]


def test_limite_por_rede(cliente):
    for i in range(10):
        entrar(cliente, f"pessoa{i}@exemplo.com.br")
    assert entrar(cliente, "mais-uma@exemplo.com.br").status_code == 429
