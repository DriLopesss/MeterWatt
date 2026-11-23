"""Gera a demonstração do aplicativo em UM arquivo HTML, que abre direto no navegador.

    python tools/gerar_demo.py                 # grava meterwatt-demonstracao.html na pasta atual
    python tools/gerar_demo.py --saida X.html

Junta a página, o estilo, as fontes e o código do aplicativo de verdade (src/meterwatt/estatico)
com um servidor de mentira (tools/demo/servidor_de_mentira.js) que responde com uma casa
inventada. Nada sai do navegador. Os números são SIMULADOS.
"""
import argparse
import base64
import os
import re

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ESTATICO = os.path.join(RAIZ, "src", "meterwatt", "estatico")
FALSO = os.path.join(RAIZ, "tools", "demo", "servidor_de_mentira.js")

FAIXA = ('<div class="faixa-demo" role="note">Demonstração com dados simulados: nada aqui é medição real e nada sai do '
         'seu navegador. Para entrar, qualquer e-mail e senha servem.</div>')
ESTILO_DA_FAIXA = (".faixa-demo{position:relative;z-index:40;background:#ff4fd8;color:#1a0016;font:600 .86rem/1.4 system-ui,sans-serif;"
                   "text-align:center;padding:7px 16px}")


def ler(caminho, modo="r"):
    with open(caminho, modo, **({} if "b" in modo else {"encoding": "utf-8"})) as f:
        return f.read()


def data_uri(caminho, tipo):
    return f"data:{tipo};base64," + base64.b64encode(ler(caminho, "rb")).decode("ascii")


def gerar():
    html = ler(os.path.join(ESTATICO, "index.html"))
    css = ler(os.path.join(ESTATICO, "app.css"))
    js = ler(os.path.join(ESTATICO, "app.js"))
    contas = ler(os.path.join(ESTATICO, "economia-calculo.js"))
    icone = data_uri(os.path.join(ESTATICO, "icone.svg"), "image/svg+xml")

    css = re.sub(r"url\(fontes/([\w.-]+\.woff2)\)",
                 lambda m: f'url("{data_uri(os.path.join(ESTATICO, "fontes", m.group(1)), "font/woff2")}")', css)
    css = css.replace("url(icone.svg)", f'url("{icone}")') + "\n" + ESTILO_DA_FAIXA
    # Começa no painel, já dentro da conta de demonstração.
    js = js.replace('const alvo = rota || (estado.eu ? "painel" : "inicio");', 'const alvo = rota || (estado.eu ? "painel" : "entrar");')

    html = re.sub(r'\s*<link rel="preload"[^>]*>', "", html)
    # Aberta direto do disco, a demonstração não é instalável: sem manifesto nem ícones do app.
    html = re.sub(r'\s*<link rel="(manifest|apple-touch-icon)"[^>]*>', "", html)
    html = html.replace('<link rel="icon" href="/estatico/icone.svg" type="image/svg+xml">', f'<link rel="icon" href="{icone}">')
    html = html.replace('<link rel="stylesheet" href="/estatico/app.css">', f"<style>\n{css}\n</style>")
    # Script embutido não respeita "defer": vai para o fim do corpo, depois dos elementos que ele usa.
    # Ordem: as contas do "E se eu…?" (o servidor de mentira também usa), o servidor de mentira e o aplicativo.
    html = re.sub(r'\s*<script src="/estatico/[\w.-]+\.js" defer></script>', "", html)
    protegidos = [codigo.replace("</script", "<\\/script") for codigo in (contas, ler(FALSO), js)]
    scripts = "".join(f"<script>\n{codigo}\n</script>\n" for codigo in protegidos)
    html = html.replace("</body>", scripts + "</body>")
    html = html.replace("<title>MeterWatt</title>", "<title>MeterWatt (demonstração)</title>")
    html = html.replace('<a class="pular" href="#conteudo">', FAIXA + '\n<a class="pular" href="#conteudo">', 1)
    if "/estatico/" in html:
        raise SystemExit("Sobrou referência a /estatico/ no arquivo gerado.")
    return html


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--saida", default="meterwatt-demonstracao.html")
    args = ap.parse_args()
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(gerar())
    print(f"Demonstração gravada em {args.saida} ({os.path.getsize(args.saida) // 1024} KB). Abra com dois cliques.")


if __name__ == "__main__":
    main()
