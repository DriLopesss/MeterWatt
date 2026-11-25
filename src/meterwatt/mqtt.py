"""Cliente MQTT do servidor: recebe telemetria, status e pedidos de pareamento,
e publica a confirmação de pareamento para o medidor.

Com MW_MQTT_DYNSEC=1 (Mosquitto com o plugin dynamic-security), o servidor também
administra as contas do broker:
- cada medidor pareado ganha um usuário próprio (o id dele), preso ao id de cliente dele,
  com a senha que o próprio medidor sorteou e mandou no pareamento, e permissão só para
  os tópicos dele (meterwatt/<id>/...);
- medidor ainda não pareado usa uma conta de "provisão", que só consegue pedir pareamento;
- ao remover o medidor da conta, o usuário dele no broker é apagado.

Testado com o Mosquitto 2.0.18. Nessa versão o plugin não troca %c/%u pelo id do cliente
nos tópicos, por isso cada medidor tem um papel próprio com os tópicos escritos por extenso.
"""
import json
import logging
import ssl

import paho.mqtt.client as mqtt

log = logging.getLogger("meterwatt.mqtt")
TOPICOS = [("meterwatt/+/telemetria", 0), ("meterwatt/+/status", 0), ("meterwatt/+/pareamento", 1)]
CONTROLE = "$CONTROL/dynamic-security/v1"
RESPOSTA = CONTROLE + "/response"
PAPEL_SERVIDOR = "meterwatt-servidor"
PAPEL_PROVISAO = "meterwatt-provisao"
ERROS_ESPERADOS = ("already exists", "already in", "Internal error", "not found")   # repetir o preparo não é problema
# Onde o servidor responde a um pedido de pareamento: um tópico que só o medidor que pediu conhece (ele sorteia o
# fim e manda junto com o código). Antes ia para meterwatt/<id>/comando, que a conta de provisão podia assinar
# com curinga: quem tivesse a senha de provisão (todo comprador) via o id de todos os medidores (revisão de 25/11).
RESPOSTA_PAREAMENTO = "meterwatt-resposta/"
ACL_ANTIGA_PROVISAO = {"acltype": "subscribePattern", "topic": "meterwatt/+/comando"}


def _acl(tipo, topico):
    return {"acltype": tipo, "topic": topico, "allow": True}


def comandos_de_preparo(cfg):
    """Papéis do servidor e da conta de provisão. Pode ser enviado quantas vezes for."""
    acls_servidor = [_acl("subscribePattern", t) for t, _ in TOPICOS] + [_acl("publishClientSend", "meterwatt/+/comando"),
                                                                         _acl("publishClientSend", RESPOSTA_PAREAMENTO + "+")]
    acls_provisao = [_acl("publishClientSend", "meterwatt/+/pareamento"), _acl("subscribePattern", RESPOSTA_PAREAMENTO + "+")]
    comandos = [{"command": "createRole", "rolename": PAPEL_SERVIDOR}]
    comandos += [{"command": "addRoleACL", "rolename": PAPEL_SERVIDOR, **a} for a in acls_servidor]
    comandos += [{"command": "createRole", "rolename": PAPEL_PROVISAO}]
    comandos += [{"command": "addRoleACL", "rolename": PAPEL_PROVISAO, **a} for a in acls_provisao]
    # Broker que já rodou uma versão anterior: a provisão perde a permissão de ler os comandos de todos.
    comandos.append({"command": "removeRoleACL", "rolename": PAPEL_PROVISAO, **ACL_ANTIGA_PROVISAO})
    if cfg.mqtt_provisao_usuario:
        comandos.append({"command": "createClient", "username": cfg.mqtt_provisao_usuario, "password": cfg.mqtt_provisao_senha,
                         "textname": "MeterWatt: medidores ainda não pareados", "roles": [{"rolename": PAPEL_PROVISAO}]})
    # Por último: dar o papel ao próprio servidor derruba a conexão uma vez (o broker faz isso
    # para as permissões novas valerem). Na reconexão, o comando repetido só devolve erro.
    comandos.append({"command": "addClientRole", "username": cfg.mqtt_usuario, "rolename": PAPEL_SERVIDOR})
    return comandos


def comandos_do_medidor(mid, segredo, cliente=None):
    """`cliente`: o id de cliente MQTT do medidor. No firmware 3.6, o id mais um sufixo sorteado que só ele conhece:
    quem tem a senha de provisão não consegue entrar com o id dele e derrubar a conexão (revisão de 25/11)."""
    papel = f"medidor-{mid}"
    cliente = cliente or mid
    acls = [_acl("publishClientSend", f"meterwatt/{mid}/{t}") for t in ("telemetria", "status", "pareamento")]
    acls.append(_acl("subscribeLiteral", f"meterwatt/{mid}/comando"))
    return [
        {"command": "createRole", "rolename": papel, "acls": acls},
        {"command": "createClient", "username": mid, "password": segredo, "clientid": cliente, "roles": [{"rolename": papel}]},
        {"command": "setClientPassword", "username": mid, "password": segredo},   # se já existia: vale o segredo novo
        {"command": "setClientId", "username": mid, "clientid": cliente},
    ]


class Credenciais:
    """Contas dos medidores no broker (plugin dynamic-security)."""

    def __init__(self, cliente):
        self.cliente = cliente

    def _enviar(self, comandos, marca):
        if not self.cliente.is_connected():
            return False
        info = self.cliente.publish(CONTROLE, json.dumps({"commands": [{**c, "correlationData": marca} for c in comandos]}), qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def registrar(self, mid, segredo, cliente=None):
        return self._enviar(comandos_do_medidor(mid, segredo, cliente), f"medidor:{mid}")

    def remover(self, mid):
        return self._enviar([{"command": "deleteClient", "username": mid}, {"command": "deleteRole", "rolename": f"medidor-{mid}"}],
                            f"remover:{mid}")


def iniciar(plataforma):
    cfg = plataforma.cfg
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meterwatt-servidor")
    if cfg.mqtt_usuario:
        cliente.username_pw_set(cfg.mqtt_usuario, cfg.mqtt_senha)
    if cfg.mqtt_tls:
        cliente.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    credenciais = Credenciais(cliente) if cfg.mqtt_dynsec else None

    def ao_conectar(c, _u, _f, codigo, _p=None):
        if codigo != 0:
            log.warning("MQTT recusou a conexão (código %s)", codigo)
            return
        log.info("MQTT conectado em %s:%s", cfg.mqtt_host, cfg.mqtt_porta)
        if credenciais:
            c.subscribe(RESPOSTA, 1)
            credenciais._enviar(comandos_de_preparo(cfg), "preparo")   # os tópicos são assinados quando o broker responder
        else:
            c.subscribe(TOPICOS)

    def resposta_do_broker(c, carga):
        try:
            respostas = json.loads(carga).get("responses", [])
        except ValueError:
            return
        for r in respostas:
            erro = r.get("error")
            if erro and not any(e in erro for e in ERROS_ESPERADOS) and not str(r.get("correlationData", "")).startswith("remover:"):
                log.error("Broker recusou %s (%s): %s", r.get("command"), r.get("correlationData"), erro)
        if any(r.get("correlationData") == "preparo" for r in respostas):
            c.subscribe(TOPICOS)

    def ao_receber(c, _u, msg):
        try:
            if msg.topic == RESPOSTA:
                resposta_do_broker(c, msg.payload)
            else:
                plataforma.medidores.processar_mqtt(msg.topic, msg.payload)
        except Exception:   # uma mensagem ruim nunca derruba o cliente
            log.exception("Falha ao processar mensagem em %s", msg.topic)

    cliente.on_connect = ao_conectar
    cliente.on_message = ao_receber
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)
    cliente.connect_async(cfg.mqtt_host, cfg.mqtt_porta)
    cliente.loop_start()
    plataforma.publicar = lambda topico, mensagem: cliente.publish(topico, mensagem, qos=1)
    plataforma.credenciais = credenciais
    return cliente
