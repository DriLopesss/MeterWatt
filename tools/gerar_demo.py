"""Gera a demonstração do aplicativo em UM arquivo HTML, que abre direto no navegador.

    python tools/gerar_demo.py                 # grava meterwatt-demonstracao.html na pasta atual
    python tools/gerar_demo.py --saida X.html

Junta a página, o estilo, as fontes e o código do aplicativo de verdade (src/meterwatt/estatico)
com o servidor de mentira da demonstração (estatico/demonstracao.js, o mesmo do botão "Ver o app
funcionando"), que responde com uma casa inventada. Nada sai do navegador. Os números são SIMULADOS.
"""
import argparse
import base64
import os
import re

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ESTATICO = os.path.join(RAIZ, "src", "meterwatt", "estatico")
FALSO = os.path.join(ESTATICO, "demonstracao.js")
# No arquivo único não há "fora" da demonstração: o app esconde o botão de sair dela.
MARCA_DO_ARQUIVO = "window.MW_DEMONSTRACAO_ARQUIVO = true;"


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
    css = css.replace("url(icone.svg)", f'url("{icone}")')
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
    protegidos = [codigo.replace("</script", "<\\/script") for codigo in (contas, MARCA_DO_ARQUIVO + "\n" + ler(FALSO), js)]
    scripts = "".join(f"<script>\n{codigo}\n</script>\n" for codigo in protegidos)
    html = html.replace("</body>", scripts + "</body>")
    html = html.replace("<title>MeterWatt</title>", "<title>MeterWatt (demonstração)</title>")
    # O arquivo precisa abrir sozinho: nenhum estilo, script, ícone ou fonte pode vir de /estatico/.
    # (No código do app há o endereço da demonstração, que só é usado no site; aqui ela já vem embutida.)
    if re.search(r'(?:src|href)="/estatico/|url\(["\']?/estatico/', html):
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
