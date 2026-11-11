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
    args = ap.parse_args()

    host = os.getenv("MQTT_HOST", "localhost")
    porta = int(os.getenv("MQTT_PORTA", "1883"))
    base = f"meterwatt/{args.id}"

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"simulador-{args.id}")
    if os.getenv("MQTT_USUARIO"):
        cliente.username_pw_set(os.getenv("MQTT_USUARIO"), os.getenv("MQTT_SENHA"))
    cliente.will_set(f"{base}/status", "offline", retain=True)
    cliente.connect(host, porta)
    cliente.loop_start()
    cliente.publish(f"{base}/status", "online", retain=True)

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
