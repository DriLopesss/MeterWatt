// Teste da validação dos ajustes da página de configuração (sem ESP32).
// Rodar na raiz do repositório:
//   g++ -std=c++11 -Wall -Wextra -o /tmp/teste_ajustes tests/firmware/teste_ajustes.cpp && /tmp/teste_ajustes
#include <cstdio>
#include <cstring>

#include "../../firmware/mwatt_pzem/ajustes.h"

static int falhas = 0;
#define CONFERE(cond)                                                    \
  do {                                                                   \
    if (!(cond)) {                                                       \
      std::printf("FALHOU linha %d: %s\n", __LINE__, #cond);             \
      falhas++;                                                          \
    }                                                                    \
  } while (0)

using namespace ajustes;

static Ajustes base() {
  Ajustes a;
  copiar(a.host, "192.168.0.10", TAM_HOST);
  a.porta = 1883;
  copiar(a.usuario, "meterwatt", TAM_USUARIO);
  copiar(a.senha, "senha-antiga", TAM_SENHA);
  copiar(a.senhaOta, "", TAM_SENHA_OTA);
  return a;
}

static bool igual(const Ajustes& x, const Ajustes& y) {
  return !strcmp(x.host, y.host) && x.porta == y.porta && !strcmp(x.usuario, y.usuario) &&
         !strcmp(x.senha, y.senha) && !strcmp(x.senhaOta, y.senhaOta);
}

int main() {
  // 1. Endereços do servidor
  CONFERE(hostValido("192.168.0.10"));
  CONFERE(hostValido("broker.meterwatt.com.br"));
  CONFERE(hostValido("meu-pc"));
  CONFERE(!hostValido(""));
  CONFERE(!hostValido("meu pc"));
  CONFERE(!hostValido("-inicio.com"));
  CONFERE(!hostValido("fim.com."));
  CONFERE(!hostValido("dois..pontos"));
  CONFERE(!hostValido("acentuação.com"));
  CONFERE(!hostValido("<script>"));
  char longo[80]; std::memset(longo, 'a', 70); longo[70] = '\0';
  CONFERE(!hostValido(longo));

  // 2. Porta
  uint16_t p = 0;
  CONFERE(portaDeTexto("1883", &p) && p == 1883);
  CONFERE(portaDeTexto("65535", &p) && p == 65535);
  CONFERE(!portaDeTexto("0", &p));
  CONFERE(!portaDeTexto("65536", &p));
  CONFERE(!portaDeTexto("123456", &p));
  CONFERE(!portaDeTexto("18 83", &p));
  CONFERE(!portaDeTexto("", &p));
  CONFERE(!portaDeTexto("-1", &p));

  // 3. Formulário válido; senha em branco mantém a antiga
  Ajustes a = base();
  CONFERE(aplicarFormulario(&a, "broker.local", "8883", "casa1", "", "") == AJUSTES_OK);
  CONFERE(!strcmp(a.host, "broker.local") && a.porta == 8883 && !strcmp(a.usuario, "casa1"));
  CONFERE(!strcmp(a.senha, "senha-antiga"));
  CONFERE(!otaLigada(a));

  // 4. Senha nova e senha de atualização
  CONFERE(aplicarFormulario(&a, "broker.local", "8883", "casa1", "nova!Senha#1", "atualiza123") == AJUSTES_OK);
  CONFERE(!strcmp(a.senha, "nova!Senha#1") && otaLigada(a));
  // deixar a senha de atualização em branco mantém a atual
  CONFERE(aplicarFormulario(&a, "broker.local", "8883", "casa1", "", "") == AJUSTES_OK && otaLigada(a));
  // desligar de propósito
  CONFERE(aplicarFormulario(&a, "broker.local", "8883", "casa1", "", "", true) == AJUSTES_OK && !otaLigada(a));

  // 5. Qualquer campo errado não muda nada
  Ajustes antes = base();
  Ajustes b = antes;
  CONFERE(aplicarFormulario(&b, "servidor novo", "1883", "x", "y", "") == HOST_INVALIDO && igual(b, antes));
  CONFERE(aplicarFormulario(&b, "novo.local", "abc", "x", "y", "") == PORTA_INVALIDA && igual(b, antes));
  CONFERE(aplicarFormulario(&b, "novo.local", "1883", "", "y", "") == USUARIO_INVALIDO && igual(b, antes));
  CONFERE(aplicarFormulario(&b, "novo.local", "1883", "joão", "y", "") == USUARIO_INVALIDO && igual(b, antes));
  CONFERE(aplicarFormulario(&b, "novo.local", "1883", "x", "com espaco", "") == SENHA_INVALIDA && igual(b, antes));
  CONFERE(aplicarFormulario(&b, "novo.local", "1883", "x", "y", "curta") == SENHA_OTA_INVALIDA && igual(b, antes));
  char senhaGrande[100]; std::memset(senhaGrande, 'k', 90); senhaGrande[90] = '\0';
  CONFERE(aplicarFormulario(&b, "novo.local", "1883", "x", senhaGrande, "") == SENHA_INVALIDA && igual(b, antes));
  CONFERE(aplicarFormulario(&b, "novo.local", "1883", "x", "y", senhaGrande) == SENHA_OTA_INVALIDA && igual(b, antes));

  // 6. Cópia nunca passa do limite
  char pequeno[5];
  copiar(pequeno, "abcdefgh", sizeof(pequeno));
  CONFERE(!strcmp(pequeno, "abcd"));
  copiar(pequeno, nullptr, sizeof(pequeno));
  CONFERE(pequeno[0] == '\0');

  // 7. Toda mensagem existe
  for (int r = AJUSTES_OK; r <= SENHA_OTA_INVALIDA; r++) CONFERE(std::strlen(mensagem((Resultado)r)) > 5);

  if (falhas == 0) std::printf("OK: todos os testes dos ajustes passaram\n");
  return falhas == 0 ? 0 : 1;
}
