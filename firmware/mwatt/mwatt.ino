/*
 * MeterWatt - firmware v2 (NodeMCU-ESP32 DevKit V1 + ACS712 30A)
 *
 * O que mudou em relação à v1:
 *  - Senhas e endereços saem do código e vão para config.h (fora do Git).
 *  - Medição própria de corrente RMS com analogReadMilliVolts (usa a calibração
 *    de fábrica do ADC do ESP32), sem depender do EmonLib.
 *  - Reconexão de Wi-Fi e MQTT que não trava a medição.
 *  - Um único caminho de dados: MQTT com usuário e senha, em JSON.
 *  - Sem buffer pequeno: o JSON é montado com snprintf num buffer de 256 bytes.
 *  - Potência rotulada como VA (aparente), porque a tensão é um valor fixo.
 *
 * Limitações:
 *  - Sem medir a tensão da rede, não há potência ativa (W) nem kWh reais: o valor é VA.
 *  - O ACS712 30A tem 66 mV/A. Com o ruído do ADC do ESP32, correntes pequenas
 *    (abaixo de algumas centenas de mA) ficam perto do ruído e precisam ser
 *    avaliadas na calibração.
 *
 * ATENÇÃO (ligação elétrica): alimentado em 5 V, o ACS712 tem saída em torno de 2,5 V
 * sem corrente e pode passar de 3,3 V com corrente alta. O ESP32 não aceita mais que
 * 3,3 V no pino. Use um divisor de tensão entre a saída do ACS712 e o GPIO34 e
 * informe a relação em FATOR_DIVISOR no config.h.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"

#ifndef SENSIBILIDADE_MV_POR_A
#error "Defina SENSIBILIDADE_MV_POR_A no config.h conforme o modelo do seu sensor."
#endif
#ifndef MQTT_PORTA
#define MQTT_PORTA 1883
#endif

WiFiClient rede;
PubSubClient mqtt(rede);

char idDispositivo[24];
char topicoTelemetria[64];
char topicoStatus[64];

unsigned long ultimoEnvio = 0;
unsigned long ultimaTentativaMqtt = 0;
unsigned long ultimaMedicao = 0;
double energiaVAh = 0;  // acumulado desde que a placa ligou (perde ao reiniciar)

struct Leitura {
  double offset_mV;  // nível médio da saída do sensor (deve ficar perto do meio da faixa)
  double irms_A;
  long amostras;
};

// Mede durante JANELA_MS (vários ciclos de 60 Hz) e calcula o RMS em torno da média.
Leitura medirCorrente() {
  Leitura l = {0, 0, 0};
  double soma = 0, somaQ = 0;
  long n = 0;
  const unsigned long inicio = millis();
  while (millis() - inicio < JANELA_MS) {
    double v = analogReadMilliVolts(PINO_SENSOR);
    soma += v;
    somaQ += v * v;
    n++;
  }
  l.amostras = n;
  if (n < 2) return l;
  double media = soma / n;
  double variancia = somaQ / n - media * media;
  if (variancia < 0) variancia = 0;
  double vrms_mV = sqrt(variancia) * FATOR_DIVISOR;  // desfaz o divisor resistivo, se houver
  double irms = vrms_mV / SENSIBILIDADE_MV_POR_A * AJUSTE_CALIBRACAO;
  if (irms < LIMIAR_RUIDO_A) irms = 0;  // descarta o ruído do ADC
  l.offset_mV = media;
  l.irms_A = irms;
  if (media < 300 || media > 2800) {
    Serial.printf("AVISO: nivel medio de %.0f mV no pino. Confira o divisor e a alimentacao do ACS712.\n", media);
  }
  return l;
}

void conectarMqttSePreciso() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - ultimaTentativaMqtt < 5000) return;  // tenta a cada 5 s, sem travar
  ultimaTentativaMqtt = millis();
  Serial.printf("MQTT: conectando a %s:%d...\n", MQTT_HOST, MQTT_PORTA);
  if (mqtt.connect(idDispositivo, MQTT_USUARIO, MQTT_SENHA, topicoStatus, 0, true, "offline")) {
    mqtt.publish(topicoStatus, "online", true);
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
  Serial.printf("\nMeterWatt v2 - id %s\n", idDispositivo);

  analogReadResolution(12);
  analogSetPinAttenuation(PINO_SENSOR, ADC_11db);  // faixa de leitura até cerca de 3,1 V

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);

  mqtt.setServer(MQTT_HOST, MQTT_PORTA);
  mqtt.setBufferSize(512);
  ultimaMedicao = millis();
}

void loop() {
  conectarMqttSePreciso();
  mqtt.loop();

  if (millis() - ultimoEnvio < INTERVALO_ENVIO_MS) return;
  ultimoEnvio = millis();

  Leitura l = medirCorrente();
  double va = l.irms_A * TENSAO_NOMINAL_V;

  unsigned long agora = millis();
  energiaVAh += va * (double)(agora - ultimaMedicao) / 3600000.0;
  ultimaMedicao = agora;

  char json[256];
  snprintf(json, sizeof(json),
           "{\"irms_a\":%.3f,\"va\":%.1f,\"vah_desde_boot\":%.3f,\"tensao_nominal_v\":%.0f,"
           "\"offset_mv\":%.0f,\"amostras\":%ld,\"rssi\":%d,\"uptime_s\":%lu}",
           l.irms_A, va, energiaVAh, (double)TENSAO_NOMINAL_V,
           l.offset_mV, l.amostras, WiFi.RSSI(), agora / 1000);

  Serial.println(json);
  if (mqtt.connected()) mqtt.publish(topicoTelemetria, json);
}
