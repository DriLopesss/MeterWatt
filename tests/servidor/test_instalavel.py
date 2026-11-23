"""App instalável: service worker, manifesto, ícones e o vínculo com o app da Play Store."""
import json
import os
import re
import shutil

import pytest
from PIL import Image

from meterwatt import config
from meterwatt.instalavel import PASTA_ESTATICA, ServiceWorker, _casca

DIGITAL = "AB:CD:" + ":".join(["0F"] * 30)


def script(r):
    texto = r.get_data(as_text=True)
    cabeca = json.loads(texto.split("\n", 1)[0].removeprefix("self.MW = ").rstrip(";"))
    return cabeca, texto


def test_service_worker_na_raiz_com_a_lista_da_casca(cliente):
    r = cliente.get("/sw.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript"
    assert r.headers["Cache-Control"] == "no-cache"                     # o navegador sempre confere se mudou
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    cabeca, texto = script(r)
    assert re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{12}", cabeca["versao"])
    assert {"/", "/estatico/app.js", "/estatico/app.css", "/manifest.webmanifest"} <= set(cabeca["arquivos"])
    assert any(a.startswith("/estatico/fontes/") for a in cabeca["arquivos"])
    assert not any(a.startswith("/api/") for a in cabeca["arquivos"])     # dado da conta nunca fica no aparelho
    assert 'addEventListener("push"' in texto and 'url.pathname.startsWith("/api/")' in texto
    for arquivo in cabeca["arquivos"]:                                    # tudo o que a casca guarda existe
        assert cliente.get(arquivo).status_code == 200, arquivo


def test_versao_muda_quando_um_arquivo_da_casca_muda(tmp_path):
    copia = tmp_path / "estatico"
    shutil.copytree(PASTA_ESTATICA, copia)
    sw = ServiceWorker(str(copia))
    antes = sw.versao()
    assert sw.versao() == antes                                           # sem mudança, mesma versão
    with open(copia / "app.css", "a", encoding="utf-8") as f:
        f.write("\n/* mudou */\n")
    os.utime(copia / "app.css", ns=(1, 1))     # mesmo com data antiga, o tamanho mudou: confere de novo
    depois = sw.versao()
    assert depois != antes
    with open(copia / "sw.js", "a", encoding="utf-8") as f:
        f.write("\n// mudou\n")
    assert sw.versao() not in (antes, depois)


def test_manifesto(cliente):
    r = cliente.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    m = r.get_json(force=True)
    assert m["id"] == "/" and m["scope"] == "/" and m["start_url"].startswith("/") and m["display"] == "standalone"
    assert m["lang"] == "pt-BR" and m["theme_color"] == m["background_color"] == "#0b0a12"
    tamanhos = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= tamanhos
    imagens = list(m["icons"]) + list(m["screenshots"]) + [i for atalho in m["shortcuts"] for i in atalho["icons"]]
    for item in imagens:
        caminho = os.path.join(PASTA_ESTATICA, item["src"].removeprefix("/estatico/"))
        assert os.path.exists(caminho), item["src"]
        with Image.open(caminho) as imagem:
            assert f"{imagem.width}x{imagem.height}" == item["sizes"], item["src"]
            assert imagem.format.lower() == item["type"].split("/")[1], item["src"]
    for tela in m["screenshots"]:
        largura, altura = map(int, tela["sizes"].split("x"))
        assert max(largura, altura) <= 2.3 * min(largura, altura)          # regra do Chrome para a tela de instalar
        assert (largura > altura) == (tela["form_factor"] == "wide")
    for atalho in m["shortcuts"]:
        assert atalho["url"].startswith("/#")


def test_pagina_aponta_para_o_manifesto_e_os_icones(cliente):
    html = cliente.get("/").get_data(as_text=True)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    icone = re.search(r'<link rel="apple-touch-icon" href="([^"]+)">', html).group(1)
    assert cliente.get(icone).status_code == 200
    with Image.open(os.path.join(PASTA_ESTATICA, icone.removeprefix("/estatico/"))) as imagem:
        assert imagem.size == (180, 180) and imagem.mode == "RGB"           # sem transparência: o iPhone pinta de preto


def test_icone_maskable_deixa_o_raio_na_area_segura():
    """O Android recorta o ícone em círculo de 80%: fora dele, só o fundo."""
    with Image.open(os.path.join(PASTA_ESTATICA, "app", "icone-maskable-512.png")) as imagem:
        pixels = imagem.convert("RGB").load()
        fundo = pixels[0, 0]
        for x in range(0, 512, 4):
            for y in range(0, 512, 4):
                if (x - 256) ** 2 + (y - 256) ** 2 > (0.4 * 512) ** 2:
                    assert max(abs(a - b) for a, b in zip(pixels[x, y], fundo)) < 40, (x, y, pixels[x, y])


def test_casca_tem_as_fontes_usadas_no_estilo():
    arquivos = _casca(PASTA_ESTATICA)
    with open(os.path.join(PASTA_ESTATICA, "app.css"), encoding="utf-8") as f:
        usadas = set(re.findall(r"url\(fontes/([\w.-]+\.woff2)\)", f.read()))
    assert usadas and {f"/estatico/fontes/{n}" for n in usadas} <= set(arquivos)


def test_assetlinks_so_com_o_app_android_configurado(cliente, tmp_path):
    r = cliente.get("/.well-known/assetlinks.json")
    assert r.status_code == 404
    base = {"MW_PASTA_INSTANCIA": str(tmp_path / "x")}
    cfg = config.carregar({**base, "MW_ANDROID_PACOTE": "br.com.meterwatt.app", "MW_ANDROID_SHA256": f"{DIGITAL.lower()}, {DIGITAL}"})
    assert cfg.android_pacote == "br.com.meterwatt.app" and cfg.android_sha256 == (DIGITAL, DIGITAL)
    from conftest import Cliente
    from meterwatt.mensageiro import MensageiroMemoria
    from meterwatt.plataforma import Plataforma
    from meterwatt.web import criar_app
    p = Plataforma(cfg, MensageiroMemoria())
    r = Cliente(criar_app(p)).get("/.well-known/assetlinks.json")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert r.get_json() == [{"relation": ["delegate_permission/common.handle_all_urls"],
                             "target": {"namespace": "android_app", "package_name": "br.com.meterwatt.app",
                                        "sha256_cert_fingerprints": [DIGITAL, DIGITAL]}}]
    p.fechar_conexao_da_thread()


@pytest.mark.parametrize("pacote, digital, trecho", [
    ("br.com.meterwatt.app", None, "juntos"),
    (None, DIGITAL, "juntos"),
    ("meterwatt", DIGITAL, "MW_ANDROID_PACOTE"),
    ("br.com.meter-watt", DIGITAL, "MW_ANDROID_PACOTE"),
    ("br.com.meterwatt.app", "AB:CD", "MW_ANDROID_SHA256"),
    ("br.com.meterwatt.app", DIGITAL.replace(":", ""), "MW_ANDROID_SHA256"),
])
def test_configuracao_do_app_android_invalida(tmp_path, pacote, digital, trecho):
    env = {"MW_PASTA_INSTANCIA": str(tmp_path / "x")}
    if pacote:
        env["MW_ANDROID_PACOTE"] = pacote
    if digital:
        env["MW_ANDROID_SHA256"] = digital
    with pytest.raises(config.ConfiguracaoInvalida, match=trecho):
        config.carregar(env)
