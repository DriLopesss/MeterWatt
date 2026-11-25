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
  pareamento::iniciar(&e, "ABCD-EFGH", 1000, "0011223344556677");
  CONFERE(!pareamento::deveEnviar(&e, 1000, false));       // sem MQTT, espera
  CONFERE(pareamento::deveEnviar(&e, 1000, true));
  pareamento::marcarEnviado(&e, 1000);
  CONFERE(!pareamento::deveEnviar(&e, 20000, true));
  CONFERE(pareamento::deveEnviar(&e, 31000, true));
  char msg[192];
  int n = pareamento::montarMensagem(e, "0123456789abcdef0123456789abcdef", "mw-A1B2C3D4E5F6-5e1a9c07", msg, sizeof(msg));
  CONFERE(std::strcmp(msg, "{\"codigo\":\"ABCD-EFGH\",\"segredo\":\"0123456789abcdef0123456789abcdef\","
                           "\"cliente\":\"mw-A1B2C3D4E5F6-5e1a9c07\",\"resposta\":\"0011223344556677\"}") == 0 && n < 192);
  char topico[64];
  pareamento::montarTopicoResposta(e, topico, sizeof(topico));
  CONFERE(std::strcmp(topico, "meterwatt-resposta/0011223344556677") == 0);
  char r16[17];
  const uint32_t al[2] = {0x01234567u, 0x89abcdefu};
  pareamento::montarResposta(al, r16);
  CONFERE(std::strcmp(r16, "0123456789abcdef") == 0);
  char suf[9];
  credencial::montarSufixo(0x5e1a9c07u, suf);
  CONFERE(std::strcmp(suf, "5e1a9c07") == 0 && credencial::sufixoValido(suf));
  CONFERE(!credencial::sufixoValido("5E1A9C07") && !credencial::sufixoValido("5e1a9c0") && !credencial::sufixoValido("5e1a9c071"));
  const unsigned char outro[] = "zerar_energia";
  CONFERE(pareamento::aoComando(&e, outro, 13) == pareamento::OUTRO_COMANDO && e.ativo);
  const unsigned char ok[] = "pareado";
  CONFERE(pareamento::aoComando(&e, ok, 7) == pareamento::PAREADO && !e.ativo && e.codigo[0] == '\0');
  CONFERE(pareamento::aoComando(&e, ok, 7) == pareamento::OUTRO_COMANDO);     // repetido: nada a fazer
  const unsigned char okCred[] = "pareado_credencial";
  CONFERE(pareamento::aoComando(&e, okCred, 18) == pareamento::OUTRO_COMANDO); // sem pedido em andamento, não vale
  pareamento::iniciar(&e, "ABCD-EFGH", 1000, "0011223344556677");
  CONFERE(pareamento::aoComando(&e, okCred, 18) == pareamento::PAREADO_CREDENCIAL && !e.ativo);
  const unsigned char fora[] = "despareado";
  CONFERE(pareamento::aoComando(&e, fora, 10) == pareamento::DESPAREADO);

  // 4. Expira em 15 minutos e apaga o código
  pareamento::iniciar(&e, "ABCD-EFGH", 5000, "0011223344556677");
  CONFERE(pareamento::deveEnviar(&e, 5000 + 14UL * 60000UL, true));
  CONFERE(!pareamento::deveEnviar(&e, 5000 + 15UL * 60000UL + 1, true) && !e.ativo && e.codigo[0] == '\0');

  // 5. millis() dá a volta (49 dias): a conta com unsigned continua certa
  pareamento::iniciar(&e, "ABCD-EFGH", 0xFFFFFF00UL, "0011223344556677");
  CONFERE(pareamento::deveEnviar(&e, 0x00000100UL, true));

  // 6. Senha própria: só desiste depois de 3 recusas do servidor; rede fora do ar não conta
  credencial::Estado c;
  credencial::limpar(&c);
  CONFERE(!credencial::usarPropria(c) && !credencial::aoFalhar(&c, 5));
  credencial::aoReceberCredencial(&c);
  CONFERE(credencial::usarPropria(c));
  CONFERE(!credencial::aoFalhar(&c, -2) && !credencial::aoFalhar(&c, -4) && c.recusas == 0);
  CONFERE(!credencial::aoFalhar(&c, 5) && !credencial::aoFalhar(&c, 4));
  credencial::aoConectar(&c);
  CONFERE(c.recusas == 0);                                  // conectou no meio: zera a contagem
  CONFERE(!credencial::aoFalhar(&c, 5) && !credencial::aoFalhar(&c, 5) && credencial::aoFalhar(&c, 5));
  CONFERE(!credencial::usarPropria(c) && c.propria && c.emProvisao);
  credencial::aoReceberCredencial(&c);
  CONFERE(credencial::usarPropria(c));
  credencial::aoDesparear(&c);
  CONFERE(!c.propria && !credencial::usarPropria(c));

  // 7. Segredo: 32 letras e números, e só isso
  const uint32_t aleatorio[4] = {0x1u, 0xABCDEF01u, 0xFFFFFFFFu, 0x0u};
  char seg[33];
  credencial::montarSegredo(aleatorio, seg);
  CONFERE(std::strcmp(seg, "00000001abcdef01ffffffff00000000") == 0 && credencial::segredoValido(seg));
  CONFERE(!credencial::segredoValido("") && !credencial::segredoValido("abc") && !credencial::segredoValido(nullptr));
  CONFERE(!credencial::segredoValido("0000000Gabcdef01ffffffff00000000") && !credencial::segredoValido("00000001abcdef01ffffffff000000001"));

  std::printf("teste_pareamento: %s (%d falha(s))\n", falhas ? "FALHOU" : "ok", falhas);
  return falhas ? 1 : 0;
}
