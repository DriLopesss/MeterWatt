"""Gera os ícones do app (tela inicial do celular, notificações e atalhos) e as telas do manifesto.

    python tools/gerar_icones.py           # ícones
    python tools/gerar_icones.py --telas   # também as telas que aparecem na hora de instalar

Desenha o raio do MeterWatt (o mesmo de src/meterwatt/estatico/icone.svg) em vários tamanhos e
grava os PNGs em src/meterwatt/estatico/app/. As telas saem da demonstração (dados simulados),
sem a faixa de aviso. Tudo vai para o repositório: o servidor não precisa desta ferramenta.
Só quem quiser mudar o desenho ou as telas roda de novo.

Precisa do Playwright com o Chromium (pip install playwright && playwright install chromium) e do Pillow.
"""
import argparse
import io
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

from PIL import Image

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DESTINO = os.path.join(RAIZ, "src", "meterwatt", "estatico", "app")

RAIO = "M18 3 7 18h8l-2 11 12-16h-8z"
NOITE = "#0b0a12"
DEFS = """<defs>
  <linearGradient id="cor" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#ffc24a"/><stop offset="1" stop-color="#ff7a1a"/>
  </linearGradient>
  <radialGradient id="luz" cx="50%" cy="46%" r="50%">
    <stop offset="0" stop-color="#ffb020" stop-opacity=".22"/><stop offset="1" stop-color="#ffb020" stop-opacity="0"/>
  </radialGradient>
  <filter id="brilho" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="1.4"/></filter>
</defs>"""

# Ícones dos atalhos: os mesmos traços das abas do aplicativo (index.html), em caixa de 24.
TRACOS = {
    "agora": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "economia": '<path d="M3 7l6.5 6.5 4-4L21 17"/><path d="M15 17h6v-6"/>',
    "avisos": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
}


def svg_raio(arredondado, escala):
    """O ícone principal. `escala` encolhe o raio em volta do centro (o maskable precisa de folga)."""
    fundo = f'<rect width="32" height="32" rx="{8 if arredondado else 0}" fill="{NOITE}"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{DEFS}{fundo}'
            '<circle cx="16" cy="16" r="15" fill="url(#luz)"/>'
            f'<g transform="translate(16 16) scale({escala}) translate(-16 -16)">'
            f'<path d="{RAIO}" fill="#ffb020" opacity=".5" filter="url(#brilho)"/>'
            f'<path d="{RAIO}" fill="url(#cor)"/></g></svg>')


def svg_selo():
    """Selo das notificações no Android: só a silhueta conta (o sistema pinta de uma cor só)."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<g transform="translate(16 16) scale(1.12) translate(-16 -16)"><path d="{RAIO}" fill="#fff"/></g></svg>')


def svg_atalho(nome):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{DEFS}'
            f'<rect width="32" height="32" rx="8" fill="{NOITE}"/>'
            '<g transform="translate(5.5 5.5) scale(.875)" fill="none" stroke="url(#cor)" stroke-width="2.2" '
            f'stroke-linecap="round" stroke-linejoin="round">{TRACOS[nome]}</g></svg>')


ICONES = [
    # arquivo, lado em pixels, desenho, opaco (sem transparência)
    ("icone-192.png", 192, svg_raio(True, 1.0), False),
    ("icone-512.png", 512, svg_raio(True, 1.0), False),
    # "maskable": o sistema recorta em círculo, gota ou quadrado. O raio fica dentro do círculo central de 80%.
    ("icone-maskable-512.png", 512, svg_raio(False, 0.78), True),
    ("icone-maskable-192.png", 192, svg_raio(False, 0.78), True),
    # iPhone e iPad: o sistema arredonda os cantos sozinho, então o fundo vai até a borda.
    ("icone-apple-180.png", 180, svg_raio(False, 0.9), True),
    ("selo-notificacao-96.png", 96, svg_selo(), False),
    ("atalho-agora-96.png", 96, svg_atalho("agora"), False),
    ("atalho-economia-96.png", 96, svg_atalho("economia"), False),
    ("atalho-avisos-96.png", 96, svg_atalho("avisos"), False),
]


def desenhar(pagina, svg, lado):
    pagina.set_viewport_size({"width": lado, "height": lado})
    pagina.set_content(f'<html><body style="margin:0;background:transparent">'
                       f'<div style="width:{lado}px;height:{lado}px">{svg.replace("<svg ", f"<svg width={lado!s} height={lado!s} ", 1)}</div>'
                       "</body></html>")
    return Image.open(io.BytesIO(pagina.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": lado, "height": lado})))


# As telas do manifesto: nome, largura e altura da janela, densidade de pixels e a tela do app.
# As telas mostram sempre o mesmo momento da casa simulada: uma quinta às 19h35 em São Paulo (22h35 em
# UTC; o Brasil não tem horário de verão desde 2019), com o chuveiro ligado e o pico do fim do dia.
MOMENTO_DAS_TELAS = datetime(2026, 11, 19, 22, 35, tzinfo=timezone.utc)
FUSO_DAS_TELAS = "America/Sao_Paulo"

TELAS = [
    ("tela-painel.webp", 390, 844, 2, "painel"),
    ("tela-economia.webp", 390, 844, 2, "economia"),
    ("tela-computador.webp", 1280, 800, 1, "painel"),
]


def telas(navegador):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from gerar_demo import gerar
    with tempfile.TemporaryDirectory() as pasta:
        demo = os.path.join(pasta, "demo.html")
        with open(demo, "w", encoding="utf-8") as f:
            f.write(gerar())
        for nome, largura, altura, densidade, rota in TELAS:
            contexto = navegador.new_context(viewport={"width": largura, "height": altura}, device_scale_factor=densidade,
                                             is_mobile=largura < 600, has_touch=largura < 600, reduced_motion="reduce",
                                             timezone_id=FUSO_DAS_TELAS)
            pagina = contexto.new_page()
            pagina.clock.set_system_time(MOMENTO_DAS_TELAS)
            pagina.goto(f"file://{demo}#{rota}")
            pagina.wait_for_selector(f'section[data-rota="{rota}"]:not([hidden])')
            pagina.add_style_tag(content=".faixa-demo{display:none!important}")
            time.sleep(2.5)                         # os números terminam de chegar e de virar
            imagem = Image.open(io.BytesIO(pagina.screenshot())).convert("RGB")
            caminho = os.path.join(DESTINO, nome)
            imagem.save(caminho, "WEBP", quality=82, method=6)
            print(f"{nome}: {imagem.width}x{imagem.height}, {os.path.getsize(caminho) // 1024} KB")
            contexto.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--telas", action="store_true", help="também gera as telas do manifesto a partir da demonstração")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright
    os.makedirs(DESTINO, exist_ok=True)
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(device_scale_factor=1)
        for nome, lado, svg, opaco in ICONES:
            imagem = desenhar(pagina, svg, lado)
            if opaco:
                imagem = imagem.convert("RGB")
            caminho = os.path.join(DESTINO, nome)
            imagem.save(caminho, optimize=True)
            print(f"{nome}: {lado}x{lado}, {os.path.getsize(caminho) // 1024 or 1} KB")
        if args.telas:
            telas(navegador)
        navegador.close()


if __name__ == "__main__":
    main()
