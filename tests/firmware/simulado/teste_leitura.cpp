// Roda o firmware firmware/mwatt_pzem num ESP32 simulado: leitura do PZEM, falhas de comunicação e comando de zerar energia.
// Use tests/firmware/simulado/rodar.sh na raiz do repositório.
#include "arduino_falso.h"
#include "../../../firmware/mwatt_pzem/mwatt_pzem.ino"
static int falhas = 0;
#define CONFERE(c) do { if (!(c)) { std::printf("FALHOU linha %d: %s\n", __LINE__, #c); falhas++; } } while (0)
static void girar(unsigned long ms) { unsigned long fim = relogio_ms + ms; while (relogio_ms < fim) { loop(); relogio_ms += 10; } }
int main() {
  setup();
  CONFERE(Serial2.baud == 9600 && Serial2.rx == 16 && Serial2.tx == 17);
  girar(12000);
  CONFERE(!publicadas.empty() && publicadas[0].topico == "meterwatt/mw-A1B2C3D4E5F6/status" && publicadas[0].corpo == "online" && publicadas[0].retido);
  CONFERE(assinados.size() == 1 && assinados[0] == "meterwatt/mw-A1B2C3D4E5F6/comando");
  size_t n_ok = publicadas.size();
  CONFERE(n_ok >= 3);
  std::string ult = publicadas.back().corpo;
  CONFERE(publicadas.back().topico == "meterwatt/mw-A1B2C3D4E5F6/telemetria");
  CONFERE(ult.find("\"tensao_v\":127.3") != std::string::npos && ult.find("\"corrente_a\":4.321") != std::string::npos);
  CONFERE(ult.find("\"potencia_w\":548.2") != std::string::npos && ult.find("\"fp\":0.98") != std::string::npos);
  CONFERE(ult.find("\"energia_wh\":12345") != std::string::npos && ult.find("\"sensor_ok\":true") != std::string::npos);
  // módulo mudo: avisa a falha e não repete medidas
  Serial2.modo = MUDO; girar(6000);
  ult = publicadas.back().corpo;
  CONFERE(ult.find("\"sensor_ok\":false") != std::string::npos && ult.find("sem_resposta") != std::string::npos);
  CONFERE(ult.find("tensao_v") == std::string::npos);
  // ruído na linha
  Serial2.modo = CORROMPIDO; girar(6000);
  CONFERE(publicadas.back().corpo.find("crc_invalido") != std::string::npos);
  // volta ao normal sozinho
  Serial2.modo = NORMAL; girar(6000);
  CONFERE(publicadas.back().corpo.find("\"sensor_ok\":true") != std::string::npos);
  CONFERE(publicadas.back().corpo.find("\"falhas_leitura\":2") != std::string::npos);
  // comando de zerar
  char topo[64]; std::strcpy(topo, "meterwatt/mw-A1B2C3D4E5F6/comando");
  byte cmd[] = "zerar_energia"; aoReceberMqtt(topo, cmd, 13); girar(200);
  CONFERE(Serial2.zerou == (PERMITE == 1));
  byte lixo[] = "apagar_tudo"; Serial2.zerou = false; aoReceberMqtt(topo, lixo, 11); girar(200);
  CONFERE(!Serial2.zerou);
  size_t maior = 0; for (auto& p : publicadas) maior = p.corpo.size() > maior ? p.corpo.size() : maior;
  std::printf("\npublicacoes: %zu | maior JSON: %zu bytes | falhas: %d\n", publicadas.size(), maior, falhas);
  return falhas ? 1 : 0;
}
