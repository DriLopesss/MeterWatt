// Teste do pareamento com o aplicativo e da validação do código (sem ESP32).
// Rodar na raiz do repositório:
//   g++ -std=c++11 -Wall -Wextra -o /tmp/teste_pareamento tests/firmware/teste_pareamento.cpp && /tmp/teste_pareamento
#include <cstdio>
#include <cstring>

#include "../../firmware/mwatt_pzem/ajustes.h"
#include "../../firmware/mwatt_pzem/pareamento.h"

static int falhas = 0;
#define CONFERE(cond)                                                    \
  do {                                                                   \
    if (!(cond)) {                                                       \
      std::printf("FALHOU linha %d: %s\n", __LINE__, #cond);             \
      falhas++;                                                          \
    }                                                                    \
  } while (0)

static bool normaliza(const char* entrada, const char* esperado) {
  char saida[ajustes::TAM_CODIGO] = "x";
  bool ok = ajustes::normalizarCodigo(entrada, saida);
  return esperado ? (ok && std::strcmp(saida, esperado) == 0) : !ok;
}

int main() {
  // 1. Código: aceita jeitos diferentes de digitar, recusa o resto
  CONFERE(normaliza("K7QM-2XPA", "K7QM-2XPA"));
  CONFERE(normaliza("k7qm2xpa", "K7QM-2XPA"));
  CONFERE(normaliza(" k7qm - 2xpa ", "K7QM-2XPA"));
  CONFERE(normaliza("K7QM-2XP", nullptr));          // curto
  CONFERE(normaliza("K7QM-2XPAB", nullptr));        // longo
  CONFERE(normaliza("K7QM-2XP0", nullptr));         // 0 não existe no alfabeto
  CONFERE(normaliza("K7QM-2XPI", nullptr));         // I também não
  CONFERE(normaliza("K7QM_2XPA", nullptr));
  CONFERE(normaliza("ÇÇÇÇ-AAAA", nullptr));
  CONFERE(normaliza("", nullptr));
  CONFERE(normaliza(nullptr, nullptr));

  // 2. Formulário: código errado não muda nada; certo sai normalizado; vazio = sem pareamento
  ajustes::Ajustes a;
  ajustes::copiar(a.host, "192.168.0.10", ajustes::TAM_HOST);
  a.porta = 1883;
  ajustes::copiar(a.usuario, "casa", ajustes::TAM_USUARIO);
  ajustes::copiar(a.senha, "s", ajustes::TAM_SENHA);
  ajustes::copiar(a.senhaOta, "", ajustes::TAM_SENHA_OTA);
  char codigo[ajustes::TAM_CODIGO] = "lixo";
  CONFERE(ajustes::aplicarFormulario(&a, "novo.local", "1883", "casa", "", "", false, "AAAA", codigo) == ajustes::CODIGO_INVALIDO);
  CONFERE(std::strcmp(a.host, "192.168.0.10") == 0 && std::strcmp(codigo, "lixo") == 0);
  CONFERE(ajustes::aplicarFormulario(&a, "novo.local", "1883", "casa", "", "", false, "abcd efgh", codigo) == ajustes::AJUSTES_OK);
  CONFERE(std::strcmp(a.host, "novo.local") == 0 && std::strcmp(codigo, "ABCD-EFGH") == 0);
  CONFERE(ajustes::aplicarFormulario(&a, "novo.local", "1883", "casa", "", "", false, "", codigo) == ajustes::AJUSTES_OK);
  CONFERE(codigo[0] == '\0');

  // 3. Estado do pareamento: envia logo, repete a cada 30 s, para quando o servidor confirma
  pareamento::Estado e;
  pareamento::limpar(&e);
  CONFERE(!pareamento::deveEnviar(&e, 1000, true));
  pareamento::iniciar(&e, "ABCD-EFGH", 1000);
  CONFERE(!pareamento::deveEnviar(&e, 1000, false));       // sem MQTT, espera
  CONFERE(pareamento::deveEnviar(&e, 1000, true));
  pareamento::marcarEnviado(&e, 1000);
  CONFERE(!pareamento::deveEnviar(&e, 20000, true));
  CONFERE(pareamento::deveEnviar(&e, 31000, true));
  char msg[48];
  pareamento::montarMensagem(e, msg, sizeof(msg));
  CONFERE(std::strcmp(msg, "{\"codigo\":\"ABCD-EFGH\"}") == 0);
  const unsigned char outro[] = "zerar_energia";
  CONFERE(!pareamento::aoComando(&e, outro, 13) && e.ativo);
  const unsigned char ok[] = "pareado";
  CONFERE(pareamento::aoComando(&e, ok, 7) && !e.ativo && e.codigo[0] == '\0');
  CONFERE(!pareamento::aoComando(&e, ok, 7));              // repetido: nada a fazer

  // 4. Expira em 15 minutos e apaga o código
  pareamento::iniciar(&e, "ABCD-EFGH", 5000);
  CONFERE(pareamento::deveEnviar(&e, 5000 + 14UL * 60000UL, true));
  CONFERE(!pareamento::deveEnviar(&e, 5000 + 15UL * 60000UL + 1, true) && !e.ativo && e.codigo[0] == '\0');

  // 5. millis() dá a volta (49 dias): a conta com unsigned continua certa
  pareamento::iniciar(&e, "ABCD-EFGH", 0xFFFFFF00UL);
  CONFERE(pareamento::deveEnviar(&e, 0x00000100UL, true));

  std::printf("teste_pareamento: %s (%d falha(s))\n", falhas ? "FALHOU" : "ok", falhas);
  return falhas ? 1 : 0;
}
