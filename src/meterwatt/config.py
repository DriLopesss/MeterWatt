"""Configuração do servidor, lida de variáveis de ambiente (veja .env.exemplo).

Em produção (MW_MODO=producao) o servidor se recusa a iniciar sem chave de cifra,
endereço público HTTPS, cookies seguros e envio de e-mail por SMTP. É melhor não subir
do que subir inseguro.
"""
import os
from dataclasses import dataclass
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet


class ConfiguracaoInvalida(Exception):
    """A configuração não permite iniciar o servidor com segurança."""


def _bool(valor, padrao):
    if valor is None or str(valor).strip() == "":
        return padrao
    return str(valor).strip().lower() in ("1", "sim", "true", "yes", "on")


def _numero(valor, padrao=None):
    if valor is None or str(valor).strip() == "":
        return padrao
    return float(str(valor).replace(",", "."))


@dataclass(frozen=True)
class Config:
    modo: str
    pasta_instancia: str
    arquivo_banco: str
    url_publica: str
    http_host: str
    http_porta: int
    cookie_seguro: bool
    confiar_proxy: bool
    chave_cripto: bytes
    fuso: str
    tarifa_padrao: float | None
    mqtt_host: str
    mqtt_porta: int
    mqtt_usuario: str | None
    mqtt_senha: str | None
    mqtt_tls: bool
    email_modo: str
    smtp_host: str | None
    smtp_porta: int
    smtp_usuario: str | None
    smtp_senha: str | None
    remetente: str
    controlador: str | None
    email_privacidade: str | None
    encarregado: str | None
    exigir_2fa: bool
    retencao_bruta_horas: int
    retencao_agregada_dias: int
    retencao_agregada_longa_dias: int
    retencao_auditoria_dias: int
    retencao_conta_nao_confirmada_dias: int
    sessao_ociosa_horas: int
    sessao_maxima_dias: int
    # planos e cobrança
    pagamento_provedor: str = "desligado"        # desligado | simulado | mercadopago
    mp_access_token: str | None = None
    mp_webhook_segredo: str | None = None
    plano_preco: float = 14.90                   # valor de exemplo: quem decide o preço são vocês
    limite_medidores_gratis: int = 1
    limite_medidores_pago: int = 5
    # senha própria de cada medidor (Mosquitto com o plugin dynamic-security)
    mqtt_dynsec: bool = False
    mqtt_provisao_usuario: str | None = None
    mqtt_provisao_senha: str | None = None
    # cópias de segurança
    backup_automatico: bool = False
    backup_manter: int = 14

    @property
    def producao(self):
        return self.modo == "producao"

    @property
    def origem_publica(self):
        u = urlparse(self.url_publica)
        return f"{u.scheme}://{u.netloc}"

    @property
    def zona(self):
        return ZoneInfo(self.fuso)


def gerar_chave():
    """Chave nova para MW_CHAVE_CRIPTO (guarde fora do repositório)."""
    return Fernet.generate_key().decode()


def _chave_de_desenvolvimento(pasta):
    """Só em desenvolvimento: cria e reaproveita uma chave local, legível só pelo dono."""
    caminho = os.path.join(pasta, "chave-desenvolvimento.key")
    if os.path.exists(caminho):
        with open(caminho, "rb") as f:
            return f.read().strip()
    chave = Fernet.generate_key()
    descritor = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descritor, "wb") as f:
        f.write(chave)
    return chave


def carregar(env=None):
    env = os.environ if env is None else env
    modo = env.get("MW_MODO", "desenvolvimento").strip().lower()
    if modo not in ("desenvolvimento", "producao"):
        raise ConfiguracaoInvalida("MW_MODO precisa ser 'desenvolvimento' ou 'producao'.")
    producao = modo == "producao"

    pasta = os.path.abspath(env.get("MW_PASTA_INSTANCIA", "instancia"))
    os.makedirs(pasta, mode=0o700, exist_ok=True)
    porta = int(env.get("MW_HTTP_PORTA", "8000"))
    url = env.get("MW_URL_PUBLICA", f"http://localhost:{porta}").strip().rstrip("/")
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        raise ConfiguracaoInvalida("MW_URL_PUBLICA precisa ser um endereço completo, como https://painel.exemplo.com.br")
    cookie_seguro = _bool(env.get("MW_COOKIE_SEGURO"), producao)
    email_modo = env.get("MW_EMAIL_MODO", "smtp" if producao else "arquivo").strip().lower()
    chave = env.get("MW_CHAVE_CRIPTO", "").strip().encode()

    if producao:
        problemas = []
        if not chave:
            problemas.append("MW_CHAVE_CRIPTO não definida")
        if not url.startswith("https://"):
            problemas.append("MW_URL_PUBLICA precisa começar com https://")
        if not cookie_seguro:
            problemas.append("MW_COOKIE_SEGURO não pode ser desligado")
        if email_modo != "smtp":
            problemas.append("os e-mails precisam sair por SMTP (MW_EMAIL_MODO=smtp)")
        if problemas:
            raise ConfiguracaoInvalida("Produção recusada: " + "; ".join(problemas) + ".")
    if email_modo not in ("arquivo", "smtp"):
        raise ConfiguracaoInvalida("MW_EMAIL_MODO precisa ser 'arquivo' ou 'smtp'.")
    if email_modo == "smtp" and not env.get("MW_SMTP_HOST"):
        raise ConfiguracaoInvalida("MW_SMTP_HOST é obrigatório com MW_EMAIL_MODO=smtp.")
    if not chave:
        chave = _chave_de_desenvolvimento(pasta)
    try:
        Fernet(chave)
    except (ValueError, TypeError):
        raise ConfiguracaoInvalida("MW_CHAVE_CRIPTO inválida. Gere uma com: python -m meterwatt --gerar-chave") from None

    fuso = env.get("MW_FUSO", "America/Sao_Paulo")
    try:
        ZoneInfo(fuso)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfiguracaoInvalida(f"Fuso horário desconhecido: {fuso}. No Windows, instale o pacote tzdata.") from None

    provedor = env.get("MW_PAGAMENTO_PROVEDOR", "desligado" if producao else "simulado").strip().lower()
    if provedor not in ("desligado", "simulado", "mercadopago"):
        raise ConfiguracaoInvalida("MW_PAGAMENTO_PROVEDOR precisa ser 'desligado', 'simulado' ou 'mercadopago'.")
    if producao and provedor == "simulado":
        raise ConfiguracaoInvalida("Produção recusada: o pagamento simulado aprovaria assinaturas sem cobrar ninguém.")
    if provedor == "mercadopago" and not (env.get("MW_MP_ACCESS_TOKEN") and env.get("MW_MP_WEBHOOK_SEGREDO")):
        raise ConfiguracaoInvalida("Com MW_PAGAMENTO_PROVEDOR=mercadopago, defina MW_MP_ACCESS_TOKEN e MW_MP_WEBHOOK_SEGREDO.")
    preco = _numero(env.get("MW_PLANO_PRECO"), 14.90)
    if not 1 <= preco <= 1000:
        raise ConfiguracaoInvalida("MW_PLANO_PRECO precisa estar entre 1 e 1000 reais.")
    dynsec = _bool(env.get("MW_MQTT_DYNSEC"), False)
    if dynsec and not (env.get("MQTT_USUARIO") and env.get("MQTT_SENHA")):
        raise ConfiguracaoInvalida("Com MW_MQTT_DYNSEC=1, o servidor precisa de MQTT_USUARIO e MQTT_SENHA (o administrador do broker).")
    if dynsec and bool(env.get("MW_MQTT_PROVISAO_USUARIO")) != bool(env.get("MW_MQTT_PROVISAO_SENHA")):
        raise ConfiguracaoInvalida("Defina MW_MQTT_PROVISAO_USUARIO e MW_MQTT_PROVISAO_SENHA juntos.")

    tarifa = _numero(env.get("MW_TARIFA_PADRAO"))
    if tarifa is not None and not 0 < tarifa <= 10:
        raise ConfiguracaoInvalida("MW_TARIFA_PADRAO precisa estar entre 0 e 10 reais por kWh.")

    return Config(
        modo=modo,
        pasta_instancia=pasta,
        arquivo_banco=env.get("MW_BANCO", os.path.join(pasta, "meterwatt.sqlite3")),
        url_publica=url,
        http_host=env.get("MW_HTTP_HOST", "127.0.0.1"),
        http_porta=porta,
        cookie_seguro=cookie_seguro,
        confiar_proxy=_bool(env.get("MW_CONFIAR_PROXY"), False),
        chave_cripto=chave,
        fuso=fuso,
        tarifa_padrao=tarifa,
        mqtt_host=env.get("MQTT_HOST", "localhost"),
        mqtt_porta=int(env.get("MQTT_PORTA", "1883")),
        mqtt_usuario=env.get("MQTT_USUARIO") or None,
        mqtt_senha=env.get("MQTT_SENHA") or None,
        mqtt_tls=_bool(env.get("MQTT_TLS"), False),
        email_modo=email_modo,
        smtp_host=env.get("MW_SMTP_HOST") or None,
        smtp_porta=int(env.get("MW_SMTP_PORTA", "587")),
        smtp_usuario=env.get("MW_SMTP_USUARIO") or None,
        smtp_senha=env.get("MW_SMTP_SENHA") or None,
        remetente=env.get("MW_REMETENTE", "MeterWatt <nao-responda@localhost>"),
        controlador=env.get("MW_CONTROLADOR") or None,
        email_privacidade=env.get("MW_EMAIL_PRIVACIDADE") or None,
        encarregado=env.get("MW_ENCARREGADO") or None,
        exigir_2fa=_bool(env.get("MW_EXIGIR_2FA"), False),
        retencao_bruta_horas=int(env.get("MW_RETENCAO_BRUTA_HORAS", "48")),
        retencao_agregada_dias=int(env.get("MW_RETENCAO_AGREGADA_DIAS", "90")),
        retencao_agregada_longa_dias=int(env.get("MW_RETENCAO_AGREGADA_LONGA_DIAS", "730")),
        retencao_auditoria_dias=int(env.get("MW_RETENCAO_AUDITORIA_DIAS", "180")),
        retencao_conta_nao_confirmada_dias=int(env.get("MW_RETENCAO_CONTA_NAO_CONFIRMADA_DIAS", "7")),
        sessao_ociosa_horas=int(env.get("MW_SESSAO_OCIOSA_HORAS", "24")),
        sessao_maxima_dias=int(env.get("MW_SESSAO_MAXIMA_DIAS", "14")),
        pagamento_provedor=provedor,
        mp_access_token=env.get("MW_MP_ACCESS_TOKEN") or None,
        mp_webhook_segredo=env.get("MW_MP_WEBHOOK_SEGREDO") or None,
        plano_preco=round(preco, 2),
        limite_medidores_gratis=int(env.get("MW_LIMITE_MEDIDORES_GRATIS", "1")),
        limite_medidores_pago=int(env.get("MW_LIMITE_MEDIDORES_PAGO", "5")),
        mqtt_dynsec=dynsec,
        mqtt_provisao_usuario=env.get("MW_MQTT_PROVISAO_USUARIO") or None,
        mqtt_provisao_senha=env.get("MW_MQTT_PROVISAO_SENHA") or None,
        backup_automatico=_bool(env.get("MW_BACKUP_AUTOMATICO"), producao),
        backup_manter=max(1, int(env.get("MW_BACKUP_MANTER", "14"))),
    )
