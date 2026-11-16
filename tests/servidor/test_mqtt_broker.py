"""Senha própria por medidor, contra um Mosquitto de verdade com o plugin dynamic-security.

Pulado quando o Mosquitto não está instalado (no CI ele é instalado antes dos testes)."""
import glob
import json
import shutil
import socket
import subprocess
import time

import paho.mqtt.client as mqtt
import pytest

from conftest import Cliente, conta_pronta
from meterwatt import config
from meterwatt import mqtt as mw_mqtt
from meterwatt.mensageiro import MensageiroMemoria
from meterwatt.plataforma import Plataforma
from meterwatt.web import criar_app

# Sem busca recursiva: em máquinas com muita coisa em /usr/lib ela não termina nunca.
PLUGIN = next((c for padrao in ("/usr/lib/mosquitto_dynamic_security.so", "/usr/lib/*/mosquitto_dynamic_security.so",
                                "/usr/lib64/mosquitto_dynamic_security.so", "/usr/local/lib/mosquitto_dynamic_security.so")
               for c in glob.glob(padrao)), None)
pytestmark = pytest.mark.skipif(not (shutil.which("mosquitto") and shutil.which("mosquitto_ctrl") and PLUGIN),
                                reason="Mosquitto com dynamic-security não está instalado")
MID = "mw-A1B2C3D4E5F6"
SEGREDO = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def esperar(condicao, segundos=8.0):
    fim = time.time() + segundos
    while time.time() < fim:
        if condicao():
            return True
        time.sleep(0.05)
    return False


class Aparelho:
    """Um medidor de mentira falando MQTT de verdade."""

    def __init__(self, porta, usuario, senha, client_id=MID):
        self.recebidas, self.codigo_conexao = [], None
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.c.username_pw_set(usuario, senha)
        self.c.on_connect = self._ao_conectar
        self.c.on_message = lambda _c, _u, m: self.recebidas.append(m.payload.decode())
        self.c.connect("127.0.0.1", porta)
        self.c.loop_start()
        esperar(lambda: self.codigo_conexao is not None)

    def _ao_conectar(self, c, _u, _f, codigo, _p=None):
        self.codigo_conexao = str(codigo)
        if codigo == 0:
            c.subscribe(f"meterwatt/{MID}/comando", 1)

    @property
    def conectado(self):
        return self.codigo_conexao == "Success"

    def publicar(self, parte, dados):
        self.c.publish(f"meterwatt/{MID}/{parte}", json.dumps(dados), qos=1).wait_for_publish(3)

    def fechar(self):
        self.c.loop_stop()
        self.c.disconnect()


@pytest.fixture
def broker(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    dynsec = tmp_path / "dynsec.json"
    subprocess.run(["mosquitto_ctrl", "dynsec", "init", str(dynsec), "meterwatt-admin", "senha-do-admin-123"], check=True,
                   capture_output=True)
    dynsec.chmod(0o644)
    conf = tmp_path / "mosquitto.conf"
    conf.write_text(f"listener {porta} 127.0.0.1\nallow_anonymous false\nuser root\nplugin {PLUGIN}\nplugin_opt_config_file {dynsec}\n")
    processo = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    yield porta
    processo.terminate()
    processo.wait(5)


@pytest.fixture
def servidor(broker, tmp_path):
    cfg = config.carregar({
        "MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MQTT_HOST": "127.0.0.1", "MQTT_PORTA": str(broker),
        "MQTT_USUARIO": "meterwatt-admin", "MQTT_SENHA": "senha-do-admin-123", "MW_MQTT_DYNSEC": "1",
        "MW_MQTT_PROVISAO_USUARIO": "provisao", "MW_MQTT_PROVISAO_SENHA": "senha-de-provisao"})
    p = Plataforma(cfg, MensageiroMemoria())
    cliente_mqtt = mw_mqtt.iniciar(p)
    http = Cliente(criar_app(p))
    conta_pronta(http, p)
    # pronto quando a conta de provisão existe e o servidor já ouve os pedidos de pareamento
    def provisao_funciona():
        a = Aparelho(broker, "provisao", "senha-de-provisao", client_id="teste-de-prontidao")
        ok = a.conectado
        a.fechar()
        return ok
    assert esperar(provisao_funciona, 10)
    time.sleep(1.2)          # o broker derruba o servidor uma vez ao dar o papel a ele; espera a volta
    assert esperar(cliente_mqtt.is_connected, 10)
    time.sleep(0.5)
    yield p, http, broker
    cliente_mqtt.loop_stop()
    cliente_mqtt.disconnect()


def leituras(p):
    try:
        return p.db().execute("SELECT COUNT(*) FROM leituras").fetchone()[0]
    finally:
        p.fechar_conexao_da_thread()


LEITURA = {"sensor_ok": True, "tensao_v": 127.0, "corrente_a": 1.0, "potencia_w": 120.0, "energia_wh": 10, "fp": 0.95, "frequencia_hz": 60.0}


def test_medidor_ganha_senha_propria_no_pareamento(servidor):
    p, http, porta = servidor
    codigo = http.post("/api/medidores/codigo").get_json()["codigo"]

    # 1. ainda sem conta própria: a senha sorteada não entra
    assert not Aparelho(porta, MID, SEGREDO).conectado

    # 2. com a conta de provisão, o medidor só consegue pedir o pareamento
    novo = Aparelho(porta, "provisao", "senha-de-provisao")
    assert novo.conectado
    novo.publicar("telemetria", LEITURA)
    novo.publicar("pareamento", {"codigo": codigo, "segredo": SEGREDO})
    assert esperar(lambda: "pareado_credencial" in novo.recebidas), novo.recebidas
    novo.fechar()
    assert leituras(p) == 0
    linha = p.db().execute("SELECT credencial_propria FROM medidores WHERE id = ?", (MID,)).fetchone()
    p.fechar_conexao_da_thread()
    assert linha["credencial_propria"] == 1

    # 3. agora ele entra com a senha própria e as leituras são guardadas
    proprio = Aparelho(porta, MID, SEGREDO)
    assert proprio.conectado
    proprio.publicar("telemetria", LEITURA)
    assert esperar(lambda: leituras(p) == 1)

    # 4. quem só tem a conta de provisão não consegue se passar por ele
    time.sleep(1.0)
    falso = Aparelho(porta, "provisao", "senha-de-provisao", client_id="outro-aparelho")
    falso.publicar("telemetria", {**LEITURA, "potencia_w": 9999.0})
    time.sleep(0.6)
    assert leituras(p) == 1
    falso.fechar()

    # 5. a senha do medidor só vale com o id de cliente dele, e senha errada não entra
    assert not Aparelho(porta, MID, SEGREDO, client_id="clone").conectado
    assert not Aparelho(porta, MID, "x" * 32).conectado

    # 6. removido da conta, a senha própria deixa de valer
    proprio.fechar()
    assert http.delete(f"/api/medidores/{MID}").status_code == 200
    assert esperar(lambda: not Aparelho(porta, MID, SEGREDO).conectado)


def test_codigo_errado_nao_cria_conta_no_broker(servidor):
    p, http, porta = servidor
    novo = Aparelho(porta, "provisao", "senha-de-provisao")
    novo.publicar("pareamento", {"codigo": "AAAA-AAAA", "segredo": SEGREDO})
    time.sleep(0.8)
    assert novo.recebidas == []
    novo.fechar()
    assert not Aparelho(porta, MID, SEGREDO).conectado


def test_medidor_de_outra_conta_nao_tem_a_senha_trocada(servidor):
    p, http, porta = servidor
    codigo = http.post("/api/medidores/codigo").get_json()["codigo"]
    dono = Aparelho(porta, "provisao", "senha-de-provisao")
    dono.publicar("pareamento", {"codigo": codigo, "segredo": SEGREDO})
    assert esperar(lambda: "pareado_credencial" in dono.recebidas)
    dono.fechar()
    # outra pessoa, com um código válido da conta dela, tenta tomar o mesmo medidor
    bia = conta_pronta(Cliente(http.http.application), p, email="bia@exemplo.com.br")
    codigo_bia = bia.post("/api/medidores/codigo").get_json()["codigo"]
    time.sleep(1.0)
    atacante = Aparelho(porta, "provisao", "senha-de-provisao", client_id="atacante")
    atacante.publicar("pareamento", {"codigo": codigo_bia, "segredo": "b" * 32})
    time.sleep(0.8)
    atacante.fechar()
    assert Aparelho(porta, MID, SEGREDO).conectado               # a senha do dono continua valendo
    assert not Aparelho(porta, MID, "b" * 32).conectado
