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
CLIENTE = MID + "-5e1a9c07"          # firmware 3.6: o id e um sufixo sorteado, que só o aparelho conhece
RESPOSTA = "0123456789abcdef"        # o fim do tópico onde o aparelho espera a resposta do pareamento
PEDIDO = {"segredo": SEGREDO, "cliente": CLIENTE, "resposta": RESPOSTA}


def esperar(condicao, segundos=8.0):
    fim = time.time() + segundos
    while time.time() < fim:
        if condicao():
            return True
        time.sleep(0.05)
    return False


class Aparelho:
    """Um medidor de mentira falando MQTT de verdade."""

    def __init__(self, porta, usuario, senha, client_id=CLIENTE, assinar=None):
        self.recebidas, self.codigo_conexao, self.caiu = [], None, False
        self.assinar = assinar or [f"meterwatt/{MID}/comando", f"meterwatt-resposta/{RESPOSTA}"]
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.c.username_pw_set(usuario, senha)
        self.c.on_connect = self._ao_conectar
        self.c.on_message = lambda _c, _u, m: self.recebidas.append((m.topic, m.payload.decode()))
        self.c.on_disconnect = lambda *_a: setattr(self, "caiu", True)
        self.c.connect("127.0.0.1", porta)
        self.c.loop_start()
        esperar(lambda: self.codigo_conexao is not None)

    def _ao_conectar(self, c, _u, _f, codigo, _p=None):
        self.codigo_conexao = str(codigo)
        if codigo == 0:
            for topico in self.assinar:
                c.subscribe(topico, 1)

    def textos(self):
        return [p for _t, p in self.recebidas]

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
    # Roda uma cópia do programa: no Ubuntu, o Mosquitto instalado em /usr/sbin é confinado pelo
    # AppArmor e não consegue ler uma configuração fora de /etc/mosquitto.
    binario = shutil.copy(shutil.which("mosquitto"), tmp_path / "mosquitto-de-teste")
    registro = tmp_path / "mosquitto.log"
    with open(registro, "w") as saida:
        processo = subprocess.Popen([binario, "-c", str(conf)], stdout=saida, stderr=subprocess.STDOUT)

    def ouvindo():
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=0.5).close()
            return True
        except OSError:
            return False
    if not esperar(ouvindo, 5):
        processo.kill()
        pytest.fail("O Mosquitto de teste não subiu:\n" + registro.read_text())
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
    novo.publicar("pareamento", {"codigo": codigo, **PEDIDO})
    assert esperar(lambda: ("meterwatt-resposta/" + RESPOSTA, "pareado_credencial") in novo.recebidas), novo.recebidas
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

    # 5. a senha do medidor só vale com o id de cliente dele (com o sufixo), e senha errada não entra
    assert not Aparelho(porta, MID, SEGREDO, client_id="clone").conectado
    assert not Aparelho(porta, MID, SEGREDO, client_id=MID).conectado
    assert not Aparelho(porta, MID, "x" * 32).conectado

    # 6. removido da conta, a senha própria deixa de valer
    proprio.fechar()
    assert http.delete(f"/api/medidores/{MID}").status_code == 200
    assert esperar(lambda: not Aparelho(porta, MID, SEGREDO).conectado)


def test_codigo_errado_nao_cria_conta_no_broker(servidor):
    p, http, porta = servidor
    novo = Aparelho(porta, "provisao", "senha-de-provisao")
    novo.publicar("pareamento", {"codigo": "AAAA-AAAA", **PEDIDO})
    time.sleep(0.8)
    assert novo.recebidas == []
    novo.fechar()
    assert not Aparelho(porta, MID, SEGREDO).conectado


def test_medidor_de_outra_conta_nao_tem_a_senha_trocada(servidor):
    p, http, porta = servidor
    codigo = http.post("/api/medidores/codigo").get_json()["codigo"]
    dono = Aparelho(porta, "provisao", "senha-de-provisao")
    dono.publicar("pareamento", {"codigo": codigo, **PEDIDO})
    assert esperar(lambda: "pareado_credencial" in dono.textos())
    dono.fechar()
    # outra pessoa, com um código válido da conta dela, tenta tomar o mesmo medidor
    bia = conta_pronta(Cliente(http.http.application), p, email="bia@exemplo.com.br")
    codigo_bia = bia.post("/api/medidores/codigo").get_json()["codigo"]
    time.sleep(1.0)
    atacante = Aparelho(porta, "provisao", "senha-de-provisao", client_id="atacante")
    atacante.publicar("pareamento", {"codigo": codigo_bia, "segredo": "b" * 32, "cliente": MID + "-00000000", "resposta": "f" * 16})
    time.sleep(0.8)
    atacante.fechar()
    assert Aparelho(porta, MID, SEGREDO).conectado               # a senha do dono continua valendo
    assert not Aparelho(porta, MID, "b" * 32).conectado


def parear_dono(http, porta):
    codigo = http.post("/api/medidores/codigo").get_json()["codigo"]
    a = Aparelho(porta, "provisao", "senha-de-provisao")
    a.publicar("pareamento", {"codigo": codigo, **PEDIDO})
    assert esperar(lambda: "pareado_credencial" in a.textos())
    a.fechar()
    time.sleep(1.0)
    proprio = Aparelho(porta, MID, SEGREDO)
    assert proprio.conectado
    return proprio


def test_provisao_nao_espiona_os_comandos_de_todos(servidor):
    """Revisão de 25/11: a conta de provisão (a senha vai em todo medidor) assinava meterwatt/+/comando."""
    _p, http, porta = servidor
    espiao = Aparelho(porta, "provisao", "senha-de-provisao", client_id="espiao", assinar=["meterwatt/+/comando", "#"])
    proprio = parear_dono(http, porta)
    proprio.fechar()
    assert http.delete(f"/api/medidores/{MID}").status_code == 200        # o servidor publica "despareado"
    time.sleep(1.0)
    espiao.fechar()
    vistos = [t for t, _ in espiao.recebidas if t.startswith("meterwatt/")]
    assert vistos == [], vistos                                               # nenhum id de medidor vazou


def test_provisao_nao_derruba_o_medidor_pelo_id(servidor):
    """Revisão de 25/11: entrar com o id de cliente de um medidor derrubava a conexão dele."""
    _p, http, porta = servidor
    proprio = parear_dono(http, porta)
    intruso = Aparelho(porta, "provisao", "senha-de-provisao", client_id=MID)   # o id que aparece na rede MeterWatt-XXXX
    time.sleep(1.0)
    assert not proprio.caiu
    intruso.fechar()
    proprio.fechar()


def test_aparelho_removido_so_pareia_de_novo_ele_mesmo(servidor):
    """Revisão de 25/11: depois de removido, outro aparelho com o mesmo id podia ser pareado em outra conta."""
    p, http, porta = servidor
    proprio = parear_dono(http, porta)
    proprio.fechar()
    assert http.delete(f"/api/medidores/{MID}").status_code == 200
    bia = conta_pronta(Cliente(http.http.application), p, email="bia@exemplo.com.br")
    codigo_bia = bia.post("/api/medidores/codigo").get_json()["codigo"]
    atacante = Aparelho(porta, "provisao", "senha-de-provisao", client_id="atacante", assinar=["meterwatt-resposta/" + "e" * 16])
    atacante.publicar("pareamento", {"codigo": codigo_bia, "segredo": "b" * 32, "cliente": MID + "-11111111", "resposta": "e" * 16})
    time.sleep(1.0)
    atacante.fechar()
    assert atacante.recebidas == []
    assert bia.get("/api/medidores").get_json()["medidores"] == []
    # O aparelho de verdade (mesmo segredo) pareia de novo, em qualquer conta.
    codigo_bia = bia.post("/api/medidores/codigo").get_json()["codigo"]
    volta = Aparelho(porta, "provisao", "senha-de-provisao")
    volta.publicar("pareamento", {"codigo": codigo_bia, **PEDIDO})
    assert esperar(lambda: "pareado_credencial" in volta.textos())
    volta.fechar()
    assert [m["id"] for m in bia.get("/api/medidores").get_json()["medidores"]] == [MID]
