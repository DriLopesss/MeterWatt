"""Páginas soltas (política, termos e exclusão da conta): abrem sem o app e sem login, como a Play Store pede."""
import re

import pytest
from conftest import Cliente

from meterwatt import config
from meterwatt.documentos import PAGINAS
from meterwatt.mensageiro import MensageiroMemoria
from meterwatt.plataforma import Plataforma
from meterwatt.web import PASTA_ESTATICA, criar_app


@pytest.fixture
def com_controlador(tmp_path):
    cfg = config.carregar({"MW_PASTA_INSTANCIA": str(tmp_path / "i"), "MW_CONTROLADOR": "Fulano & Cia <b>",
                           "MW_EMAIL_PRIVACIDADE": "privacidade@exemplo.com.br", "MW_BACKUP_MANTER": "10"})
    p = Plataforma(cfg, MensageiroMemoria())
    yield Cliente(criar_app(p))
    p.fechar_conexao_da_thread()


@pytest.mark.parametrize("endereco, titulo", [
    ("/politica-de-privacidade", "Política de privacidade"),
    ("/termos-de-uso", "Termos de uso"),
    ("/excluir-conta", "Excluir a conta do MeterWatt"),
])
def test_paginas_abrem_sem_login_e_sem_javascript(cliente, endereco, titulo):
    r = cliente.get(endereco)
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert r.headers["Cache-Control"] == "no-cache" and "default-src 'self'" in r.headers["Content-Security-Policy"]
    html = r.get_data(as_text=True)
    assert f"<h1>{titulo}</h1>" in html
    assert "<script" not in html                                   # nada depende de JavaScript
    assert 'href="#' not in html                                   # links do app viram endereços completos
    for nome in PAGINAS:
        assert f'href="/{nome}"' in html                            # rodapé com as três páginas


def test_valores_da_configuracao_entram_escapados(com_controlador):
    html = com_controlador.get("/excluir-conta").get_data(as_text=True)
    assert "Fulano &amp; Cia &lt;b&gt;" in html and "<b>" not in html
    assert "privacidade@exemplo.com.br" in html
    assert "em até 10 dias" in html                                 # cópias de segurança: MW_BACKUP_MANTER
    politica = com_controlador.get("/politica-de-privacidade").get_data(as_text=True)
    assert "guardamos as 10 mais recentes" in politica and "Fulano &amp; Cia &lt;b&gt;" in politica
    assert 'href="/excluir-conta"' in politica                     # a política leva ao passo a passo da exclusão


def test_sem_configuracao_mostra_o_que_falta_preencher(cliente):
    html = cliente.get("/excluir-conta").get_data(as_text=True)
    assert "[e-mail de privacidade, a definir]" in html             # em produção, o servidor avisa ao subir


def test_pagina_de_exclusao_tem_o_que_a_play_pede(cliente):
    texto = re.sub(r"<[^>]+>", " ", cliente.get("/excluir-conta").get_data(as_text=True))
    texto = re.sub(r"\s+", " ", texto)
    assert "Excluir a conta" in texto and "Privacidade e seus dados" in texto          # o caminho no app
    assert "Sem conseguir entrar na conta" in texto                                    # pedido sem o app
    assert "Apagado na hora" in texto and "Fica:" in texto                             # o que sai e o que fica
    assert "MeterWatt" in texto                                                        # o nome do app


def test_o_app_tem_as_mesmas_telas(cliente):
    """O texto é um só: o app (rota #excluir-conta) e a página solta usam o mesmo <template>."""
    with open(f"{PASTA_ESTATICA}/index.html", encoding="utf-8") as f:
        indice = f.read()
    for modelo, _titulo in PAGINAS.values():
        assert f'<template id="{modelo}">' in indice
    assert indice.count('<section data-rota="excluir-conta" data-documento hidden></section>') == 2   # fora e dentro da conta
    assert cliente.get("/api/publico/info").get_json()["retencao"]["backup_copias"] == 14
