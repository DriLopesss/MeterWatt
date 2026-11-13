"""Envio de e-mails.

- "arquivo" (desenvolvimento): grava cada e-mail em instancia/caixa-de-saida e avisa no
  terminal. Nada sai do computador.
- "smtp" (produção): envia por um servidor SMTP com STARTTLS obrigatório.

Os textos nunca levam senha nem dado de consumo. Links levam o token depois do "#",
que o navegador não envia ao servidor nem a sites de terceiros.
"""
import logging
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

log = logging.getLogger("meterwatt.email")


class Mensageiro:
    def enviar(self, para, assunto, texto):
        raise NotImplementedError


class MensageiroArquivo(Mensageiro):
    def __init__(self, pasta):
        self.pasta = os.path.join(pasta, "caixa-de-saida")
        os.makedirs(self.pasta, mode=0o700, exist_ok=True)

    def enviar(self, para, assunto, texto):
        nome = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}-" + re.sub(r"[^a-z0-9]+", "-", assunto.lower())[:40] + ".txt"
        caminho = os.path.join(self.pasta, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(f"Para: {para}\nAssunto: {assunto}\n\n{texto}\n")
        print(f"[e-mail de teste] '{assunto}' gravado em {caminho}", flush=True)


class MensageiroSMTP(Mensageiro):
    def __init__(self, cfg):
        self.cfg = cfg

    def enviar(self, para, assunto, texto):
        msg = EmailMessage()
        msg["From"] = self.cfg.remetente
        msg["To"] = para
        msg["Subject"] = assunto
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.cfg.remetente.split("@")[-1].strip("> "))
        msg.set_content(texto)
        try:
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_porta, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if self.cfg.smtp_usuario:
                    smtp.login(self.cfg.smtp_usuario, self.cfg.smtp_senha or "")
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as erro:
            # Não registra o destinatário nem o texto: só que falhou.
            log.error("Falha ao enviar e-mail '%s': %s", assunto, type(erro).__name__)


class MensageiroMemoria(Mensageiro):
    """Para testes: guarda os e-mails numa lista."""

    def __init__(self):
        self.enviados = []

    def enviar(self, para, assunto, texto):
        self.enviados.append({"para": para, "assunto": assunto, "texto": texto})

    def ultimo_para(self, email):
        for m in reversed(self.enviados):
            if m["para"] == email:
                return m
        return None


def criar(cfg):
    if cfg.email_modo == "smtp":
        return MensageiroSMTP(cfg)
    return MensageiroArquivo(cfg.pasta_instancia)
