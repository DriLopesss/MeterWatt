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
 * CONFIGURAÇÃO SEM MEXER NO CÓDIGO (v3.1):
 *  - Na primeira vez, ou sem Wi-Fi salvo, o ESP32 cria a rede "MeterWatt-XXXX". Conecte o
 *    celular nela (senha SENHA_PORTAL do config.h) e uma página pede a rede de casa, o
 *    servidor MQTT e as senhas. Tudo fica guardado na memória do ESP32.
 *  - Segurar o botão BOOT por 5 segundos apaga os ajustes e reabre a página.
 *  - Apertar o BOOT rapidinho (menos de 3 s) reabre a página por 10 minutos, sem apagar
 *    nada. É por ela que se digita o código de pareamento do aplicativo (v3.2).
 *
 * PAREAMENTO COM O APLICATIVO (v3.2): o aplicativo mostra um código como "K7QM-2XPA".
 * Digitado na página, o medidor o envia ao servidor, que liga este medidor à conta.
 * O código fica só na RAM e vale 15 minutos.
 *  - Com uma senha de atualização definida, o firmware pode ser atualizado pelo Wi-Fi
 *    (Arduino IDE > Ferramentas > Porta > porta de rede "mw-...").
 *
 * Este firmware ainda NÃO foi testado com o módulo real. O protocolo foi testado no
 * computador (tests/firmware). O primeiro teste na bancada está descrito no README.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <ArduinoOTA.h>
#include "config.h"
#include "pzem_modbus.h"
#include "ajustes.h"
#include "pareamento.h"

#define VERSAO_FW "3.2.0"

#ifndef USAR_PORTAL_WIFI
#define USAR_PORTAL_WIFI 1
#endif
#if USAR_PORTAL_WIFI
#include <WiFiManager.h>
#ifndef SENHA_PORTAL
#error "Defina SENHA_PORTAL no config.h (de 8 a 63 caracteres): e a senha da rede MeterWatt-XXXX."
#endif
static_assert(sizeof(SENHA_PORTAL) >= 9 && sizeof(SENHA_PORTAL) <= 64,
              "SENHA_PORTAL precisa ter de 8 a 63 caracteres (exigencia do Wi-Fi WPA2).");
#endif
#ifndef PINO_BOTAO
#define PINO_BOTAO 0           // botão BOOT da placa
#endif
#ifndef OTA_SENHA
#define OTA_SENHA ""           // vazio = atualização pelo Wi-Fi desligada até alguém definir na página
#endif
#ifndef MQTT_HOST
#define MQTT_HOST ""
#endif
#ifndef MQTT_USUARIO
#define MQTT_USUARIO ""
#endif
#ifndef MQTT_SENHA
#define MQTT_SENHA ""
#endif

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
Preferences memoria;
ajustes::Ajustes cfg;              // o que está valendo agora (memória do ESP32 ou config.h)
bool otaIniciada = false;
unsigned long botaoDesde = 0;

#if USAR_PORTAL_WIFI
WiFiManager portal;
char portaTexto[6];
// Os campos extras da página. Senhas começam vazias: a página nunca mostra senhas salvas.
WiFiManagerParameter campoHost("mqtt_host", "Servidor MQTT (IP ou nome)", "", ajustes::TAM_HOST - 1);
WiFiManagerParameter campoPorta("mqtt_porta", "Porta MQTT", "1883", 5, "type='number' min='1' max='65535'");
WiFiManagerParameter campoUsuario("mqtt_usuario", "Usuário MQTT", "", ajustes::TAM_USUARIO - 1);
WiFiManagerParameter campoSenha("mqtt_senha", "Senha MQTT (em branco = manter a atual)", "", ajustes::TAM_SENHA - 1, "type='password'");
WiFiManagerParameter campoCodigo("codigo", "Código de pareamento do aplicativo (ex.: K7QM-2XPA; opcional)", "", 12, "autocapitalize='characters' autocomplete='off'");
WiFiManagerParameter campoOta("ota_senha", "Senha para atualizar pelo Wi-Fi (8 a 32 caracteres; em branco = manter)", "", ajustes::TAM_SENHA_OTA - 1, "type='password'");
#endif

char idDispositivo[24];
char topicoTelemetria[64];
char topicoStatus[64];
char topicoComando[64];
char topicoPareamento[64];
pareamento::Estado pedidoPareamento;

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
  if (pareamento::aoComando(&pedidoPareamento, dados, tamanho)) {
    Serial.println("Pareamento: confirmado pelo servidor. O medidor ja aparece no aplicativo.");
    return;
  }
  const char* comando = "zerar_energia";
  if (tamanho == strlen(comando) && memcmp(dados, comando, tamanho) == 0) {
    pedidoZerarEnergia = true;
  }
}

// ------------------------------------------------------------- ajustes

void carregarAjustes() {
  // Valores do config.h servem de ponto de partida; o que foi salvo pela página tem prioridade.
  ajustes::copiar(cfg.host, MQTT_HOST, ajustes::TAM_HOST);
  cfg.porta = MQTT_PORTA;
  ajustes::copiar(cfg.usuario, MQTT_USUARIO, ajustes::TAM_USUARIO);
  ajustes::copiar(cfg.senha, MQTT_SENHA, ajustes::TAM_SENHA);
  ajustes::copiar(cfg.senhaOta, OTA_SENHA, ajustes::TAM_SENHA_OTA);
  memoria.begin("meterwatt", true);
  if (memoria.isKey("host")) {
    memoria.getString("host", cfg.host, ajustes::TAM_HOST);
    cfg.porta = memoria.getUShort("porta", cfg.porta);
    memoria.getString("usuario", cfg.usuario, ajustes::TAM_USUARIO);
    memoria.getString("senha", cfg.senha, ajustes::TAM_SENHA);
    memoria.getString("ota", cfg.senhaOta, ajustes::TAM_SENHA_OTA);
  }
  memoria.end();
}

void salvarAjustes() {
  memoria.begin("meterwatt", false);
  memoria.putString("host", cfg.host);
  memoria.putUShort("porta", cfg.porta);
  memoria.putString("usuario", cfg.usuario);
  memoria.putString("senha", cfg.senha);
  memoria.putString("ota", cfg.senhaOta);
  memoria.end();
}

void apagarTudoEReiniciar() {
  Serial.println("Botao BOOT segurado: apagando Wi-Fi e ajustes e reiniciando.");
  memoria.begin("meterwatt", false);
  memoria.clear();
  memoria.end();
#if USAR_PORTAL_WIFI
  portal.resetSettings();
#endif
  delay(300);
  ESP.restart();
}

#if USAR_PORTAL_WIFI
void aoSalvarPagina() {
  char codigo[ajustes::TAM_CODIGO];
  ajustes::Resultado r = ajustes::aplicarFormulario(&cfg, campoHost.getValue(), campoPorta.getValue(),
                                                    campoUsuario.getValue(), campoSenha.getValue(),
                                                    campoOta.getValue(), false, campoCodigo.getValue(), codigo);
  Serial.printf("Pagina de configuracao: %s\n", ajustes::mensagem(r));
  if (r != ajustes::AJUSTES_OK) return;
  campoCodigo.setValue("", 12);    // o código vale uma vez e nunca é gravado
  if (codigo[0]) {
    pareamento::iniciar(&pedidoPareamento, codigo, millis());
    Serial.println("Pareamento: codigo recebido, enviando ao servidor (vale 15 minutos).");
  }
  salvarAjustes();
  mqtt.disconnect();               // reconecta já com os ajustes novos
  mqtt.setServer(cfg.host, cfg.porta);
  ultimaTentativaMqtt = 0;
}
#endif

void iniciarOtaSePossivel() {
  if (otaIniciada || WiFi.status() != WL_CONNECTED || !ajustes::otaLigada(cfg)) return;
  ArduinoOTA.setHostname(idDispositivo);
  ArduinoOTA.setPassword(cfg.senhaOta);
  ArduinoOTA.onStart([]() {
    Serial.println("Atualizacao pelo Wi-Fi: comecando. Nao desligue a placa.");
    if (mqtt.connected()) mqtt.publish(topicoStatus, "atualizando", true);
  });
  ArduinoOTA.onEnd([]() { Serial.println("Atualizacao pelo Wi-Fi: concluida, reiniciando."); });
  ArduinoOTA.onError([](ota_error_t erro) { Serial.printf("Atualizacao pelo Wi-Fi: falhou (erro %u)\n", (unsigned)erro); });
  ArduinoOTA.begin();
  otaIniciada = true;
  Serial.printf("Atualizacao pelo Wi-Fi ligada: procure a porta de rede %s na Arduino IDE.\n", idDispositivo);
}

void abrirPaginaPorUmTempo() {
#if USAR_PORTAL_WIFI
  if (portal.getConfigPortalActive()) return;
  char nomeRede[24];
  snprintf(nomeRede, sizeof(nomeRede), "MeterWatt-%s", idDispositivo + strlen(idDispositivo) - 4);
  portal.setConfigPortalTimeout(600);   // fecha sozinha em 10 minutos
  portal.startConfigPortal(nomeRede, SENHA_PORTAL);
  Serial.printf("Pagina de configuracao aberta por 10 min: conecte o celular na rede %s.\n", nomeRede);
#endif
}

void vigiarBotao() {
  if (digitalRead(PINO_BOTAO) == LOW) {
    if (botaoDesde == 0) botaoDesde = millis() | 1;
    else if (millis() - botaoDesde > 5000) apagarTudoEReiniciar();
  } else {
    // Soltou: um toque curto (entre 50 ms e 3 s) reabre a página sem apagar nada.
    if (botaoDesde != 0 && millis() - botaoDesde >= 50 && millis() - botaoDesde < 3000) abrirPaginaPorUmTempo();
    botaoDesde = 0;
  }
}

void enviarPareamentoSePreciso() {
  if (!pareamento::deveEnviar(&pedidoPareamento, millis(), mqtt.connected())) return;
  char json[48];
  pareamento::montarMensagem(pedidoPareamento, json, sizeof(json));
  if (mqtt.publish(topicoPareamento, json)) pareamento::marcarEnviado(&pedidoPareamento, millis());
}

// ------------------------------------------------------------- MQTT

void conectarMqttSePreciso() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (!ajustes::hostValido(cfg.host)) return;          // ainda sem servidor configurado
  if (millis() - ultimaTentativaMqtt < 5000) return;  // tenta a cada 5 s, sem travar
  ultimaTentativaMqtt = millis();
  Serial.printf("MQTT: conectando a %s:%u...\n", cfg.host, (unsigned)cfg.porta);
  if (mqtt.connect(idDispositivo, cfg.usuario, cfg.senha, topicoStatus, 0, true, "offline")) {
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
  snprintf(topicoPareamento, sizeof(topicoPareamento), "meterwatt/%s/pareamento", idDispositivo);
  pareamento::limpar(&pedidoPareamento);
  Serial.printf("\nMeterWatt v%s (PZEM-004T v3) - id %s\n", VERSAO_FW, idDispositivo);

  serialPzem.begin(9600, SERIAL_8N1, PINO_RX_PZEM, PINO_TX_PZEM);
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  carregarAjustes();

  mqtt.setServer(cfg.host, cfg.porta);
  mqtt.setBufferSize(512);
  mqtt.setCallback(aoReceberMqtt);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
#if USAR_PORTAL_WIFI
  // Preenche a página com o que está valendo (menos as senhas).
  snprintf(portaTexto, sizeof(portaTexto), "%u", (unsigned)cfg.porta);
  campoHost.setValue(cfg.host, ajustes::TAM_HOST - 1);
  campoPorta.setValue(portaTexto, 5);
  campoUsuario.setValue(cfg.usuario, ajustes::TAM_USUARIO - 1);
  portal.addParameter(&campoHost);
  portal.addParameter(&campoPorta);
  portal.addParameter(&campoUsuario);
  portal.addParameter(&campoSenha);
  portal.addParameter(&campoOta);
  portal.addParameter(&campoCodigo);
  portal.setSaveParamsCallback(aoSalvarPagina);
  portal.setTitle("MeterWatt");
  portal.setConfigPortalBlocking(false);   // a medição continua enquanto a página está aberta
  portal.setConnectTimeout(20);
  char nomeRede[24];
  snprintf(nomeRede, sizeof(nomeRede), "MeterWatt-%s", idDispositivo + strlen(idDispositivo) - 4);
  if (portal.autoConnect(nomeRede, SENHA_PORTAL)) {
    Serial.printf("Wi-Fi conectado: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.printf("Sem Wi-Fi salvo ou rede fora do ar. Conecte o celular na rede %s para configurar.\n", nomeRede);
  }
#else
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
#endif
  if (!ajustes::hostValido(cfg.host)) Serial.println("Servidor MQTT ainda nao configurado.");
}

void loop() {
#if USAR_PORTAL_WIFI
  portal.process();
#endif
  vigiarBotao();
  iniciarOtaSePossivel();
  if (otaIniciada) ArduinoOTA.handle();
  conectarMqttSePreciso();
  mqtt.loop();
  enviarPareamentoSePreciso();

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
