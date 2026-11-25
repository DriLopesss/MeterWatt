"""Limites de tentativa guardados no banco: valem depois de reiniciar o servidor, sem e-mails legíveis no banco."""
import sqlite3

from conftest import Cliente, conta_pronta

from meterwatt import config
from meterwatt.mensageiro import MensageiroMemoria
from meterwatt.plataforma import Plataforma
from meterwatt.web import criar_app

ANA = "ana@exemplo.com.br"


def errar_login(cliente, vezes):
    return [cliente.post("/api/conta/entrar", {"email": ANA, "senha": "errada-errada-1"}).status_code for _ in range(vezes)]


def test_limite_de_login_vale_depois_de_reiniciar(cliente, plataforma, ambiente, relogio):
    conta_pronta(cliente, plataforma)
    assert errar_login(Cliente(criar_app(plataforma)), 5) == [401] * 5
    # "Reinicia": outra plataforma no mesmo banco, sem nada na memória.
    nova = Plataforma(config.carregar(ambiente), MensageiroMemoria(), relogio=relogio)
    try:
        assert errar_login(Cliente(criar_app(nova)), 1) == [429]
        # Depois da janela (15 min), libera.
        relogio.avancar(15 * 60 + 1)
        assert errar_login(Cliente(criar_app(nova)), 1) == [401]
        # Nenhuma chave legível: nem o e-mail nem a rede.
        linhas = nova.db().execute("SELECT nome, chave FROM tentativas").fetchall()
        assert linhas and all("@" not in l["chave"] and "127.0.0.1" not in l["chave"] for l in linhas)
    finally:
        nova.fechar_conexao_da_thread()


def test_zerar_e_retencao(plataforma, relogio):
    lim = plataforma.limite_login_email
    for _ in range(5):
        lim.registrar("x@y.z", relogio())
    assert lim.esgotado("x@y.z", relogio()) > 0
    lim.zerar("x@y.z")
    assert lim.esgotado("x@y.z", relogio()) == 0
    lim.registrar("x@y.z", relogio())
    relogio.avancar(2 * 86400)
    plataforma.privacidade.reter()
    assert plataforma.db().execute("SELECT COUNT(*) FROM tentativas").fetchone()[0] == 0


def test_banco_ocupado_cai_para_a_memoria(plataforma, relogio, monkeypatch):
    lim = plataforma.limite_2fa_conta

    def quebrado():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(lim, "_conn", quebrado)
    for _ in range(10):
        lim.registrar(7, relogio())                 # não levanta erro
    assert lim.esgotado(7, relogio()) > 0          # e o limite continua valendo, pela memória


def test_tentativa_errada_conta_mesmo_com_a_operacao_desfeita(cliente, plataforma):
    """Senha errada dentro de "apagar consumo": a operação é desfeita, mas a tentativa fica contada."""
    conta_pronta(cliente, plataforma)
    for _ in range(5):
        assert cliente.post("/api/privacidade/apagar-consumo", {"senha": "errada-errada-1"}).status_code == 403
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": "errada-errada-1"}).status_code == 429


def test_security_txt(tmp_path, relogio):
    cfg = config.carregar({"MW_PASTA_INSTANCIA": str(tmp_path / "i"), "MW_EMAIL_PRIVACIDADE": "privacidade@exemplo.com.br",
                           "MW_EMAIL_SEGURANCA": "seguranca@exemplo.com.br", "MW_URL_PUBLICA": "https://painel.exemplo.com.br"})
    p = Plataforma(cfg, MensageiroMemoria(), relogio=relogio)
    r = criar_app(p).test_client().get("/.well-known/security.txt")
    texto = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/plain"
    assert "Contact: mailto:seguranca@exemplo.com.br\n" in texto and "Expires: 2027-" in texto
    assert "Canonical: https://painel.exemplo.com.br/.well-known/security.txt" in texto
    p.fechar_conexao_da_thread()


def test_security_txt_sem_contato_nao_existe(app):
    assert app.test_client().get("/.well-known/security.txt").status_code == 404
