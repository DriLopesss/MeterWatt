/*
 * MeterWatt - firmware v3 (NodeMCU-ESP32 DevKit V1 + PZEM-004T v3.0)
 *
 * O PZEM mede tensão, corrente, potência ativa, energia, frequência e fator de potência.
 * O ESP32 só conversa com ele pela serial (Modbus RTU, 9600 8N1) e publica por MQTT.
 * Com isso o MeterWatt passa a ter watts e kWh de verdade, e não só VA estimado.
 *
 * ATENÇÃO (segurança):
 *  - Os bornes de tensão do PZEM ficam ligados à rede (127/220 V). Essa ligação é do
 *    eletricista, com o disjuntor desligado e o módulo dentro de caixa fechada.
 *  - O sensor de corrente (a "pinça") abraça UM fio só (fase), sem cortar nada.
 *  - O ESP32 liga apenas no conector de 4 pinos (5V, RX, TX, GND), que é isolado da rede
 *    por optoacopladores dentro do módulo.
 *
 * ATENÇÃO (níveis de tensão da serial): o lado serial do PZEM foi feito para 5 V e o
 * ESP32 só aceita 3,3 V nos pinos. Veja no README as duas formas de ligar. Nunca ligue
 * o TX do PZEM alimentado em 5 V direto no RX do ESP32.
 *
 * Este firmware ainda NÃO foi testado com o módulo real. O protocolo foi testado no
 * computador (tests/firmware). O primeiro teste na bancada está descrito no README.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"
#include "pzem_modbus.h"

#define VERSAO_FW "3.0.0"

#ifndef MQTT_PORTA
#define MQTT_PORTA 1883
#endif
#ifndef PZEM_ENDERECO
#define PZEM_ENDERECO pzem::ENDERECO_GERAL
#endif
#ifndef PZEM_TIMEOUT_MS
#define PZEM_TIMEOUT_MS 300
#endif
#ifndef PERMITIR_ZERAR_ENERGIA
#define PERMITIR_ZERAR_ENERGIA 0
#endif
#ifndef INTERVALO_ENVIO_MS
#define INTERVALO_ENVIO_MS 5000
#endif
#if INTERVALO_ENVIO_MS < 1000
#error "INTERVALO_ENVIO_MS precisa ser de pelo menos 1000: o PZEM so atualiza as medidas a cada ~1 s."
#endif

HardwareSerial& serialPzem = Serial2;
WiFiClient rede;
PubSubClient mqtt(rede);

char idDispositivo[24];
char topicoTelemetria[64];
char topicoStatus[64];
char topicoComando[64];

unsigned long ultimoEnvio = 0;
unsigned long ultimaTentativaMqtt = 0;
unsigned long falhasLeitura = 0;   // desde que a placa ligou
volatile bool pedidoZerarEnergia = false;

// Envia um pedido e espera `esperados` bytes. Devolve quantos chegaram.
size_t trocarComPzem(const uint8_t* pedido, size_t tamPedido, uint8_t* resposta, size_t esperados) {
  while (serialPzem.available()) serialPzem.read();  // joga fora sobras de uma troca anterior
  serialPzem.write(pedido, tamPedido);
  serialPzem.flush();
  size_t n = 0;
  const unsigned long inicio = millis();
  while (n < esperados && millis() - inicio < PZEM_TIMEOUT_MS) {
    if (serialPzem.available()) {
      resposta[n++] = (uint8_t)serialPzem.read();
    } else {
      delay(1);
    }
  }
  return n;
}

pzem::Erro lerPzem(pzem::Medidas* m) {
  uint8_t pedido[pzem::TAM_PEDIDO_LEITURA];
  uint8_t resposta[pzem::TAM_RESPOSTA_LEITURA];
  pzem::montarPedidoLeitura(PZEM_ENDERECO, pedido);
  size_t n = trocarComPzem(pedido, sizeof(pedido), resposta, sizeof(resposta));
  return pzem::interpretar(resposta, n, PZEM_ENDERECO, m);
}

bool zerarEnergiaNoPzem() {
  uint8_t pedido[pzem::TAM_PEDIDO_ZERAR];
  uint8_t resposta[pzem::TAM_PEDIDO_ZERAR];
  pzem::montarPedidoZerar(PZEM_ENDERECO, pedido);
  size_t n = trocarComPzem(pedido, sizeof(pedido), resposta, sizeof(resposta));
  return n == sizeof(resposta) && resposta[1] == pzem::FN_ZERAR_ENERGIA && pzem::crcConfere(resposta, n);
}

void aoReceberMqtt(char* topico, byte* dados, unsigned int tamanho) {
  if (strcmp(topico, topicoComando) != 0) return;
  const char* comando = "zerar_energia";
  if (tamanho == strlen(comando) && memcmp(dados, comando, tamanho) == 0) {
    pedidoZerarEnergia = true;
  }
}

void conectarMqttSePreciso() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - ultimaTentativaMqtt < 5000) return;  // tenta a cada 5 s, sem travar
  ultimaTentativaMqtt = millis();
  Serial.printf("MQTT: conectando a %s:%d...\n", MQTT_HOST, MQTT_PORTA);
  if (mqtt.connect(idDispositivo, MQTT_USUARIO, MQTT_SENHA, topicoStatus, 0, true, "offline")) {
    mqtt.publish(topicoStatus, "online", true);
    mqtt.subscribe(topicoComando);
    Serial.println("MQTT: conectado");
  } else {
    Serial.printf("MQTT: falhou (estado %d)\n", mqtt.state());
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  uint64_t mac = ESP.getEfuseMac();
  snprintf(idDispositivo, sizeof(idDispositivo), "mw-%04X%08lX",
           (unsigned int)(uint16_t)(mac >> 32), (unsigned long)(uint32_t)mac);
  snprintf(topicoTelemetria, sizeof(topicoTelemetria), "meterwatt/%s/telemetria", idDispositivo);
  snprintf(topicoStatus, sizeof(topicoStatus), "meterwatt/%s/status", idDispositivo);
  snprintf(topicoComando, sizeof(topicoComando), "meterwatt/%s/comando", idDispositivo);
  Serial.printf("\nMeterWatt v%s (PZEM-004T v3) - id %s\n", VERSAO_FW, idDispositivo);

  serialPzem.begin(9600, SERIAL_8N1, PINO_RX_PZEM, PINO_TX_PZEM);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);

  mqtt.setServer(MQTT_HOST, MQTT_PORTA);
  mqtt.setBufferSize(512);
  mqtt.setCallback(aoReceberMqtt);
}

void loop() {
  conectarMqttSePreciso();
  mqtt.loop();

  if (pedidoZerarEnergia) {
    pedidoZerarEnergia = false;
    if (PERMITIR_ZERAR_ENERGIA) {
      bool ok = zerarEnergiaNoPzem();
      Serial.printf("Zerar energia: %s\n", ok ? "feito" : "o modulo nao confirmou");
    } else {
      Serial.println("Zerar energia: ignorado (PERMITIR_ZERAR_ENERGIA esta em 0 no config.h)");
    }
  }

  if (millis() - ultimoEnvio < INTERVALO_ENVIO_MS) return;
  ultimoEnvio = millis();

  pzem::Medidas m;
  pzem::Erro erro = lerPzem(&m);

  char json[384];
  if (erro == pzem::SEM_ERRO) {
    snprintf(json, sizeof(json),
             "{\"fw\":\"%s\",\"sensor\":\"pzem004t-v3\",\"sensor_ok\":true,"
             "\"tensao_v\":%.1f,\"corrente_a\":%.3f,\"potencia_w\":%.1f,\"energia_wh\":%lu,"
             "\"frequencia_hz\":%.1f,\"fp\":%.2f,\"alarme\":%s,"
             "\"falhas_leitura\":%lu,\"rssi\":%d,\"uptime_s\":%lu}",
             VERSAO_FW, m.tensao_v, m.corrente_a, m.potencia_w, (unsigned long)m.energia_wh,
             m.frequencia_hz, m.fp, m.alarme ? "true" : "false",
             falhasLeitura, WiFi.RSSI(), millis() / 1000);
  } else {
    // Sem leitura válida: avisa o backend, mas NUNCA repete números antigos como se fossem novos.
    falhasLeitura++;
    snprintf(json, sizeof(json),
             "{\"fw\":\"%s\",\"sensor\":\"pzem004t-v3\",\"sensor_ok\":false,\"erro_sensor\":\"%s\","
             "\"falhas_leitura\":%lu,\"rssi\":%d,\"uptime_s\":%lu}",
             VERSAO_FW, pzem::nomeDoErro(erro), falhasLeitura, WiFi.RSSI(), millis() / 1000);
  }

  Serial.println(json);
  if (mqtt.connected()) mqtt.publish(topicoTelemetria, json);
}
