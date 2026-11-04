"""MeterWatt - backend v2.

Recebe as leituras que o ESP32 publica por MQTT, guarda cada leitura num CSV
(para calibração e testes) e expõe os valores atuais numa API HTTP para o painel.

Configuração por variáveis de ambiente (veja o README):
  MQTT_HOST, MQTT_PORTA, MQTT_USUARIO, MQTT_SENHA, TARIFA_KWH,
  ARQUIVO_CSV, HTTP_HOST, HTTP_PORTA, CORS_ORIGENS

Limitação: o firmware mede corrente e usa tensão fixa, então a potência é
aparente (VA). O custo em reais é uma ESTIMATIVA que trata VAh como se fosse Wh.
"""
import csv
import json
import os
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from flask import Flask, jsonify
from flask_cors import CORS

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORTA = int(os.getenv("MQTT_PORTA", "1883"))
MQTT_USUARIO = os.getenv("MQTT_USUARIO")
MQTT_SENHA = os.getenv("MQTT_SENHA")
TOPICO = "meterwatt/+/telemetria"

_tarifa = os.getenv("TARIFA_KWH")
TARIFA_KWH = float(_tarifa.replace(",", ".")) if _tarifa else None

ARQUIVO_CSV = os.getenv("ARQUIVO_CSV", "leituras.csv")
HTTP_HOST = os.getenv("HTTP_HOST", "127.0.0.1")
HTTP_PORTA = int(os.getenv("HTTP_PORTA", "5000"))
CORS_ORIGENS = os.getenv("CORS_ORIGENS", "http://localhost:5173").split(",")

# Se duas leituras chegam com intervalo maior que isto, o trecho não entra no
# acumulado (o aparelho estava desconectado e não sabemos o que aconteceu).
INTERVALO_MAXIMO_S = 60

CAMPOS_CSV = ["recebido_em", "dispositivo", "irms_a", "va", "offset_mv",
              "amostras", "rssi", "uptime_s"]

trava = threading.Lock()
dispositivos = {}  # id -> estado atual


def agora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gravar_csv(linha):
    novo = not os.path.exists(ARQUIVO_CSV)
    with open(ARQUIVO_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_CSV)
        if novo:
            w.writeheader()
        w.writerow(linha)


def processar_leitura(dispositivo, dados, instante=None):
    """Atualiza o estado do dispositivo com uma leitura. Retorna o estado novo."""
    instante = time.time() if instante is None else instante
    va = float(dados.get("va", 0.0))
    with trava:
        estado = dispositivos.setdefault(dispositivo, {
            "vah_acumulado": 0.0, "ultimo_instante": None,
        })
        anterior = estado["ultimo_instante"]
        if anterior is not None:
            dt = instante - anterior
            if 0 < dt <= INTERVALO_MAXIMO_S:
                # regra do trapézio entre a leitura anterior e a atual
                estado["vah_acumulado"] += (estado["va"] + va) / 2 * dt / 3600
        estado.update({
            "irms_a": float(dados.get("irms_a", 0.0)),
            "va": va,
            "offset_mv": dados.get("offset_mv"),
            "rssi": dados.get("rssi"),
            "uptime_s": dados.get("uptime_s"),
            "ultimo_instante": instante,
            "atualizado_em": agora_iso(),
        })
        return dict(estado)


def ao_conectar(cliente, _userdata, _flags, codigo, _props=None):
    if codigo == 0:
        print(f"MQTT conectado em {MQTT_HOST}:{MQTT_PORTA}, ouvindo {TOPICO}")
        cliente.subscribe(TOPICO)
    else:
        print(f"MQTT: falha ao conectar (código {codigo})")


def ao_receber(_cliente, _userdata, msg):
    partes = msg.topic.split("/")
    if len(partes) != 3:
        return
    dispositivo = partes[1]
    try:
        dados = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"Mensagem inválida em {msg.topic}: {msg.payload[:80]!r}")
        return
    processar_leitura(dispositivo, dados)
    linha = {"recebido_em": agora_iso(), "dispositivo": dispositivo}
    linha.update({k: dados.get(k) for k in CAMPOS_CSV[2:]})
    gravar_csv(linha)
    print(f"{dispositivo}: {dados.get('irms_a')} A, {dados.get('va')} VA")


def resumo(dispositivo, estado):
    kvah = estado["vah_acumulado"] / 1000
    reais = round(kvah * TARIFA_KWH, 4) if TARIFA_KWH is not None else None
    return {
        "dispositivo": dispositivo,
        "irms_a": estado["irms_a"],
        "va": estado["va"],
        "kvah_acumulado": round(kvah, 6),
        "reais_estimado": reais,
        "tarifa_kwh": TARIFA_KWH,
        "offset_mv": estado["offset_mv"],
        "rssi": estado["rssi"],
        "atualizado_em": estado["atualizado_em"],
        # Campos antigos, mantidos para o front-end da v1 continuar funcionando.
        # Atenção: "watts" contém VA (potência aparente), não potência ativa.
        "watts": estado["va"],
        "reais_acumulativo": reais,
    }


app = Flask(__name__)
CORS(app, origins=CORS_ORIGENS)


@app.get("/get")
def leitura_atual():
    """Leitura do dispositivo atualizado mais recentemente (compatível com a v1)."""
    with trava:
        if not dispositivos:
            return jsonify({"erro": "Nenhuma leitura recebida ainda."}), 404
        disp, estado = max(dispositivos.items(), key=lambda kv: kv[1]["ultimo_instante"])
        return jsonify(resumo(disp, estado))


@app.get("/dispositivos")
def todos():
    with trava:
        return jsonify([resumo(d, e) for d, e in dispositivos.items()])


def iniciar_mqtt():
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meterwatt-backend")
    if MQTT_USUARIO:
        cliente.username_pw_set(MQTT_USUARIO, MQTT_SENHA)
    cliente.on_connect = ao_conectar
    cliente.on_message = ao_receber
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)
    cliente.connect_async(MQTT_HOST, MQTT_PORTA)
    cliente.loop_start()
    return cliente


if __name__ == "__main__":
    if TARIFA_KWH is None:
        print("Aviso: TARIFA_KWH não definida. O custo em reais não será calculado.")
    iniciar_mqtt()
    app.run(host=HTTP_HOST, port=HTTP_PORTA, debug=False)
