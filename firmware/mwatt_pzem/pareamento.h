/*
 * Pareamento do medidor com uma conta do aplicativo MeterWatt.
 *
 * 1. No aplicativo, a pessoa toca em "Adicionar medidor" e recebe um código (vale 15 min).
 * 2. Na página de configuração do medidor, digita o código.
 * 3. O medidor publica {"codigo":"XXXX-XXXX"} em meterwatt/<id>/pareamento a cada 30 s,
 *    até o servidor responder "pareado" em meterwatt/<id>/comando ou passarem 15 min.
 *
 * O código fica só na memória RAM: nunca é gravado. Não depende do Arduino, para ser
 * testado no computador (tests/firmware/teste_pareamento.cpp).
 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace pareamento {

const uint32_t VALIDADE_MS = 15UL * 60UL * 1000UL;
const uint32_t REPETIR_MS = 30UL * 1000UL;

struct Estado {
  char codigo[10];
  uint32_t inicio;
  uint32_t ultimoEnvio;
  bool ativo;
  bool jaEnviou;
};

inline void limpar(Estado* e) {
  memset(e->codigo, 0, sizeof(e->codigo));
  e->inicio = e->ultimoEnvio = 0;
  e->ativo = e->jaEnviou = false;
}

// `codigo` já normalizado (ajustes::normalizarCodigo).
inline void iniciar(Estado* e, const char* codigo, uint32_t agora) {
  limpar(e);
  strncpy(e->codigo, codigo, sizeof(e->codigo) - 1);
  e->inicio = agora;
  e->ativo = true;
}

// Diz se é hora de publicar o pedido. Expira sozinho depois de 15 minutos.
inline bool deveEnviar(Estado* e, uint32_t agora, bool conectado) {
  if (!e->ativo) return false;
  if (agora - e->inicio > VALIDADE_MS) {
    limpar(e);
    return false;
  }
  if (!conectado) return false;
  return !e->jaEnviou || agora - e->ultimoEnvio >= REPETIR_MS;
}

inline void marcarEnviado(Estado* e, uint32_t agora) {
  e->ultimoEnvio = agora;
  e->jaEnviou = true;
}

inline int montarMensagem(const Estado& e, char* saida, size_t tamanho) {
  return snprintf(saida, tamanho, "{\"codigo\":\"%s\"}", e.codigo);
}

// Comando recebido do servidor. Devolve true se era a confirmação do pareamento.
inline bool aoComando(Estado* e, const unsigned char* dados, unsigned int tamanho) {
  static const char* PAREADO = "pareado";
  if (tamanho != strlen(PAREADO) || memcmp(dados, PAREADO, tamanho) != 0) return false;
  bool estavaPareando = e->ativo;
  limpar(e);
  return estavaPareando;
}

}  // namespace pareamento
