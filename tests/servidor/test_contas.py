"""Cadastro, confirmação, entrada, duas etapas, sessões e senhas, pela API."""
from conftest import SENHA, ativar_2fa, cadastrar, conta_pronta, entrar, link_do_email
from meterwatt.seguranca import codigo_totp


def test_cadastro_exige_aceites_obrigatorios(cliente):
    r = cliente.post("/api/conta/cadastro", {"nome": "Ana", "email": "ana@exemplo.com.br", "senha": SENHA,
                                             "aceites": {"termos": True, "privacidade": True}})
    assert r.status_code == 400 and "consumo" in r.get_json()["erro"]


def test_cadastro_nao_revela_email_existente(cliente, plataforma):
    cadastrar(cliente, plataforma)
    antes = len(plataforma.mensageiro.enviados)
    r = cliente.post("/api/conta/cadastro", {"nome": "Outra", "email": "ANA@exemplo.com.br ", "senha": SENHA,
                                             "aceites": {"termos": True, "privacidade": True, "consumo": True}})
    assert r.status_code == 202                       # mesma resposta de um cadastro novo
    aviso = plataforma.mensageiro.enviados[antes:]
    assert len(aviso) == 1 and "tentou criar" in aviso[0]["assunto"]
    assert plataforma.db().execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 1


def test_entrar_antes_de_confirmar(cliente, plataforma):
    cadastrar(cliente, plataforma, confirmar=False)
    r = entrar(cliente)
    assert r.status_code == 403 and r.get_json()["codigo"] == "email_nao_confirmado"
    token = link_do_email(plataforma, "ana@exemplo.com.br", "confirmar")
    assert cliente.post("/api/conta/confirmar", {"token": token}).status_code == 200
    assert cliente.post("/api/conta/confirmar", {"token": token}).status_code == 400   # link usa uma vez só
    assert entrar(cliente).status_code == 200


def test_link_de_confirmacao_expira(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma, confirmar=False)
    token = link_do_email(plataforma, "ana@exemplo.com.br", "confirmar")
    relogio.avancar(25 * 3600)
    assert cliente.post("/api/conta/confirmar", {"token": token}).status_code == 400


def test_login_errado_mesma_mensagem_e_bloqueio(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma)
    sem_conta = entrar(cliente, "ninguem@exemplo.com.br")
    senha_errada = entrar(cliente, senha="Outra-senha-2026")
    assert sem_conta.status_code == senha_errada.status_code == 401
    assert sem_conta.get_json() == senha_errada.get_json()
    for _ in range(4):
        entrar(cliente, senha="Outra-senha-2026")
    r = entrar(cliente)                                # até a senha certa espera o bloqueio passar
    assert r.status_code == 429
    relogio.avancar(16 * 60)
    assert entrar(cliente).status_code == 200


def test_sessao_eu_e_sair(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    r = cliente.get("/api/conta/eu")
    assert r.status_code == 200 and r.get_json()["email"] == "ana@exemplo.com.br"
    assert "senha_hash" not in r.get_json()
    assert cliente.post("/api/conta/sair").status_code == 200
    assert cliente.get("/api/conta/eu").status_code == 401


def test_sessao_expira_por_inatividade_e_por_idade(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    relogio.avancar(23 * 3600)
    assert cliente.get("/api/conta/eu").status_code == 200
    relogio.avancar(25 * 3600)
    assert cliente.get("/api/conta/eu").status_code == 401
    entrar(cliente)
    for _ in range(15):                                # uso diário não estende além de 14 dias
        relogio.avancar(86400 - 60)
        cliente.get("/api/conta/eu")
    assert cliente.get("/api/conta/eu").status_code == 401


def test_2fa_completo(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    segredo, backups = ativar_2fa(cliente, relogio)
    assert len(backups) == 10
    linha = plataforma.db().execute("SELECT totp_cifrado FROM usuarios").fetchone()
    assert segredo.encode() not in linha["totp_cifrado"]      # guardado cifrado

    outro = novo_cliente()
    r = entrar(outro)
    assert r.get_json()["etapa"] == "aguardando_2fa"
    assert outro.get("/api/conta/eu").status_code == 401        # meia sessão não abre nada
    assert outro.get("/api/medidores").status_code == 401
    assert outro.post("/api/conta/2fa", {"codigo": "000000"}).status_code == 400
    relogio.avancar(31)
    r = outro.post("/api/conta/2fa", {"codigo": codigo_totp(segredo, relogio())})
    assert r.status_code == 200 and r.get_json()["etapa"] == "completa"
    assert outro.get("/api/conta/eu").get_json()["dois_fatores"] is True

    # o mesmo código não serve duas vezes
    terceiro = novo_cliente()
    entrar(terceiro)
    assert terceiro.post("/api/conta/2fa", {"codigo": codigo_totp(segredo, relogio())}).status_code == 400

    # código de backup: funciona uma vez e avisa por e-mail
    quarto = novo_cliente()
    entrar(quarto)
    assert quarto.post("/api/conta/2fa", {"codigo": backups[0].upper()}).status_code == 200
    assert "backup" in plataforma.mensageiro.ultimo_para("ana@exemplo.com.br")["assunto"]
    quinto = novo_cliente()
    entrar(quinto)
    assert quinto.post("/api/conta/2fa", {"codigo": backups[0]}).status_code == 400


def test_2fa_cinco_erros_derrubam_a_meia_sessao(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    ativar_2fa(cliente, relogio)
    outro = novo_cliente()
    entrar(outro)
    for _ in range(4):
        assert outro.post("/api/conta/2fa", {"codigo": "000000"}).status_code == 400
    assert outro.post("/api/conta/2fa", {"codigo": "000000"}).status_code == 401
    assert outro.get("/api/conta/2fa/etapa").status_code == 401


def test_meia_sessao_expira_em_5_minutos(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    segredo, _ = ativar_2fa(cliente, relogio)
    outro = novo_cliente()
    entrar(outro)
    relogio.avancar(6 * 60)
    assert outro.post("/api/conta/2fa", {"codigo": codigo_totp(segredo, relogio())}).status_code == 401


def test_desativar_2fa_pede_senha_e_codigo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    segredo, _ = ativar_2fa(cliente, relogio)
    relogio.avancar(60)
    assert cliente.post("/api/conta/2fa/desativar", {"senha": SENHA, "codigo": "000000"}).status_code == 403
    assert cliente.post("/api/conta/2fa/desativar", {"senha": "errada-2026-x", "codigo": codigo_totp(segredo, relogio())}).status_code == 403
    r = cliente.post("/api/conta/2fa/desativar", {"senha": SENHA, "codigo": codigo_totp(segredo, relogio())})
    assert r.status_code == 200
    assert cliente.get("/api/conta/eu").get_json()["dois_fatores"] is False


def test_redefinir_senha_derruba_todas_as_sessoes(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    outro = novo_cliente()
    assert outro.post("/api/conta/esqueci", {"email": "ana@exemplo.com.br"}).status_code == 202
    assert outro.post("/api/conta/esqueci", {"email": "ninguem@exemplo.com.br"}).status_code == 202
    token = link_do_email(plataforma, "ana@exemplo.com.br", "redefinir")
    assert outro.post("/api/conta/redefinir", {"token": token, "senha": "123"}).status_code == 400
    assert outro.post("/api/conta/redefinir", {"token": token, "senha": "Tomada-verde-2027"}).status_code == 200
    assert outro.post("/api/conta/redefinir", {"token": token, "senha": "Tomada-verde-2028"}).status_code == 400
    assert cliente.get("/api/conta/eu").status_code == 401
    assert entrar(outro, senha=SENHA).status_code == 401
    assert entrar(outro, senha="Tomada-verde-2027").status_code == 200


def test_link_de_redefinicao_vale_30_minutos(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma)
    cliente.post("/api/conta/esqueci", {"email": "ana@exemplo.com.br"})
    token = link_do_email(plataforma, "ana@exemplo.com.br", "redefinir")
    relogio.avancar(31 * 60)
    assert cliente.post("/api/conta/redefinir", {"token": token, "senha": "Tomada-verde-2027"}).status_code == 400


def test_trocar_senha_encerra_as_outras_sessoes(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    outro = novo_cliente()
    entrar(outro)
    assert cliente.post("/api/conta/senha", {"atual": "errada-errada", "nova": "Tomada-verde-2027"}).status_code == 403
    assert cliente.post("/api/conta/senha", {"atual": SENHA, "nova": "Tomada-verde-2027"}).status_code == 200
    assert cliente.get("/api/conta/eu").status_code == 200
    assert outro.get("/api/conta/eu").status_code == 401


def test_sessoes_listar_e_encerrar(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    outro = novo_cliente()
    entrar(outro)
    lista = cliente.get("/api/conta/sessoes").get_json()["sessoes"]
    assert len(lista) == 2 and sum(s["atual"] for s in lista) == 1
    alvo = next(s["id"] for s in lista if not s["atual"])
    assert cliente.delete(f"/api/conta/sessoes/{alvo}").status_code == 200
    assert outro.get("/api/conta/eu").status_code == 401


def test_preferencias_validam(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    assert cliente.put("/api/conta/preferencias", {"tarifa_kwh": 0.92, "meta_mensal_reais": 180}).status_code == 200
    eu = cliente.get("/api/conta/eu").get_json()
    assert eu["tarifa_kwh"] == 0.92 and eu["meta_mensal_reais"] == 180
    for ruim in ({"tarifa_kwh": -1}, {"tarifa_kwh": 50}, {"tarifa_kwh": True}, {"meta_mensal_reais": "100"}):
        assert cliente.put("/api/conta/preferencias", ruim).status_code == 400


def test_exigir_2fa(ambiente, relogio, tmp_path):
    from conftest import Cliente
    from meterwatt import config
    from meterwatt.mensageiro import MensageiroMemoria
    from meterwatt.plataforma import Plataforma
    from meterwatt.web import criar_app
    ambiente["MW_EXIGIR_2FA"] = "1"
    p = Plataforma(config.carregar(ambiente), MensageiroMemoria(), relogio=relogio)
    c = Cliente(criar_app(p))
    conta_pronta(c, p)
    assert c.get("/api/medidores").get_json()["codigo"] == "2fa_obrigatorio"
    assert c.get("/api/privacidade").status_code == 200       # direitos do titular sempre liberados
    ativar_2fa(c, relogio)
    assert c.get("/api/medidores").status_code == 200
