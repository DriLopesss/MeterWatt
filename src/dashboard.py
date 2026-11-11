"""MeterWatt - backend v3.

Recebe as leituras que o ESP32 publica por MQTT, guarda cada uma num CSV e serve
o painel web e uma API HTTP.

Aceita dois tipos de medidor:
  * PZEM-004T v3 (firmware/mwatt_pzem): tensão, corrente, potência ativa (W),
    energia (Wh do contador do módulo), frequência e fator de potência.
  * ACS712 (firmware/mwatt): só corrente. A potência é aparente (VA) e o consumo
    é uma ESTIMATIVA. O painel mostra isso com o rótulo "estimado".

Configuração por variáveis de ambiente (veja o README):
  MQTT_HOST, MQTT_PORTA, MQTT_USUARIO, MQTT_SENHA, TARIFA_KWH,
  ARQUIVO_CSV, HTTP_HOST, HTTP_PORTA, CORS_ORIGENS, LIMITE_OFFLINE_S
"""
import csv
import json
import math
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from flask import Flask, abort, jsonify, request, send_from_directory

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORTA = int(os.getenv("MQTT_PORTA", "1883"))
MQTT_USUARIO = os.getenv("MQTT_USUARIO")
MQTT_SENHA = os.getenv("MQTT_SENHA")
TOPICO_TELEMETRIA = "meterwatt/+/telemetria"
TOPICO_STATUS = "meterwatt/+/status"

_tarifa = os.getenv("TARIFA_KWH")
TARIFA_KWH = float(_tarifa.replace(",", ".")) if _tarifa else None

ARQUIVO_CSV = os.getenv("ARQUIVO_CSV", "leituras.csv")
HTTP_HOST = os.getenv("HTTP_HOST", "127.0.0.1")
HTTP_PORTA = int(os.getenv("HTTP_PORTA", "5000"))
CORS_ORIGENS = [o.strip() for o in os.getenv("CORS_ORIGENS", "").split(",") if o.strip()]

# Sem leitura por mais tempo que isto, o medidor aparece como "sem sinal" no painel.
LIMITE_OFFLINE_S = float(os.getenv("LIMITE_OFFLINE_S", "20"))
# Buraco maior que isto entre duas leituras não entra na estimativa por integração
# (o aparelho ficou fora do ar e não sabemos o que aconteceu no meio).
INTERVALO_MAXIMO_S = 60
# Quantas leituras ficam na memória para o gráfico (1440 x 5 s = 2 horas).
TAMANHO_HISTORICO = 1440

ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TEXTO_SIMPLES = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

CAMPOS_CSV = ["recebido_em", "dispositivo", "sensor", "sensor_ok", "tensao_v", "corrente_a",
              "potencia_w", "va", "energia_wh", "frequencia_hz", "fp", "rssi", "uptime_s",
              "simulado"]

PASTA_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

trava = threading.Lock()
dispositivos = {}  # id -> estado atual


def agora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dia_local(instante):
    return datetime.fromtimestamp(instante).strftime("%Y-%m-%d")


def numero(dados, chave, minimo, maximo):
    """Devolve o campo como float se for um número dentro da faixa; senão, None."""
    valor = dados.get(chave)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    valor = float(valor)
    if math.isnan(valor) or math.isinf(valor) or not minimo <= valor <= maximo:
        return None
    return valor


def texto_simples(valor):
    """Aceita só letras, números, ponto, traço e sublinhado. O resto vira None."""
    return valor if isinstance(valor, str) and TEXTO_SIMPLES.match(valor) else None


def preparar_csv():
    """Se o CSV existente tem colunas de uma versão antiga, guarda-o com outro nome."""
    if not os.path.exists(ARQUIVO_CSV):
        return
    with open(ARQUIVO_CSV, newline="", encoding="utf-8") as f:
        cabecalho = next(csv.reader(f), None)
    if cabecalho != CAMPOS_CSV:
        raiz, ext = os.path.splitext(ARQUIVO_CSV)
        destino = f"{raiz}-antigo-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}"
        os.replace(ARQUIVO_CSV, destino)
        print(f"O CSV tinha colunas de outra versão e foi guardado como {destino}")


def gravar_csv(linha):
    novo = not os.path.exists(ARQUIVO_CSV)
    with open(ARQUIVO_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_CSV, extrasaction="ignore")
        if novo:
            w.writeheader()
        w.writerow(linha)


def estado_novo():
    return {
        "tipo": None, "sensor": None, "sensor_ok": True, "erro_sensor": None, "simulado": False,
        "tensao_v": None, "corrente_a": None, "potencia_w": None, "va": None,
        "frequencia_hz": None, "fp": None, "alarme": False,
        "contador_wh": None,      # contador de energia do próprio PZEM
        "wh_hoje": 0.0, "dia": None, "wh_sessao": 0.0,
        "energia_fonte": None,    # "medidor" (contador do PZEM) ou "estimada" (integração de VA)
        "potencia_anterior": None, "instante_medida": None,
        "rssi": None, "uptime_s": None, "falhas_leitura": None, "fw": None,
        "status_mqtt": None, "ultimo_instante": None, "atualizado_em": None,
        "historico": deque(maxlen=TAMANHO_HISTORICO),
    }


def _somar_energia(estado, wh, instante):
    hoje = dia_local(instante)
    if estado["dia"] != hoje:
        estado["dia"] = hoje
        estado["wh_hoje"] = 0.0
    estado["wh_hoje"] += wh
    estado["wh_sessao"] += wh


def processar_leitura(dispositivo, dados, instante=None):
    """Atualiza o estado do dispositivo com uma leitura. Devolve o resumo novo.

    Leituras com `sensor_ok: false` atualizam só a saúde do aparelho: as medidas
    antigas são apagadas, para o painel nunca mostrar número velho como se fosse atual.
    """
    instante = time.time() if instante is None else instante
    if not isinstance(dados, dict):
        raise ValueError("a leitura precisa ser um objeto JSON")

    with trava:
        e = dispositivos.setdefault(dispositivo, estado_novo())
        e["ultimo_instante"] = instante
        e["atualizado_em"] = agora_iso()
        e["status_mqtt"] = "online"   # se chegou leitura, o aparelho está vivo
        e["rssi"] = numero(dados, "rssi", -130, 0)
        e["uptime_s"] = numero(dados, "uptime_s", 0, 4e9)
        e["falhas_leitura"] = numero(dados, "falhas_leitura", 0, 4e9)
        e["simulado"] = dados.get("simulado") is True
        e["fw"] = texto_simples(dados.get("fw")) or e["fw"]
        e["sensor"] = texto_simples(dados.get("sensor")) or e["sensor"]

        if dados.get("sensor_ok") is False:
            e["sensor_ok"] = False
            e["erro_sensor"] = texto_simples(dados.get("erro_sensor")) or "desconhecido"
            for chave in ("tensao_v", "corrente_a", "potencia_w", "va", "frequencia_hz", "fp"):
                e[chave] = None
            e["potencia_anterior"] = None
            return _resumo(dispositivo, e, instante)

        tensao = numero(dados, "tensao_v", 0, 300)
        potencia = numero(dados, "potencia_w", 0, 30000)
        corrente = numero(dados, "corrente_a", 0, 120)
        if corrente is None:
            corrente = numero(dados, "irms_a", 0, 120)   # nome usado pelo firmware do ACS712

        if tensao is not None and potencia is not None and corrente is not None:
            # --- PZEM: potência ativa e contador de energia de verdade ---
            e["tipo"] = "pzem"
            e["tensao_v"], e["corrente_a"], e["potencia_w"] = tensao, corrente, potencia
            e["va"] = round(tensao * corrente, 1)
            e["frequencia_hz"] = numero(dados, "frequencia_hz", 40, 70)
            e["fp"] = numero(dados, "fp", 0, 1)
            e["alarme"] = dados.get("alarme") is True
            contador = numero(dados, "energia_wh", 0, 1e10)
            if contador is not None:
                e["energia_fonte"] = "medidor"
                anterior = e["contador_wh"]
                # Contador menor que o anterior = foi zerado no módulo: recomeça a contagem dali.
                if anterior is not None and contador >= anterior:
                    _somar_energia(e, contador - anterior, instante)
                e["contador_wh"] = contador
            valor_grafico = potencia
        elif corrente is not None:
            # --- Só corrente (ACS712): potência aparente e consumo estimado ---
            va = numero(dados, "va", 0, 30000)
            if va is None:
                raise ValueError("leitura sem 'va' nem medidas de tensão e potência")
            e["tipo"] = "corrente"
            e["corrente_a"], e["va"] = corrente, va
            e["tensao_v"] = e["potencia_w"] = e["frequencia_hz"] = e["fp"] = None
            e["energia_fonte"] = "estimada"
            ant, t_ant = e["potencia_anterior"], e["instante_medida"]
            if ant is not None and t_ant is not None and 0 < instante - t_ant <= INTERVALO_MAXIMO_S:
                # regra do trapézio entre a leitura anterior e a atual
                _somar_energia(e, (ant + va) / 2 * (instante - t_ant) / 3600, instante)
            e["potencia_anterior"] = va
            valor_grafico = va
        else:
            raise ValueError("leitura sem corrente válida")

        e["sensor_ok"], e["erro_sensor"] = True, None
        e["instante_medida"] = instante
        e["historico"].append((round(instante, 1), valor_grafico))
        return _resumo(dispositivo, e, instante)


def _arredonda(valor, casas):
    return None if valor is None else round(valor, casas)


def _resumo(dispositivo, e, agora=None):
    """Monta o JSON de um dispositivo. Chamar com a trava já adquirida."""
    agora = time.time() if agora is None else agora
    idade = None if e["ultimo_instante"] is None else max(0.0, agora - e["ultimo_instante"])
    online = idade is not None and idade <= LIMITE_OFFLINE_S and e["status_mqtt"] != "offline"
    wh_hoje = e["wh_hoje"] if e["dia"] == dia_local(agora) else 0.0
    kwh_hoje = wh_hoje / 1000
    kwh_sessao = e["wh_sessao"] / 1000
    custo_hoje = round(kwh_hoje * TARIFA_KWH, 2) if TARIFA_KWH is not None else None
    custo_sessao = round(kwh_sessao * TARIFA_KWH, 4) if TARIFA_KWH is not None else None
    potencia_para_custo = e["potencia_w"] if e["tipo"] == "pzem" else e["va"]
    custo_hora = None
    if TARIFA_KWH is not None and potencia_para_custo is not None and online and e["sensor_ok"]:
        custo_hora = round(potencia_para_custo / 1000 * TARIFA_KWH, 2)
    return {
        "dispositivo": dispositivo,
        "tipo": e["tipo"],
        "sensor": e["sensor"],
        "online": online,
        "segundos_desde_leitura": _arredonda(idade, 1),
        "sensor_ok": e["sensor_ok"],
        "erro_sensor": e["erro_sensor"],
        "simulado": e["simulado"],
        "tensao_v": e["tensao_v"],
        "corrente_a": e["corrente_a"],
        "potencia_w": e["potencia_w"],
        "potencia_aparente_va": e["va"],
        "frequencia_hz": e["frequencia_hz"],
        "fp": e["fp"],
        "alarme": e["alarme"],
        "energia_fonte": e["energia_fonte"],
        "energia_contador_kwh": _arredonda(None if e["contador_wh"] is None else e["contador_wh"] / 1000, 3),
        "kwh_hoje": round(kwh_hoje, 4),
        "kwh_sessao": round(kwh_sessao, 4),
        "custo_hoje": custo_hoje,
        "custo_por_hora": custo_hora,
        "tarifa_kwh": TARIFA_KWH,
        "rssi": e["rssi"],
        "uptime_s": e["uptime_s"],
        "falhas_leitura": e["falhas_leitura"],
        "fw": e["fw"],
        "atualizado_em": e["atualizado_em"],
        # Campos das versões anteriores, mantidos para quem já consome /get.
        # "watts" traz potência ativa no PZEM e VA (aparente) no ACS712.
        "irms_a": e["corrente_a"],
        "va": e["va"],
        "watts": potencia_para_custo,
        "kvah_acumulado": round(kwh_sessao, 6),
        "reais_estimado": custo_sessao,
        "reais_acumulativo": custo_sessao,
    }


def recuperar_consumo_de_hoje():
    """Ao iniciar, relê o CSV para o "consumo de hoje" não zerar a cada reinício do backend.

    Só vale para medidores com contador de energia (PZEM).
    """
    if not os.path.exists(ARQUIVO_CSV):
        return 0
    hoje = dia_local(time.time())
    linhas = 0
    with open(ARQUIVO_CSV, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            try:
                instante = datetime.fromisoformat(linha["recebido_em"]).timestamp()
                disp = linha["dispositivo"]
                contador = float(linha["energia_wh"])
            except (KeyError, ValueError, TypeError):
                continue
            if dia_local(instante) != hoje or not ID_VALIDO.match(disp):
                continue
            with trava:
                e = dispositivos.setdefault(disp, estado_novo())
                anterior = e["contador_wh"]
                if e["dia"] != hoje:
                    e["dia"], e["wh_hoje"] = hoje, 0.0
                if anterior is not None and contador >= anterior:
                    e["wh_hoje"] += contador - anterior
                e["contador_wh"] = contador
                e["energia_fonte"] = "medidor"
                e["tipo"] = "pzem"
            linhas += 1
    return linhas


# ---------------------------------------------------------------- MQTT

def ao_conectar(cliente, _userdata, _flags, codigo, _props=None):
    if codigo == 0:
        print(f"MQTT conectado em {MQTT_HOST}:{MQTT_PORTA}")
        cliente.subscribe([(TOPICO_TELEMETRIA, 0), (TOPICO_STATUS, 0)])
    else:
        print(f"MQTT: falha ao conectar (código {codigo})")


def ao_receber(_cliente, _userdata, msg):
    partes = msg.topic.split("/")
    if len(partes) != 3 or partes[0] != "meterwatt" or not ID_VALIDO.match(partes[1]):
        return
    dispositivo, tipo = partes[1], partes[2]
    try:
        texto = msg.payload.decode("utf-8")
    except UnicodeDecodeError:
        return

    if tipo == "status":
        if texto in ("online", "offline"):
            with trava:
                dispositivos.setdefault(dispositivo, estado_novo())["status_mqtt"] = texto
        return
    if tipo != "telemetria":
        return

    try:
        dados = json.loads(texto)
        r = processar_leitura(dispositivo, dados)
    except (json.JSONDecodeError, ValueError) as erro:
        print(f"Leitura recusada em {msg.topic}: {erro}")
        return
    gravar_csv({
        "recebido_em": agora_iso(), "dispositivo": dispositivo, "sensor": r["sensor"],
        "sensor_ok": r["sensor_ok"], "tensao_v": r["tensao_v"], "corrente_a": r["corrente_a"],
        "potencia_w": r["potencia_w"], "va": r["va"],
        "energia_wh": dados.get("energia_wh") if r["tipo"] == "pzem" and r["sensor_ok"] else None,
        "frequencia_hz": r["frequencia_hz"], "fp": r["fp"], "rssi": r["rssi"],
        "uptime_s": r["uptime_s"], "simulado": r["simulado"],
    })
    if not r["sensor_ok"]:
        print(f"{dispositivo}: sensor sem leitura ({r['erro_sensor']})")
    elif r["tipo"] == "pzem":
        print(f"{dispositivo}: {r['tensao_v']} V, {r['corrente_a']} A, {r['potencia_w']} W")
    else:
        print(f"{dispositivo}: {r['corrente_a']} A, {r['va']} VA (estimado)")


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


# ---------------------------------------------------------------- HTTP

app = Flask(__name__, static_folder=PASTA_WEB, static_url_path="/web")

if CORS_ORIGENS:
    from flask_cors import CORS
    CORS(app, origins=CORS_ORIGENS)


@app.after_request
def cabecalhos_de_seguranca(resposta):
    # O painel só carrega arquivos do próprio backend: nada de script ou estilo de fora.
    resposta.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.get("/")
def painel():
    return send_from_directory(PASTA_WEB, "index.html")


@app.get("/api/dispositivos")
def api_dispositivos():
    with trava:
        lista = [_resumo(d, e) for d, e in sorted(dispositivos.items())]
    return jsonify({"dispositivos": lista, "tarifa_kwh": TARIFA_KWH,
                    "limite_offline_s": LIMITE_OFFLINE_S, "servidor_em": agora_iso()})


@app.get("/api/historico/<dispositivo>")
def api_historico(dispositivo):
    if not ID_VALIDO.match(dispositivo):
        abort(404)
    try:
        minutos = min(max(float(request.args.get("minutos", "30")), 1), 120)
    except ValueError:
        minutos = 30
    limite = time.time() - minutos * 60
    with trava:
        e = dispositivos.get(dispositivo)
        if e is None:
            abort(404)
        pontos = [[t, v] for t, v in e["historico"] if t >= limite]
        unidade = "W" if e["tipo"] == "pzem" else "VA"
    return jsonify({"dispositivo": dispositivo, "unidade": unidade, "minutos": minutos,
                    "agora": round(time.time(), 1), "pontos": pontos})


@app.get("/get")
def leitura_atual():
    """Leitura do dispositivo atualizado mais recentemente (compatível com a v1 e a v2)."""
    with trava:
        com_leitura = {d: e for d, e in dispositivos.items() if e["ultimo_instante"] is not None}
        if not com_leitura:
            return jsonify({"erro": "Nenhuma leitura recebida ainda."}), 404
        disp, e = max(com_leitura.items(), key=lambda kv: kv[1]["ultimo_instante"])
        return jsonify(_resumo(disp, e))


@app.get("/dispositivos")
def todos():
    with trava:
        return jsonify([_resumo(d, e) for d, e in dispositivos.items()])


if __name__ == "__main__":
    if TARIFA_KWH is None:
        print("Aviso: TARIFA_KWH não definida. O custo em reais não será calculado.")
    preparar_csv()
    recuperadas = recuperar_consumo_de_hoje()
    if recuperadas:
        print(f"Consumo de hoje recuperado do CSV ({recuperadas} leituras).")
    iniciar_mqtt()
    print(f"Painel: http://{HTTP_HOST}:{HTTP_PORTA}/")
    app.run(host=HTTP_HOST, port=HTTP_PORTA, debug=False)
