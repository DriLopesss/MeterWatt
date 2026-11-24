// Teste da fila de minutos sem conexão (firmware/mwatt_pzem/fila_offline.h). Na raiz do repositório:
//   g++ -std=c++11 -Wall -Wextra -Werror -o /tmp/teste_fila tests/firmware/teste_fila_offline.cpp && /tmp/teste_fila
#include <cstdio>
#include <cstring>
#include <string>

#include "../../firmware/mwatt_pzem/fila_offline.h"

static int falhas = 0;
#define CONFERE(cond)                                                    \
  do {                                                                   \
    if (!(cond)) {                                                       \
      std::printf("FALHOU linha %d: %s\n", __LINE__, #cond);             \
      falhas++;                                                          \
    }                                                                    \
  } while (0)

static fila::Fila f;   // grande demais para a pilha, como no firmware
static bool tem(const char* s, const char* t) { return std::strstr(s, t) != nullptr; }

int main() {
  fila::limpar(&f);
  CONFERE(fila::vazia(&f));
  char buf[1000];
  CONFERE(fila::montar(&f, buf, sizeof(buf), 100) == 0);

  // 1. Uma fase, 3 minutos (uptime 120 a 299): resumo de cada minuto e o contador no fim dele.
  uint32_t e[3] = {0, 0, 0};
  for (uint32_t s = 120; s < 300; s++) {
    e[0] = 1000 + s / 10;
    fila::guardar(&f, s, s < 180 ? 100.0f : 300.0f, e, 1, 1);
  }
  CONFERE(f.tamanho == 3);
  size_t n = fila::montar(&f, buf, sizeof(buf), 400);
  CONFERE(n == 3);
  CONFERE(tem(buf, "{\"tipo\":\"atrasadas\",\"uptime_agora\":400,\"perdidos\":0,\"minutos\":[{\"u\":179,\"n\":60,\"w\":100.0,\"min\":100.0,\"max\":100.0,\"e\":[1017]}"));
  CONFERE(tem(buf, "{\"u\":299,\"n\":60,\"w\":300.0,\"min\":300.0,\"max\":300.0,\"e\":[1029]}]}"));
  fila::tirar(&f, n);
  CONFERE(fila::vazia(&f));

  // 2. Duas fases, a 2 sem resposta num minuto: vai null no lugar do contador dela.
  e[0] = 50; e[1] = 70;
  fila::guardar(&f, 600, 800.0f, e, 0x3, 2);
  fila::guardar(&f, 660, 500.0f, e, 0x1, 2);
  n = fila::montar(&f, buf, sizeof(buf), 700);
  CONFERE(n == 2 && tem(buf, "\"e\":[50,70]}") && tem(buf, "\"e\":[50,null]}"));
  fila::tirar(&f, n);

  // 3. Mínimo e máximo dentro do minuto
  fila::guardar(&f, 900, 200.0f, e, 1, 1);
  fila::guardar(&f, 901, 5600.0f, e, 1, 1);
  fila::guardar(&f, 902, 150.0f, e, 1, 1);
  CONFERE(fila::montar(&f, buf, sizeof(buf), 950) == 1 && tem(buf, "\"min\":150.0,\"max\":5600.0"));
  fila::tirar(&f, 1);

  // 4. Fila cheia: ficam os mais recentes, e a mensagem conta os perdidos.
  for (uint32_t m = 0; m < fila::MINUTOS + 5; m++) {
    e[0] = m;
    fila::guardar(&f, 10000 + m * 60, 100.0f, e, 1, 1);
  }
  CONFERE(f.tamanho == fila::MINUTOS && f.perdidos == 5);
  n = fila::montar(&f, buf, sizeof(buf), 99999);
  CONFERE(n == fila::POR_MENSAGEM && tem(buf, "\"perdidos\":5") && tem(buf, "\"u\":10300,"));   // o 6º minuto é o primeiro
  // 5. Mensagem cheia, de 3 fases com números grandes, cabe no buffer do firmware (e no MQTT de 1024)
  fila::limpar(&f);
  uint32_t grandes[3] = {4000000000u, 4000000000u, 4000000000u};
  for (uint32_t m = 0; m < 10; m++) fila::guardar(&f, 4000000000u + m * 60, 29999.9f, grandes, 0x7, 3);
  n = fila::montar(&f, buf, sizeof(buf), 4000009999u);
  CONFERE(n == fila::POR_MENSAGEM && std::strlen(buf) + 40 < 1024);
  std::printf("maior mensagem: %zu bytes\n", std::strlen(buf));
  char curto[80];
  CONFERE(fila::montar(&f, curto, sizeof(curto), 1) == 0);
  // 6. Tirar tudo zera os perdidos
  while (!fila::vazia(&f)) fila::tirar(&f, fila::POR_MENSAGEM);
  CONFERE(f.perdidos == 0 && f.tamanho == 0);

  if (falhas == 0) std::printf("OK: todos os testes da fila passaram\n");
  return falhas == 0 ? 0 : 1;
}
