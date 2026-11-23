"""Ficha da Play Store e app Android: as imagens seguem as regras da Play e o app.json é válido.

As regras de tamanho e formato estão em https://support.google.com/googleplay/android-developer/answer/9866151
(ícone, gráfico de destaque e capturas). Se alguém gerar as imagens de novo (tools/gerar_loja.py)
com outro tamanho, a Play recusaria na hora do envio: melhor saber aqui.
"""
import glob
import json
import os
import re

import pytest

Image = pytest.importorskip("PIL.Image")

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOJA = os.path.join(RAIZ, "docs", "play-store")


def test_icone_da_ficha():
    caminho = os.path.join(LOJA, "icone-512.png")
    with Image.open(caminho) as imagem:
        assert imagem.format == "PNG" and imagem.size == (512, 512)
        assert imagem.mode == "RGBA"                                   # "PNG de 32 bits, com alfa"
        assert imagem.getchannel("A").getextrema() == (255, 255)       # fundo até a borda: a Play arredonda
    assert os.path.getsize(caminho) <= 1024 * 1024


def test_grafico_de_destaque():
    caminho = os.path.join(LOJA, "destaque-1024x500.png")
    with Image.open(caminho) as imagem:
        assert imagem.size == (1024, 500)
        assert imagem.mode == "RGB"                                    # sem transparência
    assert os.path.getsize(caminho) <= 15 * 1024 * 1024


def test_capturas_de_celular():
    capturas = sorted(glob.glob(os.path.join(LOJA, "tela-*.png")))
    assert 4 <= len(capturas) <= 8                                     # 2 a 8; 4 ou mais para aparecer em destaques
    for caminho in capturas:
        with Image.open(caminho) as imagem:
            largura, altura = imagem.size
            assert imagem.mode == "RGB", caminho                       # PNG de 24 bits, sem alfa
            assert (largura, altura) == (1080, 1920), caminho          # 9:16, o mínimo para os destaques
            assert max(largura, altura) <= 2.3 * min(largura, altura)
            assert 320 <= min(largura, altura) and max(largura, altura) <= 3840
        assert os.path.getsize(caminho) <= 8 * 1024 * 1024


def test_app_json():
    with open(os.path.join(RAIZ, "android", "app.json"), encoding="utf-8") as f:
        app = json.load(f)
    # O nome do pacote não muda nunca depois do primeiro envio à Play.
    assert re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", app["pacote"])
    assert app["dominio"] == "" or re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", app["dominio"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", app["versao_nome"])
    assert app["chave_alias"] and app["nome"]


def test_app_reconhece_quando_abre_pela_play():
    """O app Android abre /?app=android: é assim que o site sabe que não pode vender a assinatura ali."""
    with open(os.path.join(RAIZ, "android", "gerar-projeto.mjs"), encoding="utf-8") as f:
        assert 'startUrl: "/?app=android#painel"' in f.read()
    with open(os.path.join(RAIZ, "src", "meterwatt", "estatico", "app.js"), encoding="utf-8") as f:
        codigo = f.read()
    assert "app=android" in codigo and "android-app://" in codigo
