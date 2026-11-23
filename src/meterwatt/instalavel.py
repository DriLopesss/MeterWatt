"""O app instalável (PWA): o service worker, a casca guardada no aparelho e o vínculo com o app da Play Store.

A casca é o mínimo para o app abrir sem internet: a página, o estilo, o código, as fontes e os ícones.
A versão do service worker é um resumo do conteúdo desses arquivos. Basta mudar um deles para o
navegador perceber, baixar a casca nova inteira e trocar de uma vez (sem misturar arquivos de versões
diferentes). Nada de /api/ entra na casca: os dados da conta não ficam gravados no aparelho.
"""
import hashlib
import json
import os
import threading

from . import VERSAO

PASTA_ESTATICA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estatico")

# endereço publicado -> arquivo dentro de estatico/
CASCA = {
    "/": "index.html",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/estatico/app.css": "app.css",
    "/estatico/app.js": "app.js",
    "/estatico/economia-calculo.js": "economia-calculo.js",
    "/estatico/graficos.js": "graficos.js",
    # A demonstração (botão "Ver o app funcionando") sai da mesma versão do app e abre sem internet.
    "/estatico/demonstracao.js": "demonstracao.js",
    "/estatico/icone.svg": "icone.svg",
    "/estatico/app/icone-192.png": "app/icone-192.png",
    "/estatico/app/selo-notificacao-96.png": "app/selo-notificacao-96.png",
}


def _casca(pasta):
    arquivos = dict(CASCA)
    for nome in sorted(os.listdir(os.path.join(pasta, "fontes"))):
        if nome.endswith(".woff2"):
            arquivos[f"/estatico/fontes/{nome}"] = f"fontes/{nome}"
    return arquivos


class ServiceWorker:
    """Monta /sw.js: a lista da casca e a versão, seguidas do código de estatico/sw.js."""

    def __init__(self, pasta=PASTA_ESTATICA):
        self.pasta = pasta
        self._trava = threading.Lock()
        self._guardado = (None, None)   # (assinatura dos arquivos no disco, script pronto)

    def _assinatura(self, arquivos):
        estado = []
        for relativo in [*arquivos.values(), "sw.js"]:
            info = os.stat(os.path.join(self.pasta, relativo))
            estado.append((relativo, info.st_mtime_ns, info.st_size))
        return tuple(estado)

    def script(self):
        arquivos = _casca(self.pasta)
        assinatura = self._assinatura(arquivos)
        with self._trava:
            if self._guardado[0] == assinatura:
                return self._guardado[1]
            resumo = hashlib.sha256(VERSAO.encode())
            for relativo in [*arquivos.values(), "sw.js"]:
                with open(os.path.join(self.pasta, relativo), "rb") as f:
                    resumo.update(relativo.encode() + b"\x00" + f.read())
            versao = f"{VERSAO}-{resumo.hexdigest()[:12]}"
            with open(os.path.join(self.pasta, "sw.js"), encoding="utf-8") as f:
                codigo = f.read()
            pronto = f"self.MW = {json.dumps({'versao': versao, 'arquivos': list(arquivos)})};\n{codigo}"
            self._guardado = (assinatura, pronto)
            return pronto

    def versao(self):
        return self.script().split('"versao": "', 1)[1].split('"', 1)[0]


def assetlinks(pacote, impressoes):
    """Digital Asset Links: prova para o Android que o app da Play Store e o site são do mesmo dono.
    Com isso, o app abre o site em tela cheia, sem a barra de endereço (Trusted Web Activity)."""
    return [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": pacote, "sha256_cert_fingerprints": list(impressoes)}}]
