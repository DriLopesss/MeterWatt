"""Gera as imagens da ficha do MeterWatt na Play Store, em docs/play-store/.

    python tools/gerar_loja.py

- icone-512.png: o ícone da ficha (512 x 512, PNG com canal alfa). O fundo vai até a borda: a Play
  arredonda os cantos sozinha.
- destaque-1024x500.png: o gráfico de destaque (PNG sem transparência, como a Play pede).
- tela-1-agora.png ... tela-6-avisos.png: capturas de celular em 1080 x 1920 (9:16, o tamanho que a
  Play usa para destacar apps), com uma frase em cima. A frase ocupa menos de 20% da altura.

As capturas saem da demonstração (a casa simulada do botão "Ver o app funcionando"), sem a faixa
de aviso dela: são as telas de verdade do app. As frases estão em TELAS, logo abaixo.
Precisa do Playwright com o Chromium e do Pillow, como tools/gerar_icones.py.
"""
import base64
import io
import os
import pathlib
import sys
import tempfile
import time

from PIL import Image

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from gerar_demo import gerar as gerar_demo  # noqa: E402
from gerar_icones import desenhar, svg_raio  # noqa: E402

RAIZ = os.path.join(AQUI, "..")
ESTATICO = os.path.join(RAIZ, "src", "meterwatt", "estatico")
DESTINO = os.path.join(RAIZ, "docs", "play-store")
FONTES = pathlib.Path(ESTATICO, "fontes").resolve()

LARGURA, ALTURA = 1080, 1920
MARGEM = 72
TOPO_DA_TELA = 300            # a frase fica acima disto: 300 de 1920 = 15,6% da altura
DENSIDADE = 2.4               # 390 pontos de largura do celular viram 936 pixels

# arquivo, frase, tela do app, título do cartão até onde rolar (None: o começo da tela)
TELAS = [
    ("tela-1-agora.png", "Quanto sua casa gasta agora, em reais", "painel", None),
    ("tela-2-meta.png", "A previsão da conta antes de ela chegar", "painel", "Meta do mês"),
    ("tela-3-fantasma.png", "O que fica ligado sem ninguém usar", "painel", "Consumo fantasma"),
    ("tela-4-e-se-eu.png", "Teste uma mudança e veja quanto economiza", "economia", None),
    ("tela-5-detetive.png", "Pistas de quais aparelhos pesam na conta", "aparelhos", None),
    ("tela-6-avisos.png", "Avisos quando o consumo foge do normal", "avisos", None),
]

FUNDO = ("radial-gradient(1000px 640px at 0% -10%,rgba(167,139,250,.18),transparent 60%),"
         "radial-gradient(900px 600px at 110% 0%,rgba(54,227,255,.1),transparent 62%),#0b0a12")


def fonte(peso):
    return (f'@font-face{{font-family:"Unbounded";font-weight:{peso};'
            f'src:url("{(FONTES / f"unbounded-latin-{peso}-normal.woff2").as_uri()}") format("woff2")}}')


def capturar(navegador, demo, rota, cartao):
    """A tela do app como o celular mostra: 390 pontos de largura, cabeçalho e abas no lugar."""
    altura_da_tela = (ALTURA - TOPO_DA_TELA - MARGEM) / DENSIDADE
    contexto = navegador.new_context(viewport={"width": 390, "height": round(altura_da_tela)},
                                     device_scale_factor=DENSIDADE, is_mobile=True, has_touch=True,
                                     reduced_motion="reduce")
    pagina = contexto.new_page()
    pagina.goto(f"{demo.as_uri()}#{rota}")
    pagina.wait_for_selector(f'section[data-rota="{rota}"]:not([hidden])')
    # Sem a faixa da demonstração; cabeçalho opaco (no celular ele é 97% opaco e, parado numa imagem,
    # o texto que passa por baixo dele aparece como um fantasma).
    pagina.add_style_tag(content=".faixa-demo{display:none!important}.topo{background:#0b0a12!important}")
    time.sleep(2.5)                              # os números terminam de chegar (e os cartões, de crescer)
    if cartao:
        rolou = pagina.evaluate("""([rota, titulo]) => {
            const h2 = [...document.querySelectorAll(`section[data-rota="${rota}"] h2`)].find((h) => h.textContent.trim() === titulo);
            if (!h2) return false;
            const topo = document.querySelector(".topo").getBoundingClientRect().height;
            window.scrollTo(0, h2.closest("article").getBoundingClientRect().top + window.scrollY - topo - 16);
            return true;
        }""", [rota, cartao])
        if not rolou:
            raise SystemExit(f'Não achei o cartão "{cartao}" na tela {rota}.')
        time.sleep(0.6)
    imagem = pagina.screenshot()
    contexto.close()
    return imagem


def montar(navegador, pasta, frase, captura):
    """A captura em moldura, com a frase em cima, no tamanho da Play."""
    largura_da_tela = LARGURA - 2 * MARGEM
    altura_da_tela = ALTURA - TOPO_DA_TELA - MARGEM
    html = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><style>
{fonte(700)}
html,body{{margin:0;width:{LARGURA}px;height:{ALTURA}px;overflow:hidden}}
body{{background:{FUNDO};color:#f4f1ff}}
h1{{position:absolute;left:{MARGEM}px;right:{MARGEM}px;bottom:{ALTURA - TOPO_DA_TELA + 52}px;margin:0;
   font:700 58px/1.13 "Unbounded",sans-serif;letter-spacing:-.012em;text-wrap:balance}}
img{{position:absolute;left:{MARGEM}px;top:{TOPO_DA_TELA}px;width:{largura_da_tela}px;height:{altura_da_tela}px;
    border-radius:40px;box-shadow:0 0 0 2px rgba(255,255,255,.13),0 40px 120px rgba(0,0,0,.55)}}
</style></head><body><h1>{frase}</h1>
<img alt="" src="data:image/png;base64,{base64.b64encode(captura).decode()}"></body></html>"""
    arquivo = os.path.join(pasta, "moldura.html")
    with open(arquivo, "w", encoding="utf-8") as f:
        f.write(html)
    pagina = navegador.new_page(viewport={"width": LARGURA, "height": ALTURA}, device_scale_factor=1)
    pagina.goto(pathlib.Path(arquivo).as_uri())
    pagina.evaluate("document.fonts.ready")
    linhas = pagina.evaluate("(() => { const h = document.querySelector('h1'); "
                             "return Math.round(h.getBoundingClientRect().height / parseFloat(getComputedStyle(h).lineHeight)); })()")
    if linhas > 2:
        raise SystemExit(f'A frase "{frase}" ocupa {linhas} linhas: encurte para caber em duas.')
    imagem = Image.open(io.BytesIO(pagina.screenshot())).convert("RGB")
    pagina.close()
    return imagem


def destaque(navegador, pasta):
    """1024 x 500: o nome, para que serve e o relógio de luz do app, como ele aparece no painel.
    Sem o ícone (a Play pede para não repetir o ícone no destaque) e com o essencial no centro."""
    css = pathlib.Path(ESTATICO, "app.css").resolve().as_uri()
    # 862 W com a tarifa de exemplo do app (R$ 0,92 por kWh): R$ 0,79 por hora.
    digitos = "".join(f'<span class="digito"><span>{c}</span></span>' for c in "862")
    html = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<link rel="stylesheet" href="{css}"><style>
html,body{{margin:0;width:1024px;height:500px;overflow:hidden;min-height:0}}
/* A Play pede fundo que não seja branco, preto nem cinza-escuro (somem no fundo da loja): violeta. */
body{{background:radial-gradient(900px 540px at 0% 0%,rgba(167,139,250,.36),transparent 62%),
     radial-gradient(720px 480px at 100% 100%,rgba(255,176,32,.2),transparent 60%),
     linear-gradient(135deg,#221a4a,#140f2c)}}
.palco{{position:absolute;inset:0;display:flex;align-items:center;justify-content:space-between;padding:0 96px 0 92px}}
.chamada{{display:grid;gap:30px;width:440px;color:var(--tinta)}}
.marca{{font:800 30px/1 var(--f-titulo);letter-spacing:-.02em}}
h1{{font:700 42px/1.16 var(--f-titulo);letter-spacing:-.012em;text-wrap:balance}}
.relogio{{padding:0;gap:16px}}
.relogio-rotulo,.relogio-sub{{font-size:21px}}
.relogio-visor{{font-size:136px}}
</style></head><body><div class="palco">
<div class="chamada"><div class="marca">MeterWatt</div>
<h1>Veja para onde vai a sua conta de luz</h1></div>
<div class="relogio"><p class="relogio-rotulo">Sua casa agora</p>
<div class="relogio-visor">{digitos}<span class="relogio-unidade">W</span></div>
<p class="relogio-sub">R$ 0,79 por hora neste ritmo</p></div>
</div></body></html>"""
    arquivo = os.path.join(pasta, "destaque.html")
    with open(arquivo, "w", encoding="utf-8") as f:
        f.write(html)
    pagina = navegador.new_page(viewport={"width": 1024, "height": 500}, device_scale_factor=1)
    pagina.goto(pathlib.Path(arquivo).as_uri())
    pagina.evaluate("document.fonts.ready")
    imagem = Image.open(io.BytesIO(pagina.screenshot())).convert("RGB")
    pagina.close()
    return imagem


def gravar(imagem, nome):
    caminho = os.path.join(DESTINO, nome)
    imagem.save(caminho, optimize=True)
    print(f"{nome}: {imagem.width}x{imagem.height} {imagem.mode}, {os.path.getsize(caminho) // 1024} KB")


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(DESTINO, exist_ok=True)
    with sync_playwright() as p, tempfile.TemporaryDirectory() as pasta:
        navegador = p.chromium.launch()
        # Ícone da ficha: quadrado cheio (a Play recorta os cantos), PNG de 32 bits com alfa.
        pagina = navegador.new_page(device_scale_factor=1)
        gravar(desenhar(pagina, svg_raio(False, 0.9), 512).convert("RGBA"), "icone-512.png")
        pagina.close()
        gravar(destaque(navegador, pasta), "destaque-1024x500.png")
        demo = pathlib.Path(pasta, "demo.html")
        demo.write_text(gerar_demo(), encoding="utf-8")
        for nome, frase, rota, cartao in TELAS:
            gravar(montar(navegador, pasta, frase, capturar(navegador, demo, rota, cartao)), nome)
        navegador.close()


if __name__ == "__main__":
    main()
