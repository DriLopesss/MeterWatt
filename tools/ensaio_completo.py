"""Ensaio geral do caminho de verdade, sem placa: Mosquitto + servidor + dois medidores simulados.

    python tools/ensaio_completo.py            # tudo numa pasta temporária, apagada no fim
    python tools/ensaio_completo.py --manter   # guarda a pasta (registros, banco, e-mails) para olhar depois

Diferente dos testes automáticos, aqui cada peça roda como vai rodar no servidor: o Mosquitto com o plugin
dynamic-security e a porta 8883 criptografada (com uma autoridade criada só para o ensaio), o aplicativo
com `python -m meterwatt` e os medidores com `tools/simulador_pzem.py`, falando MQTT pela rede. O roteiro:

 1. cria a conta pelo site (e-mail de confirmação lido da caixa de saída) e entra;
 2. pareia o medidor 1 pela porta 8883: ele ganha a senha própria e volta a conectar com ela;
 3. confere o painel: leituras chegando, "para onde vai agora", meta, perfil do dia, Tarifa Branca;
 4. assina o plano Casa com o pagamento de teste e pareia o medidor 2 como circuito do 1;
 5. segurança: o medidor 2 não consegue publicar no lugar do 1, e a conta de provisão não publica leituras;
 6. desliga o medidor 2 (o "testamento" do MQTT avisa que caiu) e confere o "sem sinal";
 7. registra a bandeira do mês pela linha de comando e confere a meta;
 8. remove o medidor 2: a senha própria dele deixa de valer;
 9. baixa a cópia dos dados e tira uma cópia de segurança.

Precisa do Mosquitto 2 com o plugin dynamic-security (no Ubuntu: apt install mosquitto mosquitto-clients)
e das dependências de src/requirements.txt. Linux e macOS; no Windows, use o WSL.
"""
import argparse
import datetime
import glob
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SIMULADOR = os.path.join(RAIZ, "tools", "simulador_pzem.py")
PLUGINS = ("/usr/lib/mosquitto_dynamic_security.so", "/usr/lib/*/mosquitto_dynamic_security.so",
           "/usr/lib64/mosquitto_dynamic_security.so", "/usr/local/lib/mosquitto_dynamic_security.so",
           "/opt/homebrew/lib/mosquitto_dynamic_security.so", "/usr/local/opt/mosquitto/lib/mosquitto_dynamic_security.so")
ADMIN, ADMIN_SENHA = "meterwatt-admin", "ensaio-admin-123"
PROVISAO, PROVISAO_SENHA = "provisao", "ensaio-provisao-123"
EMAIL, SENHA = "ensaio@exemplo.com.br", "Lampada-azul-2026"
M1, M2 = "mw-ENSAIO01", "mw-ENSAIO02"

resultados = []


def passo(texto, ok, detalhe=""):
    resultados.append((texto, ok))
    print(f"{'ok   ' if ok else 'FALHA'} {texto}" + (f"  ({detalhe})" if detalhe else ""), flush=True)
    return ok


def esperar(condicao, segundos=20.0, intervalo=0.25):
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


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ouvindo(porta):
    try:
        socket.create_connection(("127.0.0.1", porta), timeout=0.5).close()
        return True
    except OSError:
        return False


# ------------------------------------------------------------ certificados do ensaio

def criar_certificados(pasta):
    """Uma autoridade só para o ensaio e o certificado do broker para 'localhost'."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    agora = datetime.datetime.now(datetime.timezone.utc)
    chave_ca = ec.generate_private_key(ec.SECP256R1())
    nome_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Autoridade do ensaio MeterWatt")])
    ca = (x509.CertificateBuilder().subject_name(nome_ca).issuer_name(nome_ca).public_key(chave_ca.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(minutes=5))
          .not_valid_after(agora + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(chave_ca, hashes.SHA256()))
    chave = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(nome_ca).public_key(chave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(agora - datetime.timedelta(minutes=5)).not_valid_after(agora + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(chave_ca, hashes.SHA256()))
    caminhos = {nome: os.path.join(pasta, nome) for nome in ("ca.pem", "broker.pem", "broker.key")}
    with open(caminhos["ca.pem"], "wb") as f:
        f.write(ca.public_bytes(serialization.Encoding.PEM))
    with open(caminhos["broker.pem"], "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(caminhos["broker.key"], "wb") as f:
        f.write(chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    return caminhos


# ------------------------------------------------------------ o site, por HTTP de verdade

class Site:
    def __init__(self, url):
        self.url = url
        self.csrf = ""
        self.abridor = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def chamar(self, metodo, caminho, corpo=None):
        dados = None if corpo is None else json.dumps(corpo).encode()
        pedido = urllib.request.Request(self.url + caminho, data=dados, method=metodo)
        pedido.add_header("Content-Type", "application/json")
        if self.csrf:
            pedido.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.abridor.open(pedido, timeout=15) as r:
                texto = r.read().decode()
                return r.status, (json.loads(texto) if texto.startswith(("{", "[")) else texto)
        except urllib.error.HTTPError as e:
            texto = e.read().decode()
            return e.code, (json.loads(texto) if texto.startswith("{") else texto)

    def get(self, caminho):
        return self.chamar("GET", caminho)[1]


def ultimo_email(pasta, assunto):
    arquivos = sorted(glob.glob(os.path.join(pasta, "caixa-de-saida", "*.txt")))
    for caminho in reversed(arquivos):
        texto = open(caminho, encoding="utf-8").read()
        if assunto.lower() in texto.lower():
            return texto
    return None


def token_do_link(texto, rota):
    marcador = f"/#{rota}/"
    return texto.split(marcador, 1)[1].split()[0] if texto and marcador in texto else None


# ------------------------------------------------------------ o ensaio

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manter", action="store_true", help="não apaga a pasta do ensaio no fim")
    args = ap.parse_args()

    plugin = next((c for p in PLUGINS for c in glob.glob(p)), None)
    if not (shutil.which("mosquitto") and shutil.which("mosquitto_ctrl") and plugin):
        print("Precisa do Mosquitto 2 com o plugin dynamic-security (mosquitto, mosquitto_ctrl e o .so do plugin).")
        return 2

    pasta = tempfile.mkdtemp(prefix="meterwatt-ensaio-")
    processos = []
    registros = {}

    def iniciar(nome, comando, env=None, cwd=None):
        registros[nome] = os.path.join(pasta, f"{nome}.log")
        saida = open(registros[nome], "w")
        p = subprocess.Popen(comando, stdout=saida, stderr=subprocess.STDOUT, env=env, cwd=cwd)
        processos.append((nome, p))
        return p

    def log(nome):
        return open(registros[nome], encoding="utf-8", errors="replace").read()

    try:
        print(f"Pasta do ensaio: {pasta}", flush=True)
        certs = criar_certificados(pasta)
        porta_local, porta_tls, porta_http = porta_livre(), porta_livre(), porta_livre()

        # 1) Mosquitto: 1883 só para o servidor (nesta máquina), 8883 criptografada para os medidores.
        dynsec = os.path.join(pasta, "dynsec.json")
        subprocess.run(["mosquitto_ctrl", "dynsec", "init", dynsec, ADMIN, ADMIN_SENHA], check=True, capture_output=True)
        os.chmod(dynsec, 0o644)
        conf = os.path.join(pasta, "mosquitto.conf")
        with open(conf, "w") as f:
            f.write(f"per_listener_settings false\nallow_anonymous false\nplugin {plugin}\nplugin_opt_config_file {dynsec}\n"
                    f"listener {porta_local} 127.0.0.1\n"
                    f"listener {porta_tls} 127.0.0.1\ncafile {certs['ca.pem']}\ncertfile {certs['broker.pem']}\n"
                    f"keyfile {certs['broker.key']}\n")
            if os.geteuid() == 0:
                f.write("user root\n")
        # Uma cópia do programa: no Ubuntu, o Mosquitto de /usr/sbin é confinado pelo AppArmor.
        binario = shutil.copy(shutil.which("mosquitto"), os.path.join(pasta, "mosquitto-do-ensaio"))
        iniciar("mosquitto", [binario, "-c", conf])
        if not passo("Mosquitto no ar, com a porta criptografada", esperar(lambda: ouvindo(porta_local) and ouvindo(porta_tls), 10)):
            print(log("mosquitto"))
            return 1

        # 2) O aplicativo, como no servidor.
        instancia = os.path.join(pasta, "instancia")
        env = {**os.environ, "PYTHONPATH": os.path.join(RAIZ, "src"), "MW_PASTA_INSTANCIA": instancia,
               "MW_HTTP_PORTA": str(porta_http), "MW_HTTP_HOST": "127.0.0.1", "MW_URL_PUBLICA": f"http://localhost:{porta_http}",
               "MQTT_HOST": "127.0.0.1", "MQTT_PORTA": str(porta_local), "MQTT_USUARIO": ADMIN, "MQTT_SENHA": ADMIN_SENHA,
               "MW_MQTT_DYNSEC": "1", "MW_MQTT_PROVISAO_USUARIO": PROVISAO, "MW_MQTT_PROVISAO_SENHA": PROVISAO_SENHA,
               "MW_TARIFA_PADRAO": "0.92", "PYTHONUNBUFFERED": "1"}
        for chave in ("MW_MODO", "MW_BANCO", "MQTT_TLS"):
            env.pop(chave, None)
        iniciar("servidor", [sys.executable, "-m", "meterwatt"], env=env, cwd=os.path.join(RAIZ, "src"))
        site = Site(f"http://127.0.0.1:{porta_http}")
        if not passo("Servidor no ar", esperar(lambda: site.chamar("GET", "/api/publico/info")[0] == 200, 30)):
            print(log("servidor"))
            return 1

        # 3) Conta: cadastro, e-mail de confirmação, entrada.
        status, _ = site.chamar("POST", "/api/conta/cadastro", {"nome": "Ensaio", "email": EMAIL, "senha": SENHA,
                                                               "aceites": {"termos": True, "privacidade": True, "consumo": True, "aparelhos": True}})
        token = token_do_link(esperar(lambda: ultimo_email(instancia, "confirm"), 10), "confirmar")
        confirmou = token and site.chamar("POST", "/api/conta/confirmar", {"token": token})[0] == 200
        status_entrar, r = site.chamar("POST", "/api/conta/entrar", {"email": EMAIL, "senha": SENHA})
        site.csrf = r.get("csrf", "") if isinstance(r, dict) else ""
        passo("Conta criada, confirmada pelo e-mail e com a entrada feita", status == 202 and confirmou and status_entrar == 200)

        # 4) Medidor 1 pela porta criptografada: pareia, ganha a senha própria e reconecta com ela.
        env_sim = {**os.environ, "MQTT_HOST": "localhost", "MQTT_PORTA": str(porta_tls), "MQTT_TLS": "1", "MQTT_CA": certs["ca.pem"],
                   "MQTT_USUARIO": PROVISAO, "MQTT_SENHA": PROVISAO_SENHA, "PYTHONUNBUFFERED": "1"}

        def parear(mid, semente, *extra):
            codigo = site.chamar("POST", "/api/medidores/codigo")[1]["codigo"]
            iniciar(mid, [sys.executable, SIMULADOR, "--id", mid, "--intervalo", "1", "--semente", str(semente), "--parear", codigo, *extra],
                    env=env_sim, cwd=pasta)
            return esperar(lambda: any(m["id"] == mid for m in site.get("/api/medidores")["medidores"]), 40)

        def medidor(mid):
            return next((m for m in site.get("/api/medidores")["medidores"] if m["id"] == mid), None)

        passo("Medidor 1 (casa bifásica, dois PZEM) pareado pela porta 8883", parear(M1, 1, "--fases", "2"))
        passo("Medidor 1 ganhou a senha própria e voltou a conectar com ela",
              esperar(lambda: "Reconectando com ela" in log(M1) and "recusou" not in log(M1), 20), "veja o registro do simulador")
        vivo = esperar(lambda: (m := medidor(M1)) and m["online"] and m["potencia"] is not None and m, 20)
        passo("Leituras do medidor 1 chegando ao painel", vivo, f"{vivo['potencia']} W" if vivo else "")
        fases = vivo and vivo.get("fases") or []
        soma = sum(f["potencia_w"] for f in fases if f["ok"])
        passo("Medidor 1 mostra as duas fases, e o total é a soma delas",
              len(fases) == 2 and all(f["ok"] for f in fases) and abs(soma - vivo["potencia"]) < 0.5,
              " + ".join(f"{f['potencia_w']} W" for f in fases))

        partes = esperar(lambda: (p := site.get("/api/agora/partes"))["disponivel"] and p, 15)
        passo("Para onde vai agora com o medidor 1", partes and partes["fonte"] in ("detetive", "base"), partes and partes["fonte"])
        meta = site.get("/api/meta")
        passo("Meta do mês calcula a previsão", not meta.get("sem_tarifa") and meta.get("projecao") is not None)
        horas = site.get("/api/consumo/horas?dias=14")
        passo("Perfil do dia com a hora de agora medida", horas["hoje"]["horas"][horas["hora_atual"]] is not None)
        tb = site.get("/api/economia/tarifa-branca")
        passo("Tarifa Branca responde (e pede mais dias medidos)", tb["disponivel"] is False and "dias" in tb["motivo"])

        # 5) Plano Casa com o pagamento de teste, medidor 2 como circuito do 1.
        link = site.chamar("POST", "/api/plano/assinar")[1].get("link", "")
        referencia = link.rsplit("/", 1)[-1]
        site.chamar("POST", "/api/plano/pagamento-teste", {"referencia": referencia, "aprovado": True})
        passo("Plano Casa ativo com o pagamento de teste", site.get("/api/plano").get("plano") == "casa")
        passo("Medidor 2 pareado", parear(M2, 2))
        st, _ = site.chamar("PATCH", f"/api/medidores/{M2}", {"papel": "circuito"})
        passo("Medidor 2 marcado como circuito dentro do 1", st == 200 and medidor(M2)["papel"] == "circuito")
        esperar(lambda: (m := medidor(M2)) and m["online"], 20)

        def balanço():
            p = site.get("/api/agora/partes")
            m1 = medidor(M1)
            if not (p["disponivel"] and p["fonte"] == "circuitos" and m1["potencia"] is not None):
                return None
            return p, m1
        r = esperar(balanço, 20)
        if r:
            p, m1 = r
            nomes = [x["nome"] for x in p["partes"]]
            # O total é o da entrada (o circuito não soma de novo). As duas leituras chegam em instantes diferentes.
            passo("Para onde vai agora mostra o circuito e o resto da casa", "Resto da casa" in nomes, ", ".join(nomes))
            passo("O total é só o da entrada (o circuito não soma de novo)", abs(p["total"] - m1["potencia"]) <= max(30, 0.25 * m1["potencia"]),
                  f"total {p['total']} W, entrada {m1['potencia']} W")
        else:
            passo("Para onde vai agora mostra o circuito e o resto da casa", False)

        # 6) Segurança: o medidor 2 publica no tópico do 1, e a provisão tenta mandar leitura. Nada pode entrar.
        import paho.mqtt.client as mqtt
        segredo2 = open(os.path.join(pasta, f".simulador-{M2}.segredo"), encoding="ascii").read().strip()

        def tentar(usuario, senha, client_id, topico):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
            c.username_pw_set(usuario, senha)
            c.tls_set(ca_certs=certs["ca.pem"])
            conectou = []
            c.on_connect = lambda *_a: conectou.append(str(_a[3]))
            c.connect("localhost", porta_tls)
            c.loop_start()
            esperar(lambda: conectou, 5)
            leitura = {"tipo": "telemetria", "sensor_ok": True, "tensao_v": 127.0, "corrente_a": 700.0, "potencia_w": 88888.0,
                       "energia_kwh": 1.0, "frequencia_hz": 60.0, "fp": 1.0, "simulado": True, "fw": "invasor"}
            if conectou and conectou[0] == "Success":
                c.publish(topico, json.dumps(leitura), qos=1)
                time.sleep(2)
            c.loop_stop()
            c.disconnect()
            return conectou[0] if conectou else "sem resposta"

        # A senha própria só vale com o id do próprio medidor: entra como o medidor 2 (derruba o simulador dele
        # por um instante) e tenta publicar no tópico do 1. A provisão entra, mas só pode pedir pareamento.
        como_m2 = tentar(M2, segredo2, M2, f"meterwatt/{M1}/telemetria")
        como_provisao = tentar(PROVISAO, PROVISAO_SENHA, "provisao-x", f"meterwatt/{M1}/telemetria")
        time.sleep(1.5)
        historico = site.get(f"/api/medidores/{M1}/historico?periodo=2h")["pontos"]
        passo("Medidor 2 e provisão conectam (o teste de permissão vale)", como_m2 == "Success" and como_provisao == "Success",
              f"medidor 2: {como_m2}; provisão: {como_provisao}")
        passo("Leitura falsa no tópico do medidor 1 não entrou (nem pelo medidor 2, nem pela provisão)",
              all(w < 20000 for _, w in historico) and (medidor(M1)["potencia"] or 0) < 20000)
        como_outro_id = tentar(M2, segredo2, M2 + "-x", f"meterwatt/{M2}/telemetria")
        passo("A senha do medidor 2 não vale com outro id de cliente", como_outro_id != "Success", como_outro_id)
        passo("Medidor 2 volta sozinho depois de ter sido derrubado", esperar(lambda: "recusou" not in log(M2) and medidor(M2)["online"], 30))

        # 7) Medidor 2 cai: o "testamento" do MQTT avisa o servidor.
        sim2 = next(p for nome, p in processos if nome == M2)
        sim2.kill()
        passo("Medidor 2 aparece sem sinal logo depois de cair", esperar(lambda: not medidor(M2)["online"], 25))

        # 8) Bandeira do mês pela linha de comando, no mesmo banco.
        mes = datetime.datetime.now().strftime("%Y-%m")
        cli = subprocess.run([sys.executable, "-m", "meterwatt", "--bandeira", mes, "amarela"], env=env, cwd=os.path.join(RAIZ, "src"),
                             capture_output=True, text=True, timeout=60)
        meta = site.get("/api/meta")
        passo("Bandeira registrada pela linha de comando aparece na meta",
              cli.returncode == 0 and meta.get("bandeira", {}).get("cor") == "amarela" and meta["bandeira"]["adicional"] >= 0,
              cli.stdout.strip() or cli.stderr.strip())

        # 9) Remover o medidor 2: a senha própria dele deixa de valer no broker.
        st, _ = site.chamar("DELETE", f"/api/medidores/{M2}")
        time.sleep(1.5)
        recusado = tentar(M2, segredo2, M2, f"meterwatt/{M2}/telemetria")
        passo("Medidor removido perde a senha própria", st == 200 and recusado != "Success", f"conexão: {recusado}")

        # 10) Direitos e cópia de segurança.
        st, copia = site.chamar("POST", "/api/privacidade/exportar")
        passo("Cópia dos dados com o medidor e as preferências", st == 200 and isinstance(copia, dict)
              and [m.get("id") for m in copia.get("medidores", [])] == [M1] and "tarifa_branca" in copia.get("conta", {}))
        backup = subprocess.run([sys.executable, "-m", "meterwatt", "--backup"], env=env, cwd=os.path.join(RAIZ, "src"),
                                capture_output=True, text=True, timeout=120)
        passo("Cópia de segurança tirada com o servidor ligado", backup.returncode == 0, backup.stdout.strip() or backup.stderr.strip())
        erros = [l for l in log("servidor").splitlines() if "Traceback" in l or " ERROR " in l]
        passo("Registro do servidor sem erros", not erros, "; ".join(erros[:3]))
    finally:
        for _nome, p in reversed(processos):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        if args.manter:
            print(f"Registros, banco e e-mails do ensaio ficaram em {pasta}")
        else:
            shutil.rmtree(pasta, ignore_errors=True)

    falhas = [t for t, ok in resultados if not ok]
    print(f"\n{len(resultados) - len(falhas)} de {len(resultados)} passos certos." + (f" Falharam: {'; '.join(falhas)}" if falhas else ""))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
