// Teste da junção das fases (firmware/mwatt_pzem/fases.h) no computador. Na raiz do repositório:
//   g++ -std=c++11 -Wall -Wextra -Werror -o /tmp/teste_fases tests/firmware/teste_fases.cpp && /tmp/teste_fases
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "../../firmware/mwatt_pzem/fases.h"

static int falhas = 0;
#define CONFERE(cond)                                                    \
  do {                                                                   \
    if (!(cond)) {                                                       \
      std::printf("FALHOU linha %d: %s\n", __LINE__, #cond);             \
      falhas++;                                                          \
    }                                                                    \
  } while (0)

static bool perto(float a, float b, float tol = 0.01f) { return std::fabs(a - b) <= tol; }
static bool tem(const char* s, const char* trecho) { return std::strstr(s, trecho) != nullptr; }

static pzem::Medidas med(float v, float i, float p, uint32_t e, float fp) {
  pzem::Medidas m;
  m.tensao_v = v; m.corrente_a = i; m.potencia_w = p; m.energia_wh = e; m.frequencia_hz = 60.0f; m.fp = fp; m.alarme = false;
  return m;
}

int main() {
  // 1. Endereços
  const uint8_t um_geral[] = {0xF8}, um_proprio[] = {0x01}, dois[] = {0x01, 0x02}, tres[] = {0x01, 0x02, 0x03};
  const uint8_t dois_geral[] = {0xF8, 0x02}, repetido[] = {0x01, 0x01}, zero[] = {0x00, 0x02}, quatro[] = {1, 2, 3, 4};
  CONFERE(fases::enderecosValidos(um_geral, 1) && fases::enderecosValidos(um_proprio, 1));
  CONFERE(fases::enderecosValidos(dois, 2) && fases::enderecosValidos(tres, 3));
  CONFERE(!fases::enderecosValidos(dois_geral, 2));   // o geral faria os dois responderem juntos
  CONFERE(!fases::enderecosValidos(repetido, 2) && !fases::enderecosValidos(zero, 2));
  CONFERE(!fases::enderecosValidos(quatro, 4) && !fases::enderecosValidos(dois, 0));

  // 2. Chuveiro de 220 V entre as duas fases (bifásica 127/220): cada PZEM vê 127 V e a mesma corrente,
  //    com fator de potência cos(30°) ≈ 0,87; a soma é 220 V × 25 A ≈ 5.500 W.
  pzem::Medidas m[3];
  pzem::Erro e[3] = {pzem::SEM_ERRO, pzem::SEM_ERRO, pzem::SEM_ERRO};
  m[0] = med(127.0f, 25.0f, 2750.0f, 1000, 0.87f);
  m[1] = med(127.0f, 25.0f, 2750.0f, 2000, 0.87f);
  fases::Total t;
  CONFERE(fases::somar(m, e, 2, &t));
  CONFERE(t.lidas == 2 && perto(t.potencia_w, 5500.0f) && perto(t.corrente_a, 50.0f) && perto(t.tensao_v, 127.0f));
  CONFERE(t.energia_wh == 3000u && perto(t.fp, 5500.0f / (2 * 127.0f * 25.0f), 0.001f) && perto(t.frequencia_hz, 60.0f));

  // 3. Mensagem com duas fases
  char buf[1024];
  size_t n = fases::montarTelemetria(buf, sizeof(buf), "3.4.0", m, e, 2, 0, -60, 10);
  CONFERE(n > 0 && n == std::strlen(buf));
  CONFERE(tem(buf, "\"sensor_ok\":true") && tem(buf, "\"potencia_w\":5500.0") && tem(buf, "\"energia_wh\":3000,"));
  CONFERE(tem(buf, "\"fases\":[{\"fase\":1,\"ok\":true,\"tensao_v\":127.0,\"corrente_a\":25.000,\"potencia_w\":2750.0,\"energia_wh\":1000,\"fp\":0.87}"));
  CONFERE(tem(buf, ",{\"fase\":2,\"ok\":true,") && tem(buf, "],\"falhas_leitura\":0,\"rssi\":-60,\"uptime_s\":10}"));

  // 4. Fase 2 sem resposta: as outras continuam, e a mensagem diz qual faltou
  m[2] = med(128.0f, 2.0f, 250.0f, 500, 0.98f);
  e[1] = pzem::SEM_RESPOSTA;
  n = fases::montarTelemetria(buf, sizeof(buf), "3.4.0", m, e, 3, 4, -60, 10);
  CONFERE(n > 0);
  CONFERE(tem(buf, "\"sensor_ok\":true") && tem(buf, "\"potencia_w\":3000.0") && tem(buf, "\"energia_wh\":1500,"));
  CONFERE(tem(buf, "{\"fase\":2,\"ok\":false,\"erro\":\"sem_resposta\"}") && tem(buf, "{\"fase\":3,\"ok\":true"));
  CONFERE(tem(buf, "\"tensao_v\":127.5,"));   // média das duas lidas

  // 5. Nenhuma fase: sem números, com o erro da primeira e a lista
  e[0] = pzem::CRC_INVALIDO; e[2] = pzem::SEM_RESPOSTA;
  n = fases::montarTelemetria(buf, sizeof(buf), "3.4.0", m, e, 3, 5, -60, 10);
  CONFERE(n > 0 && tem(buf, "\"sensor_ok\":false,\"erro_sensor\":\"crc_invalido\"") && !tem(buf, "potencia_w"));
  CONFERE(tem(buf, "{\"fase\":3,\"ok\":false,\"erro\":\"sem_resposta\"}"));

  // 6. Um módulo só: a mesma mensagem das versões anteriores, sem "fases"
  e[0] = pzem::SEM_ERRO;
  m[0] = med(127.3f, 4.321f, 548.2f, 12345, 0.98f);
  n = fases::montarTelemetria(buf, sizeof(buf), "3.4.0", m, e, 1, 0, -61, 5);
  CONFERE(std::string(buf) == "{\"fw\":\"3.4.0\",\"sensor\":\"pzem004t-v3\",\"sensor_ok\":true,\"tensao_v\":127.3,"
                              "\"corrente_a\":4.321,\"potencia_w\":548.2,\"energia_wh\":12345,\"frequencia_hz\":60.0,"
                              "\"fp\":0.98,\"alarme\":false,\"falhas_leitura\":0,\"rssi\":-61,\"uptime_s\":5}");

  // 7. Três fases cabem no tamanho que o firmware usa, e um espaço curto devolve 0 (sem estourar)
  e[0] = e[1] = e[2] = pzem::SEM_ERRO;
  for (int i = 0; i < 3; i++) m[i] = med(230.0f, 99.999f, 22999.9f, 4000000000u, 1.0f);
  n = fases::montarTelemetria(buf, 768, "3.4.0", m, e, 3, 4000000000ul, -100, 4000000000ul);
  CONFERE(n > 0 && n < 768);
  std::printf("maior mensagem (3 fases, números máximos): %zu bytes\n", n);
  char curto[64];
  CONFERE(fases::montarTelemetria(curto, sizeof(curto), "3.4.0", m, e, 3, 0, -60, 1) == 0);

  if (falhas == 0) std::printf("OK: todos os testes das fases passaram\n");
  return falhas == 0 ? 0 : 1;
}
