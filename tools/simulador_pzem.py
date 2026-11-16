"""Simulador de um MeterWatt com PZEM-004T v3.

Publica por MQTT as mesmas mensagens que o firmware `firmware/mwatt_pzem` publica,
para testar o backend e o painel antes de o módulo chegar (ou sem ligar nada na rede).

Toda mensagem leva `"simulado": true`, e o painel mostra esse aviso. Os números são
inventados por este programa: NÃO são medições.

Uso (com o broker e o backend já rodando):
    python tools/simulador_pzem.py
    python tools/simulador_pzem.py --tensao 220 --acelerar 20 --falhas

Usa as mesmas variáveis do backend: MQTT_HOST, MQTT_PORTA, MQTT_USUARIO, MQTT_SENHA.
"""
import argparse
import json
import math
import os
import random
import secrets
import threading
import time

import paho.mqtt.client as mqtt

# nome, potência (W), fator de potência, minutos ligado, minutos desligado, chance de ligar por minuto
CARGAS = [
    ("standby e roteador", 45, 0.62, None, None, None),     # sempre ligado
    ("geladeira", 140, 0.78, 12, 18, None),                 # ciclo do compressor
    ("iluminação", 60, 0.95, 40, 25, None),
    ("micro-ondas", 1300, 0.90, 2, None, 0.03),
    ("chuveiro", 5400, 1.00, 7, None, 0.012),
]


class Casa:
    def __init__(self, tensao_nominal, semente=None):
        self.rng = random.Random(semente)
        self.nominal = tensao_nominal
        self.t = 0.0
        self.energia_wh = float(self.rng.randint(12_000, 90_000))   # contador já "rodado"
        self.ate = {}   # carga eventual -> instante em que desliga

    def cargas_ligadas(self):
        ligadas = []
        minuto = self.t / 60
        for nome, p, fp, liga, desliga, chance in CARGAS:
            if liga is None:
                ligadas.append((p, fp))
            elif chance is None:
                if minuto % (liga + desliga) < liga:
                    ligadas.append((p, fp))
            elif self.ate.get(nome, -1) > self.t:
                ligadas.append((p, fp))
        return ligadas

    def passo(self, dt):
        """Avança `dt` segundos e devolve as medidas como o PZEM as devolveria."""
        self.t += dt
        for nome, _p, _fp, liga, _desl, chance in CARGAS:
            if chance and self.ate.get(nome, -1) <= self.t and self.rng.random() < chance * dt / 60:
                self.ate[nome] = self.t + liga * 60 * self.rng.uniform(0.6, 1.3)

        p_total = q_total = 0.0
        for p, fp in self.cargas_ligadas():
            p *= self.rng.uniform(0.985, 1.015)
            p_total += p
            q_total += p * math.tan(math.acos(fp))
        s_total = math.hypot(p_total, q_total)

        tensao = self.nominal * (1 + 0.012 * math.sin(self.t / 900)) + self.rng.uniform(-0.4, 0.4)
        corrente = s_total / tensao
        tensao -= 0.03 * corrente                       # a tensão cai um pouco com carga alta
        corrente = s_total / tensao
        self.energia_wh += p_total * dt / 3600
        return {
            "tensao_v": round(tensao, 1),
            "corrente_a": round(corrente, 3),
            "potencia_w": round(p_total, 1),
            "energia_wh": int(self.energia_wh),          # o PZEM conta em Wh inteiros
            "frequencia_hz": round(60 + self.rng.uniform(-0.05, 0.05), 1),
            "fp": round(p_total / s_total, 2) if s_total else 0.0,
            "alarme": False,
        }


def main():
    ap = argparse.ArgumentParser(description="Simulador de MeterWatt com PZEM-004T v3 (dados inventados).")
    ap.add_argument("--id", default="mw-SIMULADO", help="identificador do medidor simulado")
    ap.add_argument("--intervalo", type=float, default=5.0, help="segundos entre mensagens")
    ap.add_argument("--tensao", type=float, default=127.0, choices=[127.0, 220.0], help="tensão nominal da rede")
    ap.add_argument("--acelerar", type=float, default=1.0, help="quantas vezes o tempo da casa corre mais rápido")
    ap.add_argument("--falhas", action="store_true", help="de vez em quando simula o PZEM sem responder")
    ap.add_argument("--semente", type=int, default=None, help="para repetir a mesma sequência")
    ap.add_argument("--parear", metavar="CODIGO", default=None,
                    help="código do aplicativo (ex.: K7QM-2XPA): pede o pareamento como o firmware faz")
    ap.add_argument("--senha-propria", action="store_true",
                    help="entra com a senha própria deste medidor simulado (depois de um pareamento que a cadastrou)")
    args = ap.parse_args()

    host = os.getenv("MQTT_HOST", "localhost")
    porta = int(os.getenv("MQTT_PORTA", "1883"))
    base = f"meterwatt/{args.id}"

    # Como o firmware 3.3: o medidor sorteia um segredo uma vez e o guarda. Aqui ele fica num
    # arquivo ao lado (ignorado pelo Git), para o simulador continuar sendo "o mesmo medidor".
    arquivo_segredo = f".simulador-{args.id}.segredo"
    if os.path.exists(arquivo_segredo):
        segredo = open(arquivo_segredo, encoding="ascii").read().strip()
    else:
        segredo = secrets.token_hex(16)
        with open(arquivo_segredo, "w", encoding="ascii") as f:
            f.write(segredo)
        os.chmod(arquivo_segredo, 0o600)

    pareado = threading.Event()
    com_senha_propria = threading.Event()
    if args.senha_propria:
        com_senha_propria.set()

    def ao_receber(_c, _u, msg):
        if msg.payload in (b"pareado", b"pareado_credencial"):
            if msg.payload == b"pareado_credencial":
                com_senha_propria.set()
            pareado.set()
            print("Pareamento confirmado pelo servidor: o medidor já aparece no aplicativo.")

    def conectar():
        # O id de cliente é o próprio id do medidor: a senha própria só vale com ele.
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.id)
        if com_senha_propria.is_set():
            c.username_pw_set(args.id, segredo)
        elif os.getenv("MQTT_USUARIO"):
            c.username_pw_set(os.getenv("MQTT_USUARIO"), os.getenv("MQTT_SENHA"))
        if os.getenv("MQTT_TLS", "").lower() in ("1", "sim", "true"):
            c.tls_set()
        c.will_set(f"{base}/status", "offline", retain=True)
        c.on_message = ao_receber
        c.on_connect = lambda cl, _u, _f, codigo, _p=None: (
            cl.subscribe(f"{base}/comando"), cl.publish(f"{base}/status", "online", retain=True)) if codigo == 0 else print(
            f"O servidor MQTT recusou a conexão ({codigo}). Confira usuário e senha"
            + (", ou pareie de novo: a senha própria não vale mais." if com_senha_propria.is_set() else "."))
        c.connect(host, porta)
        c.loop_start()
        return c

    cliente = conectar()
    time.sleep(0.5)
    if args.parear:
        pedido = json.dumps({"codigo": args.parear.strip().upper(), "segredo": segredo})
        limite = time.time() + 15 * 60
        while not pareado.is_set() and time.time() < limite:   # igual ao firmware: repete a cada 30 s
            cliente.publish(f"{base}/pareamento", pedido, qos=1)
            print(f"Pedido de pareamento enviado ({args.parear}). Esperando o servidor...")
            pareado.wait(30)
        if not pareado.is_set():
            print("O servidor não confirmou em 15 minutos. Gere outro código no aplicativo.")
        elif com_senha_propria.is_set():
            print("O servidor cadastrou a senha própria deste medidor. Reconectando com ela...")
            cliente.loop_stop()
            cliente.disconnect()
            time.sleep(1)
            cliente = conectar()
            time.sleep(0.5)
            print("Nas próximas vezes, rode com --senha-propria (sem --parear).")

    casa = Casa(args.tensao, args.semente)
    inicio = time.time()
    falhas = 0
    print(f"Simulando {args.id} em {host}:{porta} a cada {args.intervalo} s. Ctrl+C para parar.")
    try:
        while True:
            comum = {"fw": "simulador", "sensor": "pzem004t-v3", "simulado": True,
                     "rssi": random.randint(-68, -52), "uptime_s": int(time.time() - inicio)}
            medidas = casa.passo(args.intervalo * args.acelerar)
            if args.falhas and random.random() < 0.08:
                falhas += 1
                msg = {**comum, "sensor_ok": False, "erro_sensor": "sem_resposta", "falhas_leitura": falhas}
            else:
                msg = {**comum, "sensor_ok": True, **medidas, "falhas_leitura": falhas}
            cliente.publish(f"{base}/telemetria", json.dumps(msg))
            print(json.dumps(msg, ensure_ascii=False))
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        pass
    finally:
        cliente.publish(f"{base}/status", "offline", retain=True).wait_for_publish(2)
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()
