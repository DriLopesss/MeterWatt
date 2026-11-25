"""Senhas que já apareceram em vazamentos de dados (Pwned Passwords, do Have I Been Pwned).

Quem tenta senhas roubadas de outros sites começa por elas. A consulta usa k-anonimato: só os 5 primeiros
caracteres do SHA-1 da senha saem do servidor; a resposta traz centenas de finais parecidos, e a conferência
acontece aqui. Com "Add-Padding", a resposta tem tamanho parecido para qualquer prefixo. A senha, nem o
resumo inteiro dela, nunca saem. Serviço: https://haveibeenpwned.com/API/v3#PwnedPasswords

Se o serviço estiver fora do ar ou demorar, a senha segue (as outras regras continuam valendo): uma falha
lá não pode impedir alguém de criar conta ou trocar a senha aqui.
"""
import hashlib
import logging
import urllib.error
import urllib.request

URL = "https://api.pwnedpasswords.com/range/"
TEMPO_S = 3
log = logging.getLogger("meterwatt.vazadas")


def consultar_http(prefixo):
    pedido = urllib.request.Request(URL + prefixo, headers={"Add-Padding": "true", "User-Agent": "MeterWatt"})
    with urllib.request.urlopen(pedido, timeout=TEMPO_S) as r:
        return r.read().decode("ascii", "replace")


class SenhasVazadas:
    def __init__(self, ligado, consultar=consultar_http):
        self.ligado = ligado
        self.consultar = consultar

    def vezes(self, senha):
        """Quantas vezes a senha aparece nos vazamentos conhecidos; 0 se não aparece; None se não deu para saber."""
        if not self.ligado or not isinstance(senha, str) or not senha:
            return None
        resumo = hashlib.sha1(senha.encode("utf-8")).hexdigest().upper()   # o formato do serviço, não para guardar senha
        prefixo, final = resumo[:5], resumo[5:]
        try:
            resposta = self.consultar(prefixo)
        except (urllib.error.URLError, OSError, ValueError) as erro:
            log.warning("Consulta de senhas vazadas falhou (%s): a senha seguiu sem essa conferência", type(erro).__name__)
            return None
        for linha in resposta.splitlines():
            parte, _, contagem = linha.strip().partition(":")
            if parte == final:
                try:
                    return int(contagem)          # o "padding" vem com contagem 0: não conta como vazada
                except ValueError:
                    return None
        return 0
