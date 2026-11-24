// Firmware sem Wi-Fi por 10 minutos, num ESP32 simulado: guarda um resumo por minuto e manda tudo
// quando a conexão volta, antes das leituras ao vivo. Compilado por rodar.sh.
#include "arduino_falso.h"
#include "../../../firmware/mwatt_pzem/mwatt_pzem.ino"
static int falhas = 0;
#define CONFERE(c) do { if (!(c)) { std::printf("FALHOU linha %d: %s\n", __LINE__, #c); falhas++; } } while (0)
static void girar(unsigned long ms) { unsigned long fim = relogio_ms + ms; while (relogio_ms < fim) { loop(); relogio_ms += 10; } }
static bool tem(const std::string& s, const char* t) { return s.find(t) != std::string::npos; }
static bool atrasada(const Publicacao& p) {
  return p.topico == "meterwatt/mw-A1B2C3D4E5F6/telemetria" && p.corpo.rfind("{\"tipo\":\"atrasadas\"", 0) == 0;
}
int main() {
  setup();
  girar(10000);
  size_t antes = publicadas.size();
  CONFERE(antes > 3 && publicadas.back().topico == "meterwatt/mw-A1B2C3D4E5F6/telemetria");
  // Cai o Wi-Fi por 10 minutos: nada é publicado, a fila guarda.
  wifiConectado = false; mqtt.conectado = false;
  girar(10 * 60 * 1000);
  CONFERE(publicadas.size() == antes);
  CONFERE(filaOffline.tamanho >= 10 && filaOffline.tamanho <= 11);
  // Volta: primeiro os minutos guardados (na ordem), depois as leituras ao vivo.
  wifiConectado = true;
  girar(8000);
  std::vector<Publicacao> depois(publicadas.begin() + antes, publicadas.end());
  size_t primeira_ao_vivo = depois.size(), atrasadas = 0, minutos = 0;
  for (size_t i = 0; i < depois.size(); i++) {
    if (atrasada(depois[i])) {
      atrasadas++;
      CONFERE(i < primeira_ao_vivo);                      // nenhuma atrasada depois de uma ao vivo
      for (size_t p = depois[i].corpo.find("{\"u\":"); p != std::string::npos; p = depois[i].corpo.find("{\"u\":", p + 1)) minutos++;
    } else if (depois[i].topico == "meterwatt/mw-A1B2C3D4E5F6/telemetria" && primeira_ao_vivo == depois.size()) {
      primeira_ao_vivo = i;
    }
  }
  CONFERE(atrasadas == 2);                                // 10 ou 11 minutos: duas mensagens de até 8
  CONFERE(minutos >= 10 && minutos <= 12);
  CONFERE(primeira_ao_vivo < depois.size());              // e as leituras ao vivo voltaram
  CONFERE(fila::vazia(&filaOffline));
  std::string prim;
  for (auto& p : depois) if (atrasada(p)) { prim = p.corpo; break; }
  CONFERE(tem(prim, "\"uptime_agora\":") && tem(prim, "\"perdidos\":0") && tem(prim, "\"n\":") && tem(prim, "\"w\":548.2"));
  CONFERE(tem(prim, "\"e\":[1234"));                      // contador do PZEM no fim de cada minuto
  // Segunda queda, curta: de novo guarda e manda.
  size_t n2 = publicadas.size();
  wifiConectado = false; mqtt.conectado = false;
  girar(2 * 60 * 1000);
  CONFERE(filaOffline.tamanho >= 2);
  wifiConectado = true;
  girar(10000);
  bool voltou = false;
  for (size_t i = n2; i < publicadas.size(); i++) voltou = voltou || atrasada(publicadas[i]);
  CONFERE(voltou && fila::vazia(&filaOffline));
  std::printf("\npublicacoes: %zu | falhas: %d\n", publicadas.size(), falhas);
  return falhas ? 1 : 0;
}
