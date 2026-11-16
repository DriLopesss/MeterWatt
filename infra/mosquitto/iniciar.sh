#!/bin/sh
# Prepara e inicia o Mosquitto dentro do contêiner:
#  1. cria o arquivo de contas (plugin dynamic-security) com o administrador, na primeira vez;
#  2. espera o Caddy emitir o certificado do domínio e copia para onde o Mosquitto consegue ler;
#  3. escreve a configuração e inicia.
# O certificado é renovado pelo Caddy a cada ~60 dias. Para o Mosquitto pegar o novo, reinicie
# este contêiner uma vez por mês (o guia de implantação mostra a linha do cron).
set -eu

DADOS=/mosquitto/data
CONTAS="$DADOS/dynamic-security.json"
CERTS=/mosquitto/certs

if [ ! -f "$CONTAS" ]; then
  echo "Criando o arquivo de contas com o administrador $MQTT_USUARIO"
  mosquitto_ctrl dynsec init "$CONTAS" "$MQTT_USUARIO" "$MQTT_SENHA"
fi

echo "Esperando o certificado de $DOMINIO (o Caddy emite quando o domínio já aponta para este servidor)..."
while true; do
  CRT=$(find /caddy -name "$DOMINIO.crt" 2>/dev/null | head -n 1)
  [ -n "$CRT" ] && [ -f "${CRT%.crt}.key" ] && break
  sleep 5
done
mkdir -p "$CERTS"
cp "$CRT" "$CERTS/servidor.crt"
cp "${CRT%.crt}.key" "$CERTS/servidor.key"
chmod 600 "$CERTS/servidor.key"
chown -R mosquitto:mosquitto "$CERTS" "$DADOS"

cat > /mosquitto/config/mosquitto.conf <<CONF
persistence true
persistence_location $DADOS/
log_dest stdout
log_type error
log_type warning
log_type notice

allow_anonymous false
plugin /usr/lib/mosquitto_dynamic_security.so
plugin_opt_config_file $CONTAS

# rede interna do Docker (o aplicativo)
listener 1883

# medidores, pela internet, com criptografia
listener 8883
certfile $CERTS/servidor.crt
keyfile $CERTS/servidor.key
tls_version tlsv1.2
CONF

exec mosquitto -c /mosquitto/config/mosquitto.conf
