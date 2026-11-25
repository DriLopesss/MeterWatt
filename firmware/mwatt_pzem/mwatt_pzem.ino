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
 *
 * SENHA PRÓPRIA E CONEXÃO CRIPTOGRAFADA (v3.3):
 *  - Cada medidor sorteia um segredo na primeira vez que liga e o envia no pareamento. Se o
 *    servidor aceitar, o medidor passa a entrar com usuário e senha só dele. O usuário e a
 *    senha MQTT da página ficam sendo só a "conta de pareamento".
 *  - Com a porta 8883, a conexão com o servidor é criptografada (TLS) e o medidor confere
 *    o certificado do servidor (veja ca_raiz.h). Na rede de casa, a porta 1883 continua valendo.
 *  - Com uma senha de atualização definida, o firmware pode ser atualizado pelo Wi-Fi
 *    (Arduino IDE > Ferramentas > Porta > porta de rede "mw-...").
 *
 * CASA COM DUAS OU TRÊS FASES (v3.4):
 *  - Um PZEM por fase, todos no mesmo par de fios da serial, cada um com o seu endereço Modbus
 *    (PZEM_ENDERECOS no config.h). O medidor soma as fases e manda cada uma separada (fases.h).
 *  - Para dar endereço a um módulo novo: ligue SÓ ele na serial, abra o Monitor Serial (115200)
 *    e digite "endereco 2" (ou 1, 3...). "procurar" lista quem responde nos endereços 1 a 10.
 *
 * SEM WI-FI NÃO SE PERDE MEDIÇÃO (v3.5): sem conexão com o servidor, o medidor guarda um resumo por
 * minuto (até 12 horas, fila_offline.h) e manda tudo quando a conexão volta, antes das leituras ao vivo.
 *
 * Este firmware ainda NÃO foi testado com o módulo real. O protocolo foi testado no
 * computador (tests/firmware). O primeiro teste na bancada está descrito no README.
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <ArduinoOTA.h>
#include "config.h"
#include "pzem_modbus.h"
#include "fases.h"
#include "fila_offline.h"
#include "ajustes.h"
#include "pareamento.h"
#include "ca_raiz.h"

#define VERSAO_FW "3.6.0"

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
// Um endereço por fase. Configs antigas só têm PZEM_ENDERECO (um módulo).
#ifndef PZEM_ENDERECOS
#ifdef PZEM_ENDERECO
#define PZEM_ENDERECOS {PZEM_ENDERECO}
#else
#define PZEM_ENDERECOS {pzem::ENDERECO_GERAL}
#endif
#endif
#ifndef PZEM_TIMEOUT_MS
#define PZEM_TIMEOUT_MS 300
#endif
#ifndef PERMITIR_ZERAR_ENERGIA
#define PERMITIR_ZERAR_ENERGIA 0
#endif
#ifndef INTERVALO_ENVIO_MS
#define INTERVALO_ENVIO_MS 1000        // o painel e o Detetive de aparelhos contam com uma leitura por segundo
#endif
#if INTERVALO_ENVIO_MS < 1000
#error "INTERVALO_ENVIO_MS precisa ser de pelo menos 1000: o PZEM so atualiza as medidas a cada ~1 s."
#endif

HardwareSerial& serialPzem = Serial2;
WiFiClient rede;
WiFiClientSecure redeSegura;       // usada quando a porta é a 8883
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
fila::Fila filaOffline;            // minutos medidos sem conexão (~26 KB de RAM)
pareamento::Estado pedidoPareamento;
credencial::Estado cred;
char segredo[33];                  // senha própria deste medidor (sorteada na primeira vez)
char sufixo[9];                    // fim do id de cliente MQTT, sorteado na primeira vez (v3.6)
char clienteMqtt[40];              // <id>-<sufixo>: com ele, ninguém derruba a conexão entrando com o id do medidor
char topicoResposta[64];           // onde chega a resposta do pedido de pareamento em andamento
bool pedidoReconectar = false;
void salvarCredencial();

unsigned long ultimoEnvio = 0;
unsigned long ultimaTentativaMqtt = 0;
unsigned long falhasLeitura = 0;   // desde que a placa ligou
volatile bool pedidoZerarEnergia = false;

const uint8_t enderecosPzem[] = PZEM_ENDERECOS;
const size_t nFases = sizeof(enderecosPzem) / sizeof(enderecosPzem[0]);
static_assert(sizeof(enderecosPzem) / sizeof(enderecosPzem[0]) >= 1 && sizeof(enderecosPzem) / sizeof(enderecosPzem[0]) <= fases::MAXIMO,
              "PZEM_ENDERECOS precisa ter de 1 a 3 enderecos (um por fase).");
bool enderecosOk = false;          // conferido no setup(): com mais de um módulo, nada de 0xF8 nem repetidos
char linhaSerial[24];
size_t tamLinhaSerial = 0;

// Segundos desde que a placa ligou, sem a volta do millis() aos 49 dias.
uint32_t segundosLigado() {
  static unsigned long anterior = 0;
  static uint64_t total = 0;
  unsigned long agora = millis();
  total += (unsigned long)(agora - anterior);
  anterior = agora;
  return (uint32_t)(total / 1000);
}

// Manda os minutos guardados sem conexão, alguns por vez. Devolve true se a fila ficou vazia.
bool enviarAtrasadas() {
  char json[1000];
  for (int vez = 0; vez < 4 && !fila::vazia(&filaOffline); vez++) {
    size_t n = fila::montar(&filaOffline, json, sizeof(json), segundosLigado());
    if (!n || !mqtt.publish(topicoTelemetria, json)) return false;   // tenta de novo na próxima volta
    fila::tirar(&filaOffline, n);
  }
  if (fila::vazia(&filaOffline)) return true;
  return false;
}

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

pzem::Erro lerPzem(uint8_t endereco, pzem::Medidas* m) {
  uint8_t pedido[pzem::TAM_PEDIDO_LEITURA];
  uint8_t resposta[pzem::TAM_RESPOSTA_LEITURA];
  pzem::montarPedidoLeitura(endereco, pedido);
  size_t n = trocarComPzem(pedido, sizeof(pedido), resposta, sizeof(resposta));
  return pzem::interpretar(resposta, n, endereco, m);
}

bool zerarEnergiaNoPzem(uint8_t endereco) {
  uint8_t pedido[pzem::TAM_PEDIDO_ZERAR];
  uint8_t resposta[pzem::TAM_PEDIDO_ZERAR];
  pzem::montarPedidoZerar(endereco, pedido);
  size_t n = trocarComPzem(pedido, sizeof(pedido), resposta, sizeof(resposta));
  return n == sizeof(resposta) && resposta[1] == pzem::FN_ZERAR_ENERGIA && pzem::crcConfere(resposta, n);
}

// ------------------------------------------------------------- comandos pelo Monitor Serial (cabo USB)
// Só pelo cabo: dar endereço a um módulo não é coisa que se faça pela rede.

void procurarModulos() {
  Serial.println("Procurando PZEM nos enderecos 1 a 10...");
  int achados = 0;
  for (uint8_t e = 1; e <= 10; e++) {
    pzem::Medidas m;
    if (lerPzem(e, &m) == pzem::SEM_ERRO) {
      Serial.printf("  endereco %u: responde (%.1f V)\n", (unsigned)e, m.tensao_v);
      achados++;
    }
    delay(5);
  }
  if (!achados) Serial.println("  nenhum. Um modulo novo responde no endereco 1; confira 5V, GND, RX e TX.");
}

void trocarEndereco(int novo) {
  if (!pzem::enderecoValido(novo)) {
    Serial.println("Endereco precisa ser de 1 a 247. Exemplo: endereco 2");
    return;
  }
  Serial.printf("Gravando o endereco %d no modulo ligado. Deixe SO ESTE modulo na serial.\n", novo);
  uint8_t pedido[pzem::TAM_PEDIDO_ESCRITA];
  uint8_t resposta[pzem::TAM_PEDIDO_ESCRITA];
  pzem::montarPedidoEndereco(pzem::ENDERECO_GERAL, (uint8_t)novo, pedido);
  size_t n = trocarComPzem(pedido, sizeof(pedido), resposta, sizeof(resposta));
  delay(50);
  pzem::Medidas m;
  if (pzem::escritaConfirmada(pedido, resposta, n) && lerPzem((uint8_t)novo, &m) == pzem::SEM_ERRO) {
    Serial.printf("Pronto: o modulo responde no endereco %d. Anote na etiqueta dele e no PZEM_ENDERECOS do config.h.\n", novo);
  } else {
    Serial.println("O modulo nao confirmou. Confira se ha UM modulo so na serial, ligado na rede (ele se alimenta dela).");
  }
}

void executarComandoSerial(const char* linha) {
  if (strcmp(linha, "procurar") == 0) {
    procurarModulos();
  } else if (strncmp(linha, "endereco ", 9) == 0) {
    char* fim = nullptr;
    long novo = strtol(linha + 9, &fim, 10);
    trocarEndereco(fim && *fim == '\0' && fim != linha + 9 ? (int)novo : -1);
  } else if (linha[0]) {
    Serial.println("Comandos: procurar | endereco N (N de 1 a 247, com um modulo so na serial)");
  }
}

void atenderSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (tamLinhaSerial < sizeof(linhaSerial) - 1) linhaSerial[tamLinhaSerial++] = c;
      continue;
    }
    linhaSerial[tamLinhaSerial] = '\0';
    tamLinhaSerial = 0;
    executarComandoSerial(linhaSerial);
  }
}

void aoReceberMqtt(char* topico, byte* dados, unsigned int tamanho) {
  // Comandos do servidor: no tópico do medidor, ou a resposta do pedido de pareamento em andamento.
  bool resposta = pedidoPareamento.ativo && strcmp(topico, topicoResposta) == 0;
  if (strcmp(topico, topicoComando) != 0 && !resposta) return;
  switch (pareamento::aoComando(&pedidoPareamento, dados, tamanho)) {
    case pareamento::PAREADO:
      Serial.println("Pareamento: confirmado pelo servidor. O medidor ja aparece no aplicativo.");
      return;
    case pareamento::PAREADO_CREDENCIAL:
      Serial.println("Pareamento: confirmado. Este medidor passa a usar a senha propria.");
      credencial::aoReceberCredencial(&cred);
      salvarCredencial();
      pedidoReconectar = true;       // reconecta fora desta função, já com a senha própria
      return;
    case pareamento::DESPAREADO:
      Serial.println("Este medidor foi removido da conta. Para usar de novo, pareie com um codigo novo.");
      credencial::aoDesparear(&cred);
      salvarCredencial();
      pedidoReconectar = true;
      return;
    default:
      break;
  }
  const char* comando = "zerar_energia";
  if (tamanho == strlen(comando) && memcmp(dados, comando, tamanho) == 0) {
    pedidoZerarEnergia = true;
  }
}

// ------------------------------------------------------------- senha própria

// Fica numa área da memória separada dos ajustes: o BOOT de 5 segundos apaga o Wi-Fi e o
// servidor, mas o medidor continua sendo o mesmo para a conta em que está pareado.
void salvarCredencial() {
  memoria.begin("mw-id", false);
  memoria.putBool("propria", cred.propria);
  memoria.end();
}

void carregarCredencial() {
  credencial::limpar(&cred);
  segredo[0] = '\0';
  sufixo[0] = '\0';
  memoria.begin("mw-id", true);
  memoria.getString("segredo", segredo, sizeof(segredo));
  memoria.getString("sufixo", sufixo, sizeof(sufixo));
  cred.propria = memoria.getBool("propria", false);
  memoria.end();
  if (!credencial::sufixoValido(sufixo)) {
    // Medidor que veio de uma versão anterior também ganha o sufixo: o servidor só o conhece a partir do
    // próximo pareamento, então a senha própria antiga deixa de valer e ele volta para a conta de pareamento.
    credencial::montarSufixo(esp_random(), sufixo);
    memoria.begin("mw-id", false);
    memoria.putString("sufixo", sufixo);
    memoria.end();
  }
  snprintf(clienteMqtt, sizeof(clienteMqtt), "%s-%s", idDispositivo, sufixo);
  if (!credencial::segredoValido(segredo)) {
    const uint32_t aleatorio[4] = {esp_random(), esp_random(), esp_random(), esp_random()};
    credencial::montarSegredo(aleatorio, segredo);
    cred.propria = false;
    memoria.begin("mw-id", false);
    memoria.putString("segredo", segredo);
    memoria.putBool("propria", false);
    memoria.end();
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
    const uint32_t aleatorio[2] = {esp_random(), esp_random()};
    char resposta[17];
    pareamento::montarResposta(aleatorio, resposta);
    pareamento::iniciar(&pedidoPareamento, codigo, millis(), resposta);
    pareamento::montarTopicoResposta(pedidoPareamento, topicoResposta, sizeof(topicoResposta));
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
  char json[192];
  pareamento::montarMensagem(pedidoPareamento, segredo, clienteMqtt, json, sizeof(json));
  if (mqtt.publish(topicoPareamento, json)) pareamento::marcarEnviado(&pedidoPareamento, millis());
}

// ------------------------------------------------------------- MQTT

void conectarMqttSePreciso() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (!ajustes::hostValido(cfg.host)) return;          // ainda sem servidor configurado
  if (millis() - ultimaTentativaMqtt < 5000) return;  // tenta a cada 5 s, sem travar
  ultimaTentativaMqtt = millis();
  const bool tls = cfg.porta == 8883;
  const bool propria = credencial::usarPropria(cred);
  if (tls) mqtt.setClient(redeSegura); else mqtt.setClient(rede);
  Serial.printf("MQTT: conectando a %s:%u%s com a %s...\n", cfg.host, (unsigned)cfg.porta, tls ? " (criptografado)" : "",
                propria ? "senha propria" : "conta de pareamento");
  if (mqtt.connect(clienteMqtt, propria ? idDispositivo : cfg.usuario, propria ? segredo : cfg.senha,
                   topicoStatus, 0, true, "offline")) {
    credencial::aoConectar(&cred);
    mqtt.publish(topicoStatus, "online", true);
    mqtt.subscribe(topicoComando);
    if (pedidoPareamento.ativo) mqtt.subscribe(topicoResposta);
    Serial.println("MQTT: conectado");
  } else {
    Serial.printf("MQTT: falhou (estado %d)\n", mqtt.state());
    if (credencial::aoFalhar(&cred, mqtt.state())) {
      Serial.println("A senha propria foi recusada 3 vezes: o medidor pode ter sido removido da conta. "
                     "Voltando para a conta de pareamento; gere um codigo novo no aplicativo.");
    }
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
  fila::limpar(&filaOffline);
  pareamento::limpar(&pedidoPareamento);
  Serial.printf("\nMeterWatt v%s (PZEM-004T v3) - id %s\n", VERSAO_FW, idDispositivo);

  serialPzem.begin(9600, SERIAL_8N1, PINO_RX_PZEM, PINO_TX_PZEM);
  enderecosOk = fases::enderecosValidos(enderecosPzem, nFases);
  if (!enderecosOk) {
    Serial.println("ERRO no config.h: com mais de um PZEM, cada um precisa de um endereco de 1 a 247, sem repetir "
                   "(o 0xF8 so serve com um modulo). O medidor nao vai ler ate isso ser corrigido.");
  } else if (nFases > 1) {
    Serial.printf("%u fases: um PZEM por fase no mesmo par de fios.\n", (unsigned)nFases);
  }
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  carregarAjustes();
  carregarCredencial();
  redeSegura.setCACert(CA_RAIZ);

  mqtt.setServer(cfg.host, cfg.porta);
  mqtt.setBufferSize(1024);         // com três fases, a mensagem passa de 512 bytes
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
  if (pedidoReconectar) {
    pedidoReconectar = false;
    mqtt.disconnect();
    ultimaTentativaMqtt = 0;
  }
  enviarPareamentoSePreciso();
  atenderSerial();

  if (pedidoZerarEnergia) {
    pedidoZerarEnergia = false;
    if (PERMITIR_ZERAR_ENERGIA) {
      bool ok = enderecosOk;
      for (size_t i = 0; i < nFases && enderecosOk; i++) ok = zerarEnergiaNoPzem(enderecosPzem[i]) && ok;
      Serial.printf("Zerar energia: %s\n", ok ? "feito" : "o modulo nao confirmou");
    } else {
      Serial.println("Zerar energia: ignorado (PERMITIR_ZERAR_ENERGIA esta em 0 no config.h)");
    }
  }

  // Voltou a conexão com minutos guardados: eles vão primeiro, na ordem, e só depois as leituras ao vivo.
  bool comFila = !fila::vazia(&filaOffline);
  if (comFila && mqtt.connected()) {
    comFila = !enviarAtrasadas();
    if (!comFila) Serial.println("Minutos medidos sem conexao: enviados.");
  }

  if (millis() - ultimoEnvio < INTERVALO_ENVIO_MS) return;
  ultimoEnvio = millis();

  char json[768];
  if (!enderecosOk) {
    falhasLeitura++;
    snprintf(json, sizeof(json),
             "{\"fw\":\"%s\",\"sensor\":\"pzem004t-v3\",\"sensor_ok\":false,\"erro_sensor\":\"enderecos_invalidos\","
             "\"falhas_leitura\":%lu,\"rssi\":%d,\"uptime_s\":%lu}",
             VERSAO_FW, falhasLeitura, WiFi.RSSI(), (unsigned long)segundosLigado());
  } else {
    pzem::Medidas m[fases::MAXIMO];
    pzem::Erro erros[fases::MAXIMO];
    bool falhou = false;
    for (size_t i = 0; i < nFases; i++) {
      if (i) delay(5);               // silêncio entre pedidos no mesmo barramento (Modbus RTU)
      erros[i] = lerPzem(enderecosPzem[i], &m[i]);
      falhou = falhou || erros[i] != pzem::SEM_ERRO;
    }
    // Leitura perdida (de qualquer fase) conta uma falha. Sem leitura válida, a mensagem NUNCA repete números antigos.
    if (falhou) falhasLeitura++;
    if (!fases::montarTelemetria(json, sizeof(json), VERSAO_FW, m, erros, nFases, falhasLeitura, WiFi.RSSI(), segundosLigado())) {
      Serial.println("Mensagem maior que o espaco reservado: nao enviada.");
      return;
    }
    // Sem conexão (ou ainda mandando o que ficou guardado): a leitura vai para a fila, não se perde.
    if (!mqtt.connected() || comFila) {
      fases::Total total;
      if (fases::somar(m, erros, nFases, &total)) {
        uint32_t energia[fases::MAXIMO] = {0, 0, 0};
        uint8_t validas = 0;
        for (size_t i = 0; i < nFases; i++) {
          if (erros[i] != pzem::SEM_ERRO) continue;
          energia[i] = m[i].energia_wh;
          validas |= (uint8_t)(1u << i);
        }
        fila::guardar(&filaOffline, segundosLigado(), total.potencia_w, energia, validas, (uint8_t)nFases);
      }
      Serial.println(json);
      return;
    }
  }

  Serial.println(json);
  if (mqtt.connected()) mqtt.publish(topicoTelemetria, json);
}
