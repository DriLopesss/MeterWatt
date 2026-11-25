/*
 * Pareamento do medidor com uma conta do aplicativo MeterWatt.
 *
 * 1. No aplicativo, a pessoa toca em "Adicionar medidor" e recebe um código (vale 15 min).
 * 2. Na página de configuração do medidor, digita o código.
 * 3. O medidor publica {"codigo":"XXXX-XXXX",...} em meterwatt/<id>/pareamento a cada 30 s,
 *    até o servidor responder "pareado" ou passarem 15 min.
 *
 * RESPOSTA SÓ PARA ELE (v3.6): a resposta vai para meterwatt-resposta/<16 letras sorteadas a cada pedido>,
 * que só este medidor conhece (ele manda junto com o código). Antes ia para meterwatt/<id>/comando, e a
 * conta de pareamento (cuja senha está em todo medidor) podia ler os comandos de todos. O medidor também
 * entra no servidor com um id de cliente que leva um sufixo sorteado (<id>-xxxxxxxx), para ninguém derrubar
 * a conexão dele entrando com o mesmo id.
 *
 * O código fica só na memória RAM: nunca é gravado. Não depende do Arduino, para ser
 * testado no computador (tests/firmware/teste_pareamento.cpp).
 *
 * SENHA PRÓPRIA (v3.3): junto com o código, o medidor manda um segredo que ele mesmo
 * sorteou. Se o servidor responder "pareado_credencial", esse segredo virou a senha deste
 * medidor no servidor de mensagens (usuário = id do medidor), e ele passa a conectar com
 * ela. Se a senha própria for recusada 3 vezes seguidas (o medidor foi removido da conta),
 * ele volta para a conta de pareamento da página de configuração, de onde só consegue
 * pedir um pareamento novo.
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
  char resposta[17];    // fim do tópico onde a resposta deste pedido chega (sorteado a cada pedido)
  uint32_t inicio;
  uint32_t ultimoEnvio;
  bool ativo;
  bool jaEnviou;
};

inline void limpar(Estado* e) {
  memset(e->codigo, 0, sizeof(e->codigo));
  memset(e->resposta, 0, sizeof(e->resposta));
  e->inicio = e->ultimoEnvio = 0;
  e->ativo = e->jaEnviou = false;
}

// 16 letras de 0-9a-f a partir de dois números aleatórios de 32 bits.
inline void montarResposta(const uint32_t aleatorio[2], char saida[17]) {
  snprintf(saida, 17, "%08lx%08lx", (unsigned long)aleatorio[0], (unsigned long)aleatorio[1]);
}

// `codigo` já normalizado (ajustes::normalizarCodigo); `resposta` de montarResposta.
inline void iniciar(Estado* e, const char* codigo, uint32_t agora, const char* resposta) {
  limpar(e);
  strncpy(e->codigo, codigo, sizeof(e->codigo) - 1);
  strncpy(e->resposta, resposta, sizeof(e->resposta) - 1);
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

inline int montarMensagem(const Estado& e, const char* segredo, const char* cliente, char* saida, size_t tamanho) {
  return snprintf(saida, tamanho, "{\"codigo\":\"%s\",\"segredo\":\"%s\",\"cliente\":\"%s\",\"resposta\":\"%s\"}",
                  e.codigo, segredo, cliente, e.resposta);
}

// O tópico onde chega a resposta deste pedido.
inline int montarTopicoResposta(const Estado& e, char* saida, size_t tamanho) {
  return snprintf(saida, tamanho, "meterwatt-resposta/%s", e.resposta);
}

enum Comando {
  OUTRO_COMANDO = 0,
  PAREADO,              // pareado; continua com a conta da página de configuração
  PAREADO_CREDENCIAL,   // pareado; o servidor cadastrou a senha própria deste medidor
  DESPAREADO            // o medidor foi removido da conta
};

inline bool igual(const unsigned char* dados, unsigned int tamanho, const char* texto) {
  return tamanho == strlen(texto) && memcmp(dados, texto, tamanho) == 0;
}

// Comando recebido do servidor. As confirmações só valem se havia um pedido em andamento.
inline Comando aoComando(Estado* e, const unsigned char* dados, unsigned int tamanho) {
  if (igual(dados, tamanho, "despareado")) return DESPAREADO;
  Comando c = igual(dados, tamanho, "pareado") ? PAREADO
            : igual(dados, tamanho, "pareado_credencial") ? PAREADO_CREDENCIAL : OUTRO_COMANDO;
  if (c == OUTRO_COMANDO) return c;
  bool estavaPareando = e->ativo;
  limpar(e);
  return estavaPareando ? c : OUTRO_COMANDO;
}

}  // namespace pareamento

namespace credencial {

const int MQTT_SENHA_ERRADA = 4;       // estados do PubSubClient quando o servidor recusa
const int MQTT_NAO_AUTORIZADO = 5;
const uint8_t RECUSAS_PARA_DESISTIR = 3;

struct Estado {
  bool propria;       // gravado na memória: o servidor confirmou a senha própria
  bool emProvisao;    // só na RAM: a senha própria foi recusada; usando a conta de pareamento
  uint8_t recusas;
};

inline void limpar(Estado* e) { e->propria = e->emProvisao = false; e->recusas = 0; }
inline bool usarPropria(const Estado& e) { return e.propria && !e.emProvisao; }
inline void aoConectar(Estado* e) { e->recusas = 0; }

// Devolve true quando acabou de desistir da senha própria (para avisar no monitor serial).
inline bool aoFalhar(Estado* e, int estadoMqtt) {
  if (!usarPropria(*e)) return false;
  if (estadoMqtt != MQTT_SENHA_ERRADA && estadoMqtt != MQTT_NAO_AUTORIZADO) return false;   // rede fora do ar não conta
  if (++e->recusas < RECUSAS_PARA_DESISTIR) return false;
  e->emProvisao = true;
  e->recusas = 0;
  return true;
}

inline void aoReceberCredencial(Estado* e) { e->propria = true; e->emProvisao = false; e->recusas = 0; }
inline void aoDesparear(Estado* e) { limpar(e); }

// 32 letras e números a partir de 4 números aleatórios de 32 bits (128 bits).
inline void montarSegredo(const uint32_t aleatorio[4], char saida[33]) {
  snprintf(saida, 33, "%08lx%08lx%08lx%08lx", (unsigned long)aleatorio[0], (unsigned long)aleatorio[1],
           (unsigned long)aleatorio[2], (unsigned long)aleatorio[3]);
}

// Sufixo do id de cliente MQTT: 8 letras de 0-9a-f, sorteadas uma vez e guardadas com o segredo.
inline void montarSufixo(uint32_t aleatorio, char saida[9]) { snprintf(saida, 9, "%08lx", (unsigned long)aleatorio); }

inline bool sufixoValido(const char* s) {
  if (!s) return false;
  size_t n = 0;
  for (; s[n]; n++) {
    if (!((s[n] >= '0' && s[n] <= '9') || (s[n] >= 'a' && s[n] <= 'f')) || n >= 8) return false;
  }
  return n == 8;
}

inline bool segredoValido(const char* s) {
  if (!s) return false;
  size_t n = 0;
  for (; s[n]; n++) {
    bool ok = (s[n] >= '0' && s[n] <= '9') || (s[n] >= 'a' && s[n] <= 'f');
    if (!ok || n >= 32) return false;
  }
  return n == 32;
}

}  // namespace credencial
