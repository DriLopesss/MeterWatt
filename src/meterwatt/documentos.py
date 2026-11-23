"""Política de privacidade, termos de uso e "como excluir a conta" em endereços próprios.

A Play Store pede links que abram sem o app e sem login: o da política de privacidade e o da página
para pedir a exclusão da conta. O texto é o mesmo que o app mostra (os <template> do index.html),
com os valores da configuração (prazos, controlador, e-mail de privacidade) preenchidos aqui no
servidor. Sem JavaScript: a página abre em qualquer navegador e em qualquer robô de verificação.
"""
import html
import os
import re
import threading

PASTA_ESTATICA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estatico")

# endereço -> (id do <template> no index.html, título da aba)
PAGINAS = {
    "politica-de-privacidade": ("doc-politica", "Política de privacidade"),
    "termos-de-uso": ("doc-termos", "Termos de uso"),
    "excluir-conta": ("doc-excluir-conta", "Excluir a conta"),
}
# Os links do texto apontam para telas do app (#privacidade): fora dele, para as páginas soltas quando existem.
LINKS_SOLTOS = {"#politica": "/politica-de-privacidade", "#termos": "/termos-de-uso", "#excluir-conta": "/excluir-conta"}
CAMPO = re.compile(r'<span data-info="([a-z_]+)">(.*?)</span>', re.S)


class Documentos:
    def __init__(self, pasta=PASTA_ESTATICA):
        self.arquivo = os.path.join(pasta, "index.html")
        self._trava = threading.Lock()
        self._guardado = (None, {})

    def _modelos(self):
        assinatura = os.stat(self.arquivo).st_mtime_ns
        with self._trava:
            if self._guardado[0] != assinatura:
                with open(self.arquivo, encoding="utf-8") as f:
                    texto = f.read()
                modelos = dict(re.findall(r'<template id="([\w-]+)">(.*?)</template>', texto, re.S))
                self._guardado = (assinatura, modelos)
            return self._guardado[1]

    def pagina(self, nome, valores):
        """A página inteira, pronta para enviar. `valores` preenche os campos data-info."""
        modelo, titulo = PAGINAS[nome]
        corpo = self._modelos()[modelo]

        def preencher(m):
            valor = valores.get(m.group(1))
            return html.escape(str(valor)) if valor not in (None, "") else m.group(2)

        corpo = CAMPO.sub(preencher, corpo)
        corpo = re.sub(r'href="(#[\w-]+)"', lambda m: f'href="{LINKS_SOLTOS.get(m.group(1), "/" + m.group(1))}"', corpo)
        rodape = "".join(f'<a href="/{n}">{html.escape(t)}</a>' for n, (_m, t) in PAGINAS.items())
        return ("<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                "<meta name=\"color-scheme\" content=\"dark\">\n<meta name=\"referrer\" content=\"no-referrer\">\n"
                f"<title>{html.escape(titulo)} | MeterWatt</title>\n"
                "<link rel=\"icon\" href=\"/estatico/icone.svg\" type=\"image/svg+xml\">\n"
                "<link rel=\"stylesheet\" href=\"/estatico/app.css\">\n</head>\n"
                "<body class=\"corpo-solto\">\n<div class=\"pagina-solta\">\n"
                "<header><a class=\"logo\" href=\"/\" aria-label=\"MeterWatt, início\"><span class=\"logo-raio\" aria-hidden=\"true\">"
                "</span>MeterWatt</a></header>\n"
                f"<main>{corpo}</main>\n"
                f"<footer class=\"pagina-solta-rodape\">{rodape}<a href=\"/#entrar\">Entrar no app</a></footer>\n"
                "</div>\n</body>\n</html>\n")
