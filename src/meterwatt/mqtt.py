"""Cliente MQTT do servidor: recebe telemetria, status e pedidos de pareamento,
e publica a confirmação de pareamento para o medidor."""
import logging
import ssl

import paho.mqtt.client as mqtt

log = logging.getLogger("meterwatt.mqtt")
TOPICOS = [("meterwatt/+/telemetria", 0), ("meterwatt/+/status", 0), ("meterwatt/+/pareamento", 1)]


def iniciar(plataforma):
    cfg = plataforma.cfg
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meterwatt-servidor")
    if cfg.mqtt_usuario:
        cliente.username_pw_set(cfg.mqtt_usuario, cfg.mqtt_senha)
    if cfg.mqtt_tls:
        cliente.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def ao_conectar(c, _u, _f, codigo, _p=None):
        if codigo == 0:
            log.info("MQTT conectado em %s:%s", cfg.mqtt_host, cfg.mqtt_porta)
            c.subscribe(TOPICOS)
        else:
            log.warning("MQTT recusou a conexão (código %s)", codigo)

    def ao_receber(_c, _u, msg):
        try:
            plataforma.medidores.processar_mqtt(msg.topic, msg.payload)
        except Exception:   # uma mensagem ruim nunca derruba o cliente
            log.exception("Falha ao processar mensagem em %s", msg.topic)

    cliente.on_connect = ao_conectar
    cliente.on_message = ao_receber
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)
    cliente.connect_async(cfg.mqtt_host, cfg.mqtt_porta)
    cliente.loop_start()
    plataforma.publicar = lambda topico, mensagem: cliente.publish(topico, mensagem, qos=1)
    return cliente
