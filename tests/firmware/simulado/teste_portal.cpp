// Roda o firmware firmware/mwatt_pzem num ESP32 simulado: página de configuração, memória, atualização pelo Wi-Fi e botão BOOT.
// Use tests/firmware/simulado/rodar.sh na raiz do repositório.
#include "arduino_falso.h"
#include "../../../firmware/mwatt_pzem/mwatt_pzem.ino"
static int falhas = 0;
#define CONFERE(c) do { if (!(c)) { std::printf("FALHOU linha %d: %s\n", __LINE__, #c); falhas++; } } while (0)
static void girar(unsigned long ms) { unsigned long fim = relogio_ms + ms; while (relogio_ms < fim) { loop(); relogio_ms += 10; } }
int main() {
  // 1. Primeira vez: sem nada salvo, vale o config.h; a rede do portal tem nome e senha certos
  setup();
  CONFERE(portal.rede == "MeterWatt-E5F6" && portal.senhaRede == SENHA_PORTAL);
  CONFERE(std::string(cfg.host) == "192.168.0.10" && cfg.porta == 1883 && std::string(cfg.usuario) == "meterwatt");
  CONFERE(campoHost.valor == "192.168.0.10" && campoPorta.valor == "1883" && campoSenha.valor == "");   // senha nunca aparece na página
  CONFERE(ArduinoOTA.comecou == 0);                                   // sem senha de atualização: desligada
  girar(6000);
  CONFERE(portal.processos > 100);
  CONFERE(!publicadas.empty() && publicadas.back().topico == "meterwatt/mw-A1B2C3D4E5F6/telemetria");

  // 2. Formulário com erro não muda nada nem grava na memória
  portal.enviar({{"mqtt_host", "servidor errado"}, {"mqtt_porta", "1883"}, {"mqtt_usuario", "casa"}});
  CONFERE(std::string(cfg.host) == "192.168.0.10" && nvs.empty());

  // 3. Formulário certo: grava, reconecta e liga a atualização pelo Wi-Fi
  portal.enviar({{"mqtt_host", "broker.local"}, {"mqtt_porta", "8883"}, {"mqtt_usuario", "casa1"},
                 {"mqtt_senha", "s3nha!"}, {"ota_senha", "atualiza123"}});
  CONFERE(std::string(cfg.host) == "broker.local" && cfg.porta == 8883 && std::string(cfg.senha) == "s3nha!");
  CONFERE(nvs["host"] == "broker.local" && nvs["porta"] == "8883" && nvs["senha"] == "s3nha!" && nvs["ota"] == "atualiza123");
  CONFERE(mqtt.desconexoes == 1);
  girar(6000);
  CONFERE(mqtt.connected());                                          // reconectou com o servidor novo
  CONFERE(ArduinoOTA.comecou == 1 && ArduinoOTA.senha == "atualiza123" && ArduinoOTA.host == "mw-A1B2C3D4E5F6");
  CONFERE(ArduinoOTA.chamadas > 5);
  girar(6000);
  CONFERE(ArduinoOTA.comecou == 1);                                   // não reinicia a cada volta

  // 3b. Pareamento com o aplicativo: código errado não muda nada; certo vai ao servidor até ele confirmar
  auto pedidos = [] { size_t n = 0; for (auto& p : publicadas) if (p.topico == "meterwatt/mw-A1B2C3D4E5F6/pareamento") n++; return n; };
  portal.enviar({{"codigo", "XXXX-0000"}});
  CONFERE(!pedidoPareamento.ativo && pedidos() == 0);
  portal.enviar({{"codigo", "k7qm 2xpa"}});
  CONFERE(pedidoPareamento.ativo && campoCodigo.valor == "");        // o campo é limpo: o código não fica na página
  CONFERE(nvs.count("codigo") == 0);                                  // e nunca vai para a memória
  girar(100);
  CONFERE(pedidos() == 1 && publicadas.back().corpo == "{\"codigo\":\"K7QM-2XPA\"}" && !publicadas.back().retido);
  girar(31000);
  CONFERE(pedidos() == 2);                                            // repete a cada 30 s
  { char t[] = "meterwatt/mw-A1B2C3D4E5F6/comando"; byte r[] = {'p','a','r','e','a','d','o'}; mqtt.cb(t, r, 7); }
  CONFERE(!pedidoPareamento.ativo);
  girar(31000);
  CONFERE(pedidos() == 2);                                            // confirmado: parou de pedir

  // 4. Depois de reiniciar, vale o que foi salvo, não o config.h
  ajustes::copiar(cfg.host, "lixo", ajustes::TAM_HOST);
  carregarAjustes();
  CONFERE(std::string(cfg.host) == "broker.local" && cfg.porta == 8883 && std::string(cfg.usuario) == "casa1");

  // 5. Botão BOOT: toque rápido reabre a página por 10 min sem apagar nada; segurar 5 s apaga tudo
  nivelBotao = LOW; girar(1500); nivelBotao = HIGH; girar(100);
  CONFERE(reinicios == 0 && !nvs.empty() && portal.aberturas == 1 && portal.tempoLimite == 600);
  CONFERE(portal.rede == "MeterWatt-E5F6" && portal.senhaRede == SENHA_PORTAL);
  nivelBotao = LOW; girar(5300);
  CONFERE(reinicios >= 1 && nvs.empty() && portal.resets >= 1);
  nivelBotao = HIGH;

  // 6. Sem Wi-Fi, a medição continua e nada quebra
  wifiConectado = false; size_t antes = publicadas.size(); girar(6000);
  CONFERE(publicadas.size() >= antes);
  std::printf("\npublicacoes: %zu | falhas: %d\n", publicadas.size(), falhas);
  return falhas ? 1 : 0;
}
