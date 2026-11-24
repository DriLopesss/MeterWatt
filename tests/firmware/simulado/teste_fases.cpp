// Firmware com dois PZEM no mesmo par de fios (casa bifásica), num ESP32 simulado.
// Compilado por rodar.sh com PZEM_ENDERECOS {0x01, 0x02} no config.h.
#include "arduino_falso.h"
#include "../../../firmware/mwatt_pzem/mwatt_pzem.ino"
static int falhas = 0;
#define CONFERE(c) do { if (!(c)) { std::printf("FALHOU linha %d: %s\n", __LINE__, #c); falhas++; } } while (0)
static void girar(unsigned long ms) { unsigned long fim = relogio_ms + ms; while (relogio_ms < fim) { loop(); relogio_ms += 10; } }
static bool tem(const std::string& s, const char* t) { return s.find(t) != std::string::npos; }
int main() {
  // Dois módulos de fábrica (os dois no endereço 1). Primeiro: dar o endereço 2 ao segundo, sozinho na serial.
  ModuloPzem segundo; segundo.energia = 500; segundo.tensao = 1281; segundo.corrente = 2000; segundo.potencia = 2400; segundo.fp = 94;
  Serial2.modulos[0].modo = MUDO;            // o primeiro ainda desligado da serial
  Serial2.modulos.push_back(segundo);
  setup();
  CONFERE(tem(Serial.saida, "2 fases"));
  Serial.digitar("endereco 2\n"); girar(100);
  CONFERE(Serial2.modulos[1].endereco == 2);
  CONFERE(tem(Serial.saida, "responde no endereco 2"));
  Serial.digitar("endereco 300\n"); girar(100);
  CONFERE(tem(Serial.saida, "de 1 a 247"));
  Serial.digitar("bobagem\n"); girar(100);
  CONFERE(tem(Serial.saida, "Comandos: procurar"));
  // Com os dois na serial, o "endereco" pelo 0xF8 faria os dois responderem juntos: o firmware não confirma.
  Serial2.modulos[0].modo = NORMAL;
  Serial.saida.clear();
  Serial.digitar("endereco 5\n"); girar(100);
  CONFERE(tem(Serial.saida, "nao confirmou"));
  Serial2.modulos[0].endereco = 1; Serial2.modulos[1].endereco = 2;   // a colisão mudou os dois; volta ao combinado
  Serial.saida.clear();
  Serial.digitar("procurar\n"); girar(100);
  CONFERE(tem(Serial.saida, "endereco 1: responde (127.3 V)") && tem(Serial.saida, "endereco 2: responde (128.1 V)"));

  // Leitura normal: soma das duas fases e cada uma separada.
  girar(6000);
  std::string ult = publicadas.back().corpo;
  CONFERE(publicadas.back().topico == "meterwatt/mw-A1B2C3D4E5F6/telemetria");
  CONFERE(tem(ult, "\"sensor_ok\":true") && tem(ult, "\"potencia_w\":788.2") && tem(ult, "\"corrente_a\":6.321"));
  CONFERE(tem(ult, "{\"fase\":1,\"ok\":true,\"tensao_v\":127.3") && tem(ult, "{\"fase\":2,\"ok\":true,\"tensao_v\":128.1"));
  // Fase 2 sem energia (o PZEM dela apaga): a fase 1 continua, e a mensagem diz qual faltou.
  Serial2.modulos[1].modo = MUDO; girar(6000);
  ult = publicadas.back().corpo;
  CONFERE(tem(ult, "\"sensor_ok\":true") && tem(ult, "\"potencia_w\":548.2") && tem(ult, "{\"fase\":2,\"ok\":false,\"erro\":\"sem_resposta\"}"));
  // As duas mudas: sem números.
  Serial2.modulos[0].modo = MUDO; girar(6000);
  ult = publicadas.back().corpo;
  CONFERE(tem(ult, "\"sensor_ok\":false") && !tem(ult, "potencia_w"));
  // Voltam sozinhas.
  Serial2.modulos[0].modo = NORMAL; Serial2.modulos[1].modo = NORMAL; girar(6000);
  CONFERE(tem(publicadas.back().corpo, "\"potencia_w\":788.2"));
  size_t maior = 0; for (auto& p : publicadas) maior = p.corpo.size() > maior ? p.corpo.size() : maior;
  std::printf("\npublicacoes: %zu | maior JSON: %zu bytes | falhas: %d\n", publicadas.size(), maior, falhas);
  return falhas ? 1 : 0;
}
