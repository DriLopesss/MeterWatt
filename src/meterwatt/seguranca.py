"""Peças de segurança: senhas, tokens, verificação em duas etapas (TOTP), cifra e limites.

Escolhas e por quê:
- Senhas com Argon2id (parâmetros mínimos recomendados pela OWASP: 19 MiB, 2 iterações).
- Tokens de sessão e de e-mail aleatórios de 256 bits; no banco fica só o SHA-256 deles.
  Se o banco vazar, os tokens guardados não servem para entrar.
- Segredo do autenticador (TOTP) cifrado com Fernet (AES-128-CBC + HMAC-SHA256), com a
  chave em MW_CHAVE_CRIPTO, fora do banco.
- Códigos de backup e de pareamento guardados como HMAC com chave derivada da mesma
  chave: sem ela, não dá para testar códigos contra um banco vazado.
"""
import base64
import hashlib
import hmac
import ipaddress
import secrets
import struct
import threading
from collections import deque
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

# ---------------------------------------------------------------- senhas

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
_HASH_FALSO = _hasher.hash(secrets.token_hex(16))

SENHAS_COMUNS = {
    "1234567890", "12345678910", "0123456789", "123456789a", "1234567890a", "senha12345",
    "senha123456", "senhasenha", "qwertyuiop", "asdfghjkl", "password123", "password1234",
    "meterwatt", "meterwatt123", "brasil1234", "iloveyou12", "abcdefghij", "1q2w3e4r5t",
    "q1w2e3r4t5", "aaaaaaaaaa", "1111111111", "0000000000", "admin12345", "mudar12345",
}


def hash_senha(senha):
    return _hasher.hash(senha)


def confere_senha(senha_hash, senha):
    """Devolve (confere, precisa_atualizar_o_hash)."""
    try:
        _hasher.verify(senha_hash, senha)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    return True, _hasher.check_needs_rehash(senha_hash)


def gastar_tempo_de_senha(senha):
    """Mesmo custo de uma verificação real. Usado quando o e-mail não existe, para que o
    tempo de resposta não revele quais e-mails têm conta."""
    confere_senha(_HASH_FALSO, senha)


def problema_na_senha(senha, email=""):
    """Devolve a explicação do problema, ou None se a senha serve."""
    if not isinstance(senha, str):
        return "Senha inválida."
    if len(senha) < 10:
        return "A senha precisa ter pelo menos 10 caracteres."
    if len(senha) > 128:
        return "A senha pode ter no máximo 128 caracteres."
    baixa = senha.lower()
    if baixa in SENHAS_COMUNS or len(set(senha)) < 5:
        return "Essa senha é fácil de adivinhar. Use uma frase ou mais variedade de caracteres."
    parte_email = (email or "").split("@")[0].lower()
    if len(parte_email) >= 4 and parte_email in baixa:
        return "A senha não pode conter o seu e-mail."
    return None


# ---------------------------------------------------------------- tokens e códigos

def novo_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_com_formato_valido(token):
    return isinstance(token, str) and 20 <= len(token) <= 100 and all(
        c.isalnum() or c in "-_" for c in token)


_ALFABETO_PAREAMENTO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # sem 0/O, 1/I/L
_ALFABETO_BACKUP = "abcdefghjkmnpqrstuvwxyz23456789"


def novo_codigo_pareamento():
    """Oito caracteres em dois grupos, como ABCD-EF23 (cerca de 40 bits)."""
    c = "".join(secrets.choice(_ALFABETO_PAREAMENTO) for _ in range(8))
    return f"{c[:4]}-{c[4:]}"


def normalizar_codigo_pareamento(codigo):
    """Aceita minúsculas, espaços e traço. Devolve os 8 caracteres ou None."""
    if not isinstance(codigo, str) or len(codigo) > 20:
        return None
    limpo = "".join(c for c in codigo.upper() if c not in " -")
    if len(limpo) != 8 or any(c not in _ALFABETO_PAREAMENTO for c in limpo):
        return None
    return limpo


def novos_codigos_backup(quantidade=10):
    codigos = []
    for _ in range(quantidade):
        c = "".join(secrets.choice(_ALFABETO_BACKUP) for _ in range(10))
        codigos.append(f"{c[:5]}-{c[5:]}")
    return codigos


def normalizar_codigo_backup(codigo):
    if not isinstance(codigo, str) or len(codigo) > 20:
        return None
    limpo = "".join(c for c in codigo.lower() if c.isalnum())
    if len(limpo) != 10 or any(c not in _ALFABETO_BACKUP for c in limpo):
        return None
    return limpo


# ---------------------------------------------------------------- cifra e HMAC

class Cofre:
    def __init__(self, chave):
        self._fernet = Fernet(chave)
        bruta = base64.urlsafe_b64decode(chave)
        self._chave_hmac = hmac.new(bruta, b"meterwatt/hmac/v1", hashlib.sha256).digest()

    def cifrar(self, texto):
        return self._fernet.encrypt(texto.encode("utf-8"))

    def decifrar(self, dados):
        try:
            return self._fernet.decrypt(bytes(dados)).decode("utf-8")
        except InvalidToken:
            raise ValueError("dado cifrado com outra chave ou alterado") from None

    def hmac(self, texto):
        return hmac.new(self._chave_hmac, texto.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- TOTP (RFC 6238)

PERIODO_TOTP = 30
DIGITOS_TOTP = 6


def novo_segredo_totp():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(segredo_b32, contador, digitos=DIGITOS_TOTP):
    chave = base64.b32decode(segredo_b32 + "=" * (-len(segredo_b32) % 8), casefold=True)
    resumo = hmac.new(chave, struct.pack(">Q", contador), hashlib.sha1).digest()
    deslocamento = resumo[-1] & 0x0F
    numero = (struct.unpack(">I", resumo[deslocamento:deslocamento + 4])[0] & 0x7FFFFFFF) % (10 ** digitos)
    return str(numero).zfill(digitos)


def codigo_totp(segredo_b32, instante, digitos=DIGITOS_TOTP):
    return _hotp(segredo_b32, int(instante // PERIODO_TOTP), digitos)


def confere_totp(segredo_b32, codigo, instante, ultimo_passo=0, janela=1):
    """Devolve o passo de tempo aceito, ou None.

    Aceita um passo antes e um depois (relógio do celular adiantado ou atrasado) e
    recusa passos já usados: o mesmo código não entra duas vezes.
    """
    if not isinstance(codigo, str):
        return None
    codigo = "".join(c for c in codigo if c.isdigit())
    if len(codigo) != DIGITOS_TOTP:
        return None
    atual = int(instante // PERIODO_TOTP)
    for passo in range(atual - janela, atual + janela + 1):
        if passo <= ultimo_passo:
            continue
        if hmac.compare_digest(_hotp(segredo_b32, passo), codigo):
            return passo
    return None


def uri_totp(segredo_b32, email, emissor="MeterWatt"):
    rotulo = quote(f"{emissor}:{email}", safe="")
    return (f"otpauth://totp/{rotulo}?secret={segredo_b32}&issuer={quote(emissor, safe='')}"
            f"&algorithm=SHA1&digits={DIGITOS_TOTP}&period={PERIODO_TOTP}")


def qr_svg_data_uri(texto):
    import segno  # importado aqui: só é usado ao ativar a verificação em duas etapas
    return segno.make(texto, error="m").svg_data_uri(scale=5, border=2, dark="#0b0a12", light="#ffffff")


# ---------------------------------------------------------------- limites de tentativas

class Limitador:
    """Conta eventos por chave numa janela de tempo. Seguro entre threads.

    Fica na memória: reiniciar o servidor zera as contagens. Para mais de um processo,
    trocar por um armazenamento compartilhado (Redis, por exemplo).
    """

    def __init__(self, maximo, janela_s):
        self.maximo = maximo
        self.janela = janela_s
        self._eventos = {}
        self._trava = threading.Lock()

    def _limpar(self, fila, agora):
        while fila and fila[0] <= agora - self.janela:
            fila.popleft()

    def registrar(self, chave, agora):
        with self._trava:
            fila = self._eventos.setdefault(chave, deque())
            self._limpar(fila, agora)
            fila.append(agora)
            if len(self._eventos) > 50000:   # não deixa a memória crescer sem fim
                for k in [k for k, f in self._eventos.items() if not f or f[-1] <= agora - self.janela][:25000]:
                    del self._eventos[k]

    def esgotado(self, chave, agora):
        """Segundos até liberar, ou 0 se ainda há tentativas."""
        with self._trava:
            fila = self._eventos.get(chave)
            if not fila:
                return 0
            self._limpar(fila, agora)
            if len(fila) < self.maximo:
                return 0
            return max(1, int(fila[0] + self.janela - agora) + 1)

    def zerar(self, chave):
        with self._trava:
            self._eventos.pop(chave, None)


# ---------------------------------------------------------------- dados mínimos sobre o acesso

def resumir_ip(ip):
    """Guarda só a rede (/24 no IPv4, /48 no IPv6): ajuda a reconhecer um acesso estranho
    sem guardar o endereço exato de ninguém."""
    try:
        endereco = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return None
    if endereco.version == 6 and endereco.ipv4_mapped:
        endereco = endereco.ipv4_mapped
    prefixo = 24 if endereco.version == 4 else 48
    return str(ipaddress.ip_network(f"{endereco}/{prefixo}", strict=False))


def resumir_navegador(agente):
    """Só o tipo de navegador e de sistema, como 'Chrome no Android'."""
    agente = agente or ""
    navegador = next((nome for chave, nome in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                                ("Chrome/", "Chrome"), ("Safari/", "Safari")) if chave in agente), None)
    sistema = next((nome for chave, nome in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                              ("Windows", "Windows"), ("Mac OS X", "Mac"), ("Linux", "Linux")) if chave in agente), None)
    if navegador and sistema:
        return f"{navegador} no {sistema}"
    return navegador or sistema or "Navegador desconhecido"
