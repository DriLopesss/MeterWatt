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

from .config import criar_pasta_privada

log = logging.getLogger("meterwatt.email")


class Mensageiro:
    def enviar(self, para, assunto, texto):
        raise NotImplementedError


class MensageiroArquivo(Mensageiro):
    """Cada e-mail vira um .txt. Ao ligar o servidor, os e-mails de vezes anteriores vão para a
    subpasta "antigos": assim a caixa de saída mostra só os desta vez, e ninguém abre por engano um
    link velho (que já foi usado ou trocado por um mais novo)."""

    def __init__(self, pasta):
        self.pasta = os.path.join(pasta, "caixa-de-saida")
        criar_pasta_privada(self.pasta)
        self._arquivar_antigos()

    def _arquivar_antigos(self):
        antigos = os.path.join(self.pasta, "antigos")
        for nome in os.listdir(self.pasta):
            if not nome.endswith(".txt"):
                continue
            try:
                criar_pasta_privada(antigos)
                os.replace(os.path.join(self.pasta, nome), os.path.join(antigos, nome))
            except OSError:
                pass   # arquivo aberto em outro programa: fica onde está

    def enviar(self, para, assunto, texto):
        nome = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}-" + re.sub(r"[^a-z0-9]+", "-", assunto.lower())[:40] + ".txt"
        caminho = os.path.join(self.pasta, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(f"Para: {para}\nAssunto: {assunto}\n\n{texto}\n")
        print(f"[e-mail de teste] '{assunto}' gravado em {caminho}", flush=True)
        for link in re.findall(r"https?://\S+", texto):
            print(f"    link: {link}", flush=True)   # dá para abrir direto do terminal (Ctrl+clique)


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
