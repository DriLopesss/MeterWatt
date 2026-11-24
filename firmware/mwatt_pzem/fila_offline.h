/*
 * Sem Wi-Fi (ou sem o servidor), o medidor continua medindo e guarda um resumo por minuto na memória.
 * Quando a conexão volta, ele manda esses minutos ANTES de voltar às leituras ao vivo, e o servidor
 * os põe nos horários certos. Vão no mesmo tópico da telemetria, com "tipo":"atrasadas": assim as
 * permissões de cada medidor no broker não mudam.
 *
 * - Cada minuto: quantas leituras, potência média, mínima e máxima, e o contador de energia de cada
 *   fase no fim do minuto. A energia sai dos contadores do próprio PZEM, então não se perde nem se repete.
 * - O horário vai como o tempo ligado (uptime) do medidor; o servidor converte usando o uptime de agora.
 *   Assim não precisa de relógio na placa (nem de internet para acertar o relógio).
 * - Cabem MINUTOS minutos (12 horas). Numa queda maior, ficam os mais recentes, e o servidor fica
 *   sabendo quantos se perderam. Se a placa reiniciar, a memória se perde: o contador do PZEM
 *   continua certo, mas a energia do intervalo entra de uma vez quando a conexão voltar.
 * Não depende do Arduino (testado no computador em tests/firmware/teste_fila_offline.cpp).
 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

namespace fila {

const size_t MINUTOS = 720;          // 12 horas; cada minuto ocupa uns 36 bytes (~26 KB)
const size_t POR_MENSAGEM = 8;       // minutos por mensagem: cabe no buffer MQTT de 1024 bytes
const size_t FASES = 3;

struct Minuto {
  uint32_t chave;                    // uptime em minutos
  uint32_t uptime_s;                 // uptime da última leitura do minuto
  uint16_t n;
  float soma_w, min_w, max_w;
  uint32_t energia[FASES];
  uint8_t fases;                     // quantas fases o medidor tem
  uint8_t validas;                   // bit i = a fase i teve contador neste minuto
};

struct Fila {
  Minuto itens[MINUTOS];
  size_t inicio, tamanho;
  uint32_t perdidos;                 // minutos que não couberam desde a última vez que a fila esvaziou
};

inline void limpar(Fila* f) { f->inicio = 0; f->tamanho = 0; f->perdidos = 0; }
inline bool vazia(const Fila* f) { return f->tamanho == 0; }
inline Minuto* em(Fila* f, size_t i) { return &f->itens[(f->inicio + i) % MINUTOS]; }

// Guarda uma leitura. `energia[i]` só vale se `validas` tiver o bit i (fase lida).
inline void guardar(Fila* f, uint32_t uptime_s, float potencia_w, const uint32_t* energia, uint8_t validas, uint8_t fases) {
  uint32_t chave = uptime_s / 60;
  Minuto* m = f->tamanho ? em(f, f->tamanho - 1) : nullptr;
  if (!m || m->chave != chave) {
    if (f->tamanho == MINUTOS) {     // cheia: perde o minuto mais antigo
      f->inicio = (f->inicio + 1) % MINUTOS;
      f->tamanho--;
      f->perdidos++;
    }
    Minuto* anterior = m;
    m = em(f, f->tamanho);
    f->tamanho++;
    m->chave = chave;
    m->n = 0;
    m->soma_w = 0;
    m->min_w = potencia_w;
    m->max_w = potencia_w;
    m->fases = fases;
    m->validas = 0;
    for (size_t i = 0; i < FASES; i++) m->energia[i] = anterior ? anterior->energia[i] : 0;
  }
  m->uptime_s = uptime_s;
  if (m->n < 0xFFFF) m->n++;
  m->soma_w += potencia_w;
  if (potencia_w < m->min_w) m->min_w = potencia_w;
  if (potencia_w > m->max_w) m->max_w = potencia_w;
  for (size_t i = 0; i < fases && i < FASES; i++) {
    if (validas & (1u << i)) {
      m->energia[i] = energia[i];
      m->validas |= (uint8_t)(1u << i);
    }
  }
}

// Monta a mensagem com os primeiros minutos da fila (até POR_MENSAGEM). Devolve quantos minutos
// entraram (0 se não coube nada). Só tire da fila (`tirar`) depois que a mensagem foi enviada.
inline size_t montar(Fila* f, char* buf, size_t tam, uint32_t uptime_agora) {
  size_t quantos = f->tamanho < POR_MENSAGEM ? f->tamanho : POR_MENSAGEM;
  if (!quantos) return 0;
  int e = snprintf(buf, tam, "{\"tipo\":\"atrasadas\",\"uptime_agora\":%lu,\"perdidos\":%lu,\"minutos\":[", (unsigned long)uptime_agora,
                   (unsigned long)f->perdidos);
  if (e < 0 || (size_t)e >= tam) return 0;
  size_t pos = (size_t)e;
  for (size_t k = 0; k < quantos; k++) {
    Minuto* m = em(f, k);
    e = snprintf(buf + pos, tam - pos, "%s{\"u\":%lu,\"n\":%u,\"w\":%.1f,\"min\":%.1f,\"max\":%.1f,\"e\":[", k ? "," : "",
                 (unsigned long)m->uptime_s, (unsigned)m->n, m->n ? m->soma_w / m->n : 0.0f, m->min_w, m->max_w);
    if (e < 0 || pos + (size_t)e >= tam) return 0;
    pos += (size_t)e;
    for (size_t i = 0; i < m->fases && i < FASES; i++) {
      if (m->validas & (1u << i)) e = snprintf(buf + pos, tam - pos, "%s%lu", i ? "," : "", (unsigned long)m->energia[i]);
      else e = snprintf(buf + pos, tam - pos, "%snull", i ? "," : "");
      if (e < 0 || pos + (size_t)e >= tam) return 0;
      pos += (size_t)e;
    }
    e = snprintf(buf + pos, tam - pos, "]}");
    if (e < 0 || pos + (size_t)e >= tam) return 0;
    pos += (size_t)e;
  }
  e = snprintf(buf + pos, tam - pos, "]}");
  if (e < 0 || pos + (size_t)e >= tam) return 0;
  return quantos;
}

inline void tirar(Fila* f, size_t quantos) {
  if (quantos > f->tamanho) quantos = f->tamanho;
  f->inicio = (f->inicio + quantos) % MINUTOS;
  f->tamanho -= quantos;
  if (!f->tamanho) f->perdidos = 0;
}

}  // namespace fila
