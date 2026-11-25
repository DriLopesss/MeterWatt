"""Ensaio do conjunto Docker, como vai rodar no servidor: Caddy (HTTPS), Mosquitto (8883 com TLS) e o app.

    python tools/ensaio_docker.py            # sobe, testa e desmonta tudo (volumes inclusive)
    python tools/ensaio_docker.py --manter   # deixa o conjunto no ar para olhar (docker compose -p meterwatt-ensaio ...)

Usa infra/docker-compose.yml de verdade, com o domínio meterwatt.localhost: o Caddy emite o certificado pela
autoridade interna dele (um domínio .localhost não vai à Let's Encrypt), e o Mosquitto pega esse certificado
pelo mesmo script de produção. O único acréscimo é infra/teste/docker-compose.teste.yml: um servidor de
e-mail de mentira com STARTTLS, porque o modo produção exige SMTP.

Roteiro: site em HTTPS com os cabeçalhos de segurança e o HTTP redirecionando; conta criada com o e-mail
lido do servidor de mentira; medidor simulado (duas fases) pareado pela 8883, ganhando a senha própria;
leituras no painel; a 1883 fechada para fora; cópia de segurança e restauração dentro do contêiner;
reinício do conjunto sem perder conta, sessão nem medidor; registros sem erro.

Precisa de Docker com o compose, de acesso ao Docker Hub, das dependências de src/requirements.txt e de
`127.0.0.1 meterwatt.localhost` no /etc/hosts (o job do GitHub faz isso). Recusa rodar se já existir um
infra/.env (que pode ser o de produção de vocês).
"""
import argparse
import email
import email.policy
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INFRA = os.path.join(RAIZ, "infra")
CERTS = os.path.join(INFRA, "teste", "certs")
ENV = os.path.join(INFRA, ".env")
SIMULADOR = os.path.join(RAIZ, "tools", "simulador_pzem.py")
DOMINIO = "meterwatt.localhost"
URL = f"https://{DOMINIO}"
PROJETO = "meterwatt-ensaio"
EMAIL, SENHA = "ensaio@exemplo.com.br", "Lampada-azul-2026"
MEDIDOR = "mw-DOCKER01"

resultados = []


def passo(texto, ok, detalhe=""):
    resultados.append((texto, bool(ok)))
    print(f"{'ok   ' if ok else 'FALHA'} {texto}" + (f"  ({detalhe})" if detalhe else ""), flush=True)
    return ok


def esperar(condicao, segundos=60.0, intervalo=1.0):
    fim = time.time() + segundos
    while time.time() < fim:
        try:
            r = condicao()
            if r:
                return r
        except Exception:
            pass
        time.sleep(intervalo)
    return None


def compose(*args, verificar=True, capturar=True, tempo=600):
    comando = ["docker", "compose", "-p", PROJETO, "-f", "docker-compose.yml", "-f", "teste/docker-compose.teste.yml", *args]
    r = subprocess.run(comando, cwd=INFRA, capture_output=capturar, text=True, timeout=tempo)
    if verificar and r.returncode:
        raise RuntimeError(f"{' '.join(args)} falhou:\n{r.stdout}\n{r.stderr}")
    return r


def openssl(*args):
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def criar_certificados():
    """Autoridade só do teste e o certificado do servidor de e-mail de mentira."""
    os.makedirs(CERTS, exist_ok=True)
    c = lambda nome: os.path.join(CERTS, nome)
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=MeterWatt ensaio Docker",
            "-keyout", c("ca-chave.pem"), "-out", c("ca.pem"),
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=correio", "-keyout", c("correio-chave.pem"), "-out", c("correio.csr"))
    with open(c("ext"), "w") as f:
        f.write("subjectAltName=DNS:correio\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n")
    openssl("x509", "-req", "-in", c("correio.csr"), "-CA", c("ca.pem"), "-CAkey", c("ca-chave.pem"), "-CAcreateserial",
            "-days", "2", "-extfile", c("ext"), "-out", c("correio.pem"))
    for nome in ("ca.pem", "correio.pem", "correio-chave.pem"):
        os.chmod(c(nome), 0o644)       # o contêiner do app roda com outro usuário; é só um teste


def criar_env(admin, admin_senha, provisao, provisao_senha):
    chave = subprocess.run([sys.executable, "-m", "meterwatt", "--gerar-chave"], capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONPATH": os.path.join(RAIZ, "src")}).stdout
    chave = re.search(r"\S{40,}", chave).group(0)
    with open(ENV, "w") as f:
        f.write(f"""DOMINIO={DOMINIO}
MW_CHAVE_CRIPTO={chave}
MQTT_USUARIO={admin}
MQTT_SENHA={admin_senha}
MW_MQTT_PROVISAO_USUARIO={provisao}
MW_MQTT_PROVISAO_SENHA={provisao_senha}
MW_EMAIL_MODO=smtp
MW_SMTP_HOST=correio
MW_SMTP_PORTA=2525
MW_REMETENTE=MeterWatt <nao-responda@exemplo.com.br>
MW_CONTROLADOR=Ensaio
MW_EMAIL_PRIVACIDADE=privacidade@exemplo.com.br
MW_TARIFA_PADRAO=0.92
""")
    os.chmod(ENV, 0o600)


class Site:
    def __init__(self, contexto):
        self.csrf = ""
        self.abridor = urllib.request.build_opener(urllib.request.HTTPSHandler(context=contexto),
                                                   urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def chamar(self, metodo, caminho, corpo=None, cru=False):
        dados = None if corpo is None else json.dumps(corpo).encode()
        pedido = urllib.request.Request(URL + caminho, data=dados, method=metodo)
        pedido.add_header("Content-Type", "application/json")
        pedido.add_header("Origin", URL)
        if self.csrf:
            pedido.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.abridor.open(pedido, timeout=20) as r:
                texto = r.read().decode()
                if cru:
                    return r.status, r.headers, texto
                corpo = json.loads(texto) if texto.startswith(("{", "[")) else texto
        except urllib.error.HTTPError as e:
            texto = e.read().decode()
            if cru:
                return e.code, e.headers, texto
            return e.code, (json.loads(texto) if texto.startswith("{") else texto)
        if isinstance(corpo, dict) and corpo.get("csrf"):
            self.csrf = corpo["csrf"]
        return r.status, corpo

    def get(self, caminho):
        return self.chamar("GET", caminho)[1]


def emails():
    """As mensagens que o servidor de mentira recebeu: (assunto, para, corpo)."""
    script = ("import email,email.policy,glob,json;print(json.dumps([(m['Subject'],m['To'],m.get_body(('plain',)).get_content()) "
              "for m in (email.message_from_bytes(open(f,'rb').read(),policy=email.policy.default) "
              "for f in sorted(glob.glob('/caixa/*.eml')))]))")
    r = compose("exec", "-T", "correio", "python", "-c", script)
    return json.loads(r.stdout)


def link(rota, assunto):
    for titulo, _para, corpo in reversed(emails()):
        if assunto.lower() in titulo.lower() and f"/#{rota}/" in corpo:
            return corpo.split(f"/#{rota}/", 1)[1].split()[0]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manter", action="store_true", help="não desmonta o conjunto no fim")
    args = ap.parse_args()
    if os.path.exists(ENV):
        print("Já existe infra/.env (pode ser o de produção). O ensaio não mexe nele: rode numa cópia do repositório.")
        return 2
    try:
        socket.getaddrinfo(DOMINIO, 443)
    except OSError:
        print(f"{DOMINIO} não resolve. Acrescente '127.0.0.1 {DOMINIO}' ao /etc/hosts.")
        return 2

    pasta = tempfile.mkdtemp(prefix="meterwatt-docker-")
    simulador = None
    admin, provisao = "meterwatt-servidor", "pareamento"
    admin_senha, provisao_senha = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    try:
        criar_certificados()
        criar_env(admin, admin_senha, provisao, provisao_senha)
        print("Construindo e subindo o conjunto (a primeira vez baixa as imagens)...", flush=True)
        compose("up", "-d", "--build", tempo=1200)
        passo("Conjunto no ar (app, mosquitto, caddy e o e-mail de mentira)", True)

        # 1) HTTPS pelo Caddy, com a autoridade interna dele.
        raiz = os.path.join(pasta, "caddy-raiz.pem")
        ok = esperar(lambda: compose("cp", "caddy:/data/caddy/pki/authorities/local/root.crt", raiz, verificar=False).returncode == 0
                     and os.path.getsize(raiz) > 0, 90)
        passo("Caddy criou a autoridade interna (domínio .localhost)", ok)
        contexto = ssl.create_default_context(cafile=raiz)
        site = Site(contexto)
        saude = esperar(lambda: site.chamar("GET", "/api/publico/saude", cru=True)[0] == 200, 120)
        passo("Site respondendo em HTTPS, pelo Caddy, com certificado válido", saude)
        status, cab, _ = site.chamar("GET", "/", cru=True)
        passo("Cabeçalhos de segurança chegam pelo Caddy (CSP e HSTS)",
              status == 200 and "default-src" in (cab.get("Content-Security-Policy") or "") and cab.get("Strict-Transport-Security"),
              (cab.get("Strict-Transport-Security") or "sem HSTS"))
        try:
            urllib.request.build_opener(type("SemRedirecionar", (urllib.request.HTTPRedirectHandler,),
                                             {"redirect_request": lambda *a, **k: None})).open(f"http://{DOMINIO}/", timeout=10)
            http_status, destino = 200, ""
        except urllib.error.HTTPError as e:
            http_status, destino = e.code, e.headers.get("Location", "")
        passo("HTTP (porta 80) redireciona para HTTPS", http_status in (301, 302, 307, 308) and destino.startswith("https://"),
              f"{http_status} {destino}")

        # 2) Conta, com o e-mail de confirmação saindo por SMTP com STARTTLS.
        st, _ = site.chamar("POST", "/api/conta/cadastro", {"nome": "Ensaio", "email": EMAIL, "senha": SENHA,
                                                            "aceites": {"termos": True, "privacidade": True, "consumo": True}})
        token = esperar(lambda: link("confirmar", "confirm"), 60, 2)
        passo("E-mail de confirmação saiu por SMTP com STARTTLS (certificado conferido)", st == 202 and token)
        confirmou = bool(token) and site.chamar("POST", "/api/conta/confirmar", {"token": token})[0] == 200
        st_entrar, r = site.chamar("POST", "/api/conta/entrar", {"email": EMAIL, "senha": SENHA})
        passo("Conta confirmada e entrada feita (cookie seguro, CSRF)", confirmou and st_entrar == 200 and r.get("etapa") == "completa")
        site.chamar("PUT", "/api/conta/preferencias", {"tarifa_kwh": 0.92, "meta_mensal_reais": 150, "ligacao": "bifasica"})

        # 3) Medidor simulado pela 8883 (TLS conferido com a autoridade do Caddy), com duas fases.
        codigo = site.chamar("POST", "/api/medidores/codigo")[1]["codigo"]
        env_sim = {**os.environ, "MQTT_HOST": DOMINIO, "MQTT_PORTA": "8883", "MQTT_TLS": "1", "MQTT_CA": raiz,
                   "MQTT_USUARIO": provisao, "MQTT_SENHA": provisao_senha, "PYTHONUNBUFFERED": "1"}
        registro_sim = open(os.path.join(pasta, "simulador.log"), "w")
        simulador = subprocess.Popen([sys.executable, SIMULADOR, "--id", MEDIDOR, "--semente", "7", "--fases", "2", "--parear", codigo],
                                     stdout=registro_sim, stderr=subprocess.STDOUT, env=env_sim, cwd=pasta)
        log_sim = lambda: open(os.path.join(pasta, "simulador.log"), encoding="utf-8", errors="replace").read()
        medidor = lambda: next((m for m in site.get("/api/medidores")["medidores"] if m["id"] == MEDIDOR), None)
        passo("Medidor pareado pela porta 8883 (Mosquitto com o certificado do Caddy)", esperar(medidor, 90, 2), log_sim()[-300:])
        passo("Medidor ganhou a senha própria e voltou com ela",
              esperar(lambda: "Reconectando com ela" in log_sim() and "recusou" not in log_sim(), 60, 2))
        vivo = esperar(lambda: (m := medidor()) and m["online"] and m["potencia"] is not None and m, 60, 2)
        passo("Leituras chegando ao painel, com as duas fases", vivo and len(vivo.get("fases") or []) == 2,
              f"{vivo['potencia']} W" if vivo else "")

        # 4) A 1883 (sem criptografia) não aparece para fora.
        try:
            socket.create_connection(("127.0.0.1", 1883), timeout=3).close()
            aberta = True
        except OSError:
            aberta = False
        passo("Porta 1883 fechada para fora do Docker", not aberta)

        # 5) Cópia de segurança e restauração dentro do contêiner.
        r = compose("exec", "-T", "app", "python", "-m", "meterwatt", "--backup", verificar=False)
        arquivo = re.search(r"(/dados/\S+\.mwbackup)", r.stdout + r.stderr)
        passo("Cópia de segurança cifrada tirada com o conjunto no ar", r.returncode == 0 and arquivo, (r.stdout + r.stderr).strip()[-160:])
        if arquivo:
            r = compose("exec", "-T", "app", "python", "-m", "meterwatt", "--restaurar", arquivo.group(1), verificar=False)
            passo("Cópia de segurança abre com a chave do .env", r.returncode == 0, (r.stdout + r.stderr).strip()[-160:])

        # 6) Reinício: conta, sessão e medidor continuam; o medidor volta sozinho.
        compose("restart", tempo=300)
        voltou = esperar(lambda: site.chamar("GET", "/api/conta/eu")[0] == 200, 120, 2)
        passo("Depois de reiniciar o conjunto, a sessão continua valendo", voltou)
        de_novo = esperar(lambda: (m := medidor()) and m["online"] and m["potencia"] is not None, 120, 2)
        passo("Depois de reiniciar, o medidor volta sozinho com a senha própria", de_novo, log_sim()[-300:] if not de_novo else "")

        # 7) Registros.
        registros = compose("logs", "--no-color", "app", "mosquitto", "caddy", verificar=False).stdout
        # Esperados, e só esses: o Caddy dá 502 enquanto o app sobe (o ensaio pergunta antes de ele ficar pronto)
        # e no reinício; o Mosquitto registra a conexão TLS cortada quando o conjunto reinicia.
        esperados = ("connect: connection refused", "unexpected eof while reading")
        ruins = [l for l in registros.splitlines() if re.search(r"Traceback|\bERROR\b|level\":\"error|Error", l)
                 and not any(e in l for e in esperados)]
        passo("Registros do app, do Mosquitto e do Caddy sem erros", not ruins, "; ".join(ruins[:3])[:400])
    except Exception as e:
        passo(f"O ensaio parou: {e}", False)
    finally:
        if simulador:
            simulador.terminate()
        if not all(ok for _, ok in resultados):
            print("\n--- registros do conjunto ---")
            print(compose("logs", "--no-color", "--tail", "60", verificar=False).stdout)
            try:
                print("--- simulador ---\n" + open(os.path.join(pasta, "simulador.log"), encoding="utf-8").read()[-1500:])
            except OSError:
                pass
        if not args.manter:
            compose("down", "-v", "--remove-orphans", verificar=False)
            for caminho in (ENV, CERTS):
                if os.path.isdir(caminho):
                    shutil.rmtree(caminho)
                elif os.path.exists(caminho):
                    os.remove(caminho)
            shutil.rmtree(pasta, ignore_errors=True)
        else:
            print(f"Conjunto no ar (projeto {PROJETO}); arquivos do ensaio em {pasta}. Para desmontar: "
                  f"cd infra && docker compose -p {PROJETO} -f docker-compose.yml -f teste/docker-compose.teste.yml down -v")
    certos = sum(ok for _, ok in resultados)
    print(f"\n{certos} de {len(resultados)} passos certos.")
    return 0 if resultados and certos == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
