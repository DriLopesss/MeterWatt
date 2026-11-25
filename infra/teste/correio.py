"""Servidor de e-mail de mentira, só para o teste do conjunto Docker (tools/ensaio_docker.py).

Fala SMTP como um provedor de verdade no que importa ao MeterWatt: anuncia STARTTLS, recusa
MAIL antes do STARTTLS e usa um certificado (de uma autoridade criada pelo próprio teste, que o
aplicativo recebe por SSL_CERT_FILE). Não entrega nada: grava cada mensagem em /caixa.
Só biblioteca padrão. NÃO use fora do teste.
"""
import os
import socket
import ssl
import threading
import time

CERT = os.environ.get("CORREIO_CERT", "/certs/correio.pem")
CHAVE = os.environ.get("CORREIO_CHAVE", "/certs/correio-chave.pem")
CAIXA = os.environ.get("CORREIO_CAIXA", "/caixa")
PORTA = int(os.environ.get("CORREIO_PORTA", "2525"))


def atender(con):
    contexto = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    contexto.load_cert_chain(CERT, CHAVE)
    tls = False
    leitor = con.makefile("rb")

    def dizer(texto):
        con.sendall((texto + "\r\n").encode())

    dizer("220 correio-de-teste ESMTP")
    remetente, destinos = None, []
    while True:
        linha = leitor.readline()
        if not linha:
            return
        comando = linha.decode("utf-8", "replace").strip()
        verbo = comando.split(" ", 1)[0].upper()
        if verbo in ("EHLO", "HELO"):
            con.sendall(b"250-correio-de-teste\r\n" + (b"" if tls else b"250-STARTTLS\r\n") + b"250 8BITMIME\r\n")
        elif verbo == "STARTTLS" and not tls:
            dizer("220 pode comecar")
            con = contexto.wrap_socket(con, server_side=True)
            leitor = con.makefile("rb")
            tls = True
        elif verbo == "MAIL":
            if not tls:
                dizer("530 STARTTLS primeiro")
                continue
            remetente, destinos = comando[10:].strip(), []
            dizer("250 ok")
        elif verbo == "RCPT":
            destinos.append(comando[8:].strip())
            dizer("250 ok")
        elif verbo == "DATA":
            dizer("354 pode mandar")
            corpo = []
            while True:
                l = leitor.readline()
                if not l or l in (b".\r\n", b".\n"):
                    break
                corpo.append(l[1:] if l.startswith(b"..") else l)
            nome = os.path.join(CAIXA, f"{time.time():.6f}.eml")
            with open(nome, "wb") as f:
                f.writelines(corpo)
            print(f"recebido de {remetente} para {', '.join(destinos)}: {nome}", flush=True)
            dizer("250 guardado")
        elif verbo == "RSET":
            remetente, destinos = None, []
            dizer("250 ok")
        elif verbo == "NOOP":
            dizer("250 ok")
        elif verbo == "QUIT":
            dizer("221 tchau")
            return
        else:
            dizer("502 nao conheco")


def main():
    os.makedirs(CAIXA, exist_ok=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORTA))
    s.listen(5)
    print(f"correio de teste na porta {PORTA}", flush=True)
    while True:
        con, _ = s.accept()
        threading.Thread(target=lambda c=con: (atender(c), c.close()), daemon=True).start()


if __name__ == "__main__":
    main()
