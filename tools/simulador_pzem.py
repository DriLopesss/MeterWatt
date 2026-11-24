"""Simulador de um MeterWatt com PZEM-004T v3.

Publica por MQTT as mesmas mensagens que o firmware `firmware/mwatt_pzem` publica,
para testar o backend e o painel antes de o módulo chegar (ou sem ligar nada na rede).

Toda mensagem leva `"simulado": true`, e o painel mostra esse aviso. Os números são
inventados por este programa: NÃO são medições.

Uso (com o broker e o backend já rodando):
    python tools/simulador_pzem.py
    python tools/simulador_pzem.py --tensao 220 --acelerar 20 --falhas
    python tools/simulador_pzem.py --fases 2              # casa bifásica: um PZEM por fase (firmware 3.4)
    python tools/simulador_pzem.py --fases 2 --sem-fase 2 # a fase 2 sem energia
    python tools/simulador_pzem.py --queda 300            # 5 minutos sem conexão, depois manda o que guardou

Usa as mesmas variáveis do backend: MQTT_HOST, MQTT_PORTA, MQTT_USUARIO, MQTT_SENHA.
Com MQTT_TLS=1, conecta criptografado como o firmware 3.3; MQTT_CA aponta outra autoridade (só no ensaio local).
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

# nome, potência (W), fator de potência, minutos ligado, minutos desligado, chance de ligar por minuto, onde
# "onde" só conta com mais de uma fase: o número da fase (aparelho de 127 V) ou "12" (220 V entre as fases 1 e 2).
CARGAS = [
    ("standby e roteador", 45, 0.62, None, None, None, 1),     # sempre ligado
    ("geladeira", 140, 0.78, 12, 18, None, 2),                 # ciclo do compressor
    ("iluminação", 60, 0.95, 40, 25, None, 1),
    ("micro-ondas", 1300, 0.90, 2, None, 0.03, 2),
    ("chuveiro", 5400, 1.00, 7, None, 0.012, "12"),
]


class Casa:
    def __init__(self, tensao_nominal, semente=None, fases=1):
        self.rng = random.Random(semente)
        self.nominal = tensao_nominal
        self.fases = fases
        self.t = 0.0
        # Um contador por PZEM, já "rodado".
        self.energia_wh = [float(self.rng.randint(12_000, 90_000)) for _ in range(fases)]
        self.ate = {}   # carga eventual -> instante em que desliga

    def cargas_ligadas(self):
        ligadas = []
        minuto = self.t / 60
        for nome, p, fp, liga, desliga, chance, onde in CARGAS:
            if liga is None:
                ligadas.append((p, fp, onde))
            elif chance is None:
                if minuto % (liga + desliga) < liga:
                    ligadas.append((p, fp, onde))
            elif self.ate.get(nome, -1) > self.t:
                ligadas.append((p, fp, onde))
        return ligadas

    def passo(self, dt):
        """Avança `dt` segundos e devolve as medidas de cada fase como cada PZEM as devolveria."""
        self.t += dt
        for nome, _p, _fp, liga, _desl, chance, _onde in CARGAS:
            if chance and self.ate.get(nome, -1) <= self.t and self.rng.random() < chance * dt / 60:
                self.ate[nome] = self.t + liga * 60 * self.rng.uniform(0.6, 1.3)

        # Potência ativa e reativa que cada PZEM vê. Com uma fase, tudo cai nela.
        p_fase, q_fase = [0.0] * self.fases, [0.0] * self.fases
        for p, fp, onde in self.cargas_ligadas():
            p *= self.rng.uniform(0.985, 1.015)
            theta = math.acos(fp)
            if self.fases == 1:
                p_fase[0] += p
                q_fase[0] += p * math.tan(theta)
            elif onde == "12":
                # 220 V entre duas fases (127/220): cada PZEM vê a tensão da sua fase para o neutro e a mesma
                # corrente, defasada de 30° para um lado e para o outro. A soma das duas ativas é a do aparelho.
                corrente = p / (self.nominal * math.sqrt(3) * fp)
                for k, desvio in ((0, math.radians(30)), (1, -math.radians(30))):
                    p_fase[k] += self.nominal * corrente * math.cos(theta + desvio)
                    q_fase[k] += self.nominal * corrente * math.sin(theta + desvio)
            else:
                k = min(int(onde), self.fases) - 1
                p_fase[k] += p
                q_fase[k] += p * math.tan(theta)

        saida = []
        for k in range(self.fases):
            s = math.hypot(p_fase[k], q_fase[k])
            tensao = self.nominal * (1 + 0.012 * math.sin(self.t / 900 + k)) + self.rng.uniform(-0.4, 0.4)
            tensao -= 0.03 * s / tensao                    # a tensão cai um pouco com carga alta
            self.energia_wh[k] += p_fase[k] * dt / 3600
            saida.append({
                "tensao_v": round(tensao, 1),
                "corrente_a": round(s / tensao, 3),
                "potencia_w": round(p_fase[k], 1),
                "energia_wh": int(self.energia_wh[k]),     # o PZEM conta em Wh inteiros
                "frequencia_hz": round(60 + self.rng.uniform(-0.05, 0.05), 1),
                "fp": round(p_fase[k] / s, 2) if s else 0.0,
                "alarme": False,
            })
        return saida


def mensagem(medidas, erros):
    """O que o firmware 3.4 manda (fases.h): um módulo, igual às versões antigas; mais de um, o total e cada fase."""
    lidas = [m for m, e in zip(medidas, erros) if not e]
    if not lidas:
        msg = {"sensor_ok": False, "erro_sensor": erros[0]}
    elif len(medidas) == 1:
        msg = {"sensor_ok": True, **lidas[0]}
    else:
        aparente = sum(m["tensao_v"] * m["corrente_a"] for m in lidas)
        potencia = sum(m["potencia_w"] for m in lidas)
        msg = {"sensor_ok": True, "tensao_v": round(sum(m["tensao_v"] for m in lidas) / len(lidas), 1),
               "corrente_a": round(sum(m["corrente_a"] for m in lidas), 3), "potencia_w": round(potencia, 1),
               "energia_wh": sum(m["energia_wh"] for m in lidas), "frequencia_hz": lidas[0]["frequencia_hz"],
               "fp": round(min(1.0, potencia / aparente), 2) if aparente else 1.0, "alarme": False}
    if len(medidas) > 1:
        msg["fases"] = [{"fase": k + 1, "ok": True, **{c: m[c] for c in ("tensao_v", "corrente_a", "potencia_w", "energia_wh", "fp")}}
                        if not e else {"fase": k + 1, "ok": False, "erro": e} for k, (m, e) in enumerate(zip(medidas, erros))]
    return msg


class FilaOffline:
    """O que o firmware 3.5 faz sem conexão (fila_offline.h): um resumo por minuto, mandado depois."""
    POR_MENSAGEM = 8

    def __init__(self):
        self.minutos = []

    def guardar(self, uptime_s, medidas, erros):
        lidas = [(k, m) for k, (m, e) in enumerate(zip(medidas, erros)) if not e]
        if not lidas:
            return
        w = sum(m["potencia_w"] for _, m in lidas)
        chave = uptime_s // 60
        if not self.minutos or self.minutos[-1]["chave"] != chave:
            self.minutos.append({"chave": chave, "n": 0, "soma": 0.0, "min": w, "max": w, "e": [None] * len(medidas)})
        m = self.minutos[-1]
        m.update(u=uptime_s, n=m["n"] + 1, soma=m["soma"] + w, min=min(m["min"], w), max=max(m["max"], w))
        for k, medida in lidas:
            m["e"][k] = medida["energia_wh"]

    def mensagens(self, uptime_agora):
        while self.minutos:
            lote, self.minutos = self.minutos[:self.POR_MENSAGEM], self.minutos[self.POR_MENSAGEM:]
            yield {"tipo": "atrasadas", "uptime_agora": uptime_agora, "perdidos": 0, "minutos": [
                {"u": m["u"], "n": m["n"], "w": round(m["soma"] / m["n"], 1), "min": m["min"], "max": m["max"], "e": m["e"]}
                for m in lote]}


def main():
    ap = argparse.ArgumentParser(description="Simulador de MeterWatt com PZEM-004T v3 (dados inventados).")
    ap.add_argument("--id", default="mw-SIMULADO", help="identificador do medidor simulado")
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre mensagens (o firmware manda a cada 1 s)")
    ap.add_argument("--tensao", type=float, default=127.0, choices=[127.0, 220.0], help="tensão nominal da rede")
    ap.add_argument("--acelerar", type=float, default=1.0, help="quantas vezes o tempo da casa corre mais rápido")
    ap.add_argument("--falhas", action="store_true", help="de vez em quando simula o PZEM sem responder")
    ap.add_argument("--fases", type=int, default=1, choices=[1, 2, 3], help="quantas fases (um PZEM por fase, firmware 3.4)")
    ap.add_argument("--sem-fase", type=int, default=None, metavar="N", help="a fase N sem energia (o PZEM dela não responde)")
    ap.add_argument("--queda", type=int, default=0, metavar="SEG",
                    help="depois de 20 s, fica SEG segundos sem conexão guardando um resumo por minuto (firmware 3.5)")
    ap.add_argument("--semente", type=int, default=None, help="para repetir a mesma sequência")
    ap.add_argument("--parear", metavar="CODIGO", default=None,
                    help="código do aplicativo (ex.: K7QM-2XPA): pede o pareamento como o firmware faz")
    ap.add_argument("--senha-propria", action="store_true",
                    help="entra com a senha própria deste medidor simulado (depois de um pareamento que a cadastrou)")
    args = ap.parse_args()
    if args.sem_fase is not None and not 1 <= args.sem_fase <= args.fases:
        ap.error("--sem-fase precisa ser uma das fases simuladas (use junto com --fases)")

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
            c.tls_set(ca_certs=os.getenv("MQTT_CA") or None)   # MQTT_CA: autoridade própria (ensaio local)
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

    casa = Casa(args.tensao, args.semente, args.fases)
    inicio = time.time()
    falhas = 0
    fila = FilaOffline()
    queda = (inicio + 20, inicio + 20 + args.queda) if args.queda else None
    print(f"Simulando {args.id} em {host}:{porta} a cada {args.intervalo} s. Ctrl+C para parar.")
    try:
        while True:
            comum = {"fw": "simulador", "sensor": "pzem004t-v3", "simulado": True,
                     "rssi": random.randint(-68, -52), "uptime_s": int(time.time() - inicio)}
            medidas = casa.passo(args.intervalo * args.acelerar)
            erros = [None] * args.fases
            for k in range(args.fases):
                if (args.falhas and random.random() < 0.08) or args.sem_fase == k + 1:
                    erros[k] = "sem_resposta"
            if any(erros):
                falhas += 1
            msg = {**comum, **mensagem(medidas, erros), "falhas_leitura": falhas}
            agora = time.time()
            if queda and queda[0] <= agora < queda[1]:
                fila.guardar(comum["uptime_s"], medidas, erros)
                print(f"Sem conexão (simulada): {len(fila.minutos)} minuto(s) guardado(s).")
                time.sleep(args.intervalo)
                continue
            for atrasada in fila.mensagens(comum["uptime_s"]):
                cliente.publish(f"{base}/telemetria", json.dumps({**atrasada, "simulado": True}))
                print(f"Minutos guardados enviados: {len(atrasada['minutos'])}")
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
