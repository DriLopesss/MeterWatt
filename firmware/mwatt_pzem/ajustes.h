/*
 * Ajustes do MeterWatt que a pessoa preenche na página de configuração (servidor MQTT,
 * usuário, senhas). Este arquivo não depende do Arduino: só valida e copia textos.
 * Por isso dá para testá-lo no computador (veja tests/firmware/teste_ajustes.cpp).
 *
 * Regra principal: um formulário com qualquer campo inválido não muda NADA. Assim,
 * um erro de digitação não deixa o aparelho com metade dos ajustes novos.
 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace ajustes {

const size_t TAM_HOST = 64;        // inclui o '\0'
const size_t TAM_USUARIO = 33;
const size_t TAM_SENHA = 65;
const size_t TAM_SENHA_OTA = 33;
const size_t MIN_SENHA_OTA = 8;

struct Ajustes {
  char host[TAM_HOST];
  uint16_t porta;
  char usuario[TAM_USUARIO];
  char senha[TAM_SENHA];
  char senhaOta[TAM_SENHA_OTA];   // vazia = atualização pelo Wi-Fi desligada
};

enum Resultado {
  AJUSTES_OK = 0,
  HOST_INVALIDO,
  PORTA_INVALIDA,
  USUARIO_INVALIDO,
  SENHA_INVALIDA,
  SENHA_OTA_INVALIDA
};

inline const char* mensagem(Resultado r) {
  switch (r) {
    case AJUSTES_OK: return "ajustes salvos";
    case HOST_INVALIDO: return "endereco do servidor invalido (use so letras, numeros, ponto e traco)";
    case PORTA_INVALIDA: return "porta invalida (use um numero de 1 a 65535)";
    case USUARIO_INVALIDO: return "usuario invalido (de 1 a 32 caracteres, sem espacos nem acentos)";
    case SENHA_INVALIDA: return "senha do MQTT invalida (ate 64 caracteres, sem espacos nem acentos)";
    case SENHA_OTA_INVALIDA: return "senha de atualizacao invalida (de 8 a 32 caracteres, sem espacos nem acentos)";
  }
  return "erro desconhecido";
}

// Copia com limite e sempre termina com '\0'.
inline void copiar(char* destino, const char* origem, size_t tamanho) {
  if (tamanho == 0) return;
  size_t i = 0;
  if (origem) {
    for (; i + 1 < tamanho && origem[i]; i++) destino[i] = origem[i];
  }
  destino[i] = '\0';
}

// Só caracteres visíveis do ASCII (sem espaço, sem acento) e tamanho entre min e max.
inline bool textoSimples(const char* t, size_t min, size_t max) {
  if (!t) return min == 0;
  size_t n = 0;
  for (; t[n]; n++) {
    if (n >= max) return false;
    unsigned char c = (unsigned char)t[n];
    if (c < 0x21 || c > 0x7E) return false;
  }
  return n >= min;
}

// Nome ou IP do servidor: letras, números, ponto e traço; não começa nem termina com ponto ou traço.
inline bool hostValido(const char* h) {
  if (!h || !h[0]) return false;
  size_t n = 0;
  for (; h[n]; n++) {
    if (n >= TAM_HOST - 1) return false;
    char c = h[n];
    bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '-';
    if (!ok) return false;
    if ((c == '.' || c == '-') && (n == 0 || h[n - 1] == '.')) return false;
  }
  char ultimo = h[n - 1];
  return ultimo != '.' && ultimo != '-';
}

inline bool portaDeTexto(const char* t, uint16_t* porta) {
  if (!t || !t[0]) return false;
  unsigned long v = 0;
  for (size_t i = 0; t[i]; i++) {
    if (t[i] < '0' || t[i] > '9' || i >= 5) return false;
    v = v * 10 + (unsigned long)(t[i] - '0');
  }
  if (v < 1 || v > 65535) return false;
  *porta = (uint16_t)v;
  return true;
}

/*
 * Aplica o formulário. Senhas em branco mantêm a senha atual (a página nunca mostra
 * senhas salvas). Para desligar a atualização pelo Wi-Fi, use desligarOta = true.
 * Se qualquer campo estiver errado, `atual` fica exatamente como estava.
 */
inline Resultado aplicarFormulario(Ajustes* atual, const char* host, const char* porta,
                                   const char* usuario, const char* senha, const char* senhaOta,
                                   bool desligarOta = false) {
  Ajustes novo = *atual;
  if (!hostValido(host)) return HOST_INVALIDO;
  if (!portaDeTexto(porta, &novo.porta)) return PORTA_INVALIDA;
  if (!textoSimples(usuario, 1, TAM_USUARIO - 1)) return USUARIO_INVALIDO;
  if (senha && senha[0] && !textoSimples(senha, 1, TAM_SENHA - 1)) return SENHA_INVALIDA;
  if (senhaOta && senhaOta[0] && !textoSimples(senhaOta, MIN_SENHA_OTA, TAM_SENHA_OTA - 1)) return SENHA_OTA_INVALIDA;

  copiar(novo.host, host, TAM_HOST);
  copiar(novo.usuario, usuario, TAM_USUARIO);
  if (senha && senha[0]) copiar(novo.senha, senha, TAM_SENHA);
  if (desligarOta) novo.senhaOta[0] = '\0';
  else if (senhaOta && senhaOta[0]) copiar(novo.senhaOta, senhaOta, TAM_SENHA_OTA);
  *atual = novo;
  return AJUSTES_OK;
}

inline bool otaLigada(const Ajustes& a) { return textoSimples(a.senhaOta, MIN_SENHA_OTA, TAM_SENHA_OTA - 1); }

}  // namespace ajustes
