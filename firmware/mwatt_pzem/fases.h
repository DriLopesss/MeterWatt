/*
 * Casa com mais de uma fase: um PZEM por fase, todos no mesmo par de fios da serial, cada um com
 * o seu endereço Modbus. Este arquivo junta as leituras e monta a mensagem para o servidor.
 * Não depende do Arduino (testado no computador em tests/firmware/teste_fases.cpp).
 *
 * Por que somar dá certo: numa ligação com neutro, N fios pedem N-1 medidores referidos a um deles
 * (teorema de Blondel). Bifásica (2 fases + neutro): 2 PZEM, cada um com a tensão da sua fase para o
 * neutro e a pinça no fio dessa fase. Trifásica: 3. A soma das potências ativas é o total da casa,
 * inclusive dos aparelhos de 220 V ligados entre duas fases.
 *
 * Limite: o PZEM não mostra o sinal da potência. Um aparelho entre duas fases com fator de potência
 * muito baixo (abaixo de 0,5) faria um dos dois medir "ao contrário", e a soma sairia maior. Em casa
 * isso é raro (motores ficam perto de 0,8), mas fica anotado.
 *
 * Uma fase sem resposta não cala as outras: a mensagem leva as fases lidas, o total delas e qual
 * faltou. Uma fase sem energia desliga o PZEM dela (ele se alimenta da própria fase), e esse é
 * justamente o caso que vale avisar.
 */
#pragma once
#include <stdio.h>
#include "pzem_modbus.h"

namespace fases {

const size_t MAXIMO = 3;

// Um endereço só pode ser o geral (0xF8) com um módulo. Com mais de um: todos de 0x01 a 0xF7 e diferentes.
inline bool enderecosValidos(const uint8_t* e, size_t n) {
  if (n < 1 || n > MAXIMO) return false;
  if (n == 1) return e[0] == pzem::ENDERECO_GERAL || pzem::enderecoValido(e[0]);
  for (size_t i = 0; i < n; i++) {
    if (!pzem::enderecoValido(e[i])) return false;
    for (size_t j = 0; j < i; j++) {
      if (e[i] == e[j]) return false;
    }
  }
  return true;
}

struct Total {
  float tensao_v;       // média das fases lidas (fase-neutro)
  float corrente_a;     // soma
  float potencia_w;     // soma: é o consumo da casa
  uint32_t energia_wh;  // soma dos contadores das fases lidas
  float frequencia_hz;  // da primeira fase lida
  float fp;             // uma fase: o do módulo; mais: potência ativa ÷ soma das aparentes
  bool alarme;
  size_t lidas;
};

// Soma as fases que responderam. Devolve false se nenhuma respondeu.
inline bool somar(const pzem::Medidas* m, const pzem::Erro* erros, size_t n, Total* t) {
  Total s = {0, 0, 0, 0, 0, 0, false, 0};
  float aparente = 0, fpUnico = 1.0f;
  for (size_t i = 0; i < n; i++) {
    if (erros[i] != pzem::SEM_ERRO) continue;
    if (s.lidas == 0) {
      s.frequencia_hz = m[i].frequencia_hz;
      fpUnico = m[i].fp;
    }
    s.lidas++;
    s.tensao_v += m[i].tensao_v;
    s.corrente_a += m[i].corrente_a;
    s.potencia_w += m[i].potencia_w;
    s.energia_wh += m[i].energia_wh;
    s.alarme = s.alarme || m[i].alarme;
    aparente += m[i].tensao_v * m[i].corrente_a;
  }
  if (s.lidas == 0) return false;
  s.tensao_v /= (float)s.lidas;
  // Uma fase: o fator de potência que o próprio módulo mediu. Mais de uma: ativa ÷ soma das aparentes.
  s.fp = s.lidas == 1 ? fpUnico : (aparente > 0 ? s.potencia_w / aparente : 1.0f);
  if (s.fp > 1.0f) s.fp = 1.0f;
  *t = s;
  return true;
}

// Acrescenta texto em buf a partir de *pos. Devolve false se não coube.
inline bool juntar(char* /*buf*/, size_t tam, size_t* pos, int escritos) {
  if (escritos < 0 || *pos + (size_t)escritos >= tam) return false;
  *pos += (size_t)escritos;
  return true;
}

// A mensagem de telemetria. Com um módulo, igual à das versões anteriores. Com mais de um, os totais
// no mesmo lugar (servidores antigos continuam entendendo) e a lista "fases".
// Devolve o tamanho escrito, ou 0 se não coube em `tam`.
inline size_t montarTelemetria(char* buf, size_t tam, const char* fw, const pzem::Medidas* m, const pzem::Erro* erros,
                               size_t n, unsigned long falhas, int rssi, unsigned long uptime_s) {
  size_t pos = 0;
  Total t;
  bool algum = somar(m, erros, n, &t);
  if (algum) {
    if (!juntar(buf, tam, &pos, snprintf(buf + pos, tam - pos,
        "{\"fw\":\"%s\",\"sensor\":\"pzem004t-v3\",\"sensor_ok\":true,"
        "\"tensao_v\":%.1f,\"corrente_a\":%.3f,\"potencia_w\":%.1f,\"energia_wh\":%lu,"
        "\"frequencia_hz\":%.1f,\"fp\":%.2f,\"alarme\":%s",
        fw, t.tensao_v, t.corrente_a, t.potencia_w, (unsigned long)t.energia_wh, t.frequencia_hz, t.fp,
        t.alarme ? "true" : "false"))) return 0;
  } else {
    // Sem leitura válida: avisa, mas NUNCA repete números antigos como se fossem novos.
    if (!juntar(buf, tam, &pos, snprintf(buf + pos, tam - pos,
        "{\"fw\":\"%s\",\"sensor\":\"pzem004t-v3\",\"sensor_ok\":false,\"erro_sensor\":\"%s\"",
        fw, pzem::nomeDoErro(erros[0])))) return 0;
  }
  if (n > 1) {
    if (!juntar(buf, tam, &pos, snprintf(buf + pos, tam - pos, ",\"fases\":["))) return 0;
    for (size_t i = 0; i < n; i++) {
      int e;
      if (erros[i] == pzem::SEM_ERRO) {
        e = snprintf(buf + pos, tam - pos,
                     "%s{\"fase\":%u,\"ok\":true,\"tensao_v\":%.1f,\"corrente_a\":%.3f,\"potencia_w\":%.1f,"
                     "\"energia_wh\":%lu,\"fp\":%.2f}",
                     i ? "," : "", (unsigned)(i + 1), m[i].tensao_v, m[i].corrente_a, m[i].potencia_w,
                     (unsigned long)m[i].energia_wh, m[i].fp);
      } else {
        e = snprintf(buf + pos, tam - pos, "%s{\"fase\":%u,\"ok\":false,\"erro\":\"%s\"}", i ? "," : "",
                     (unsigned)(i + 1), pzem::nomeDoErro(erros[i]));
      }
      if (!juntar(buf, tam, &pos, e)) return 0;
    }
    if (!juntar(buf, tam, &pos, snprintf(buf + pos, tam - pos, "]"))) return 0;
  }
  if (!juntar(buf, tam, &pos, snprintf(buf + pos, tam - pos, ",\"falhas_leitura\":%lu,\"rssi\":%d,\"uptime_s\":%lu}",
                                       falhas, rssi, uptime_s))) return 0;
  return pos;
}

}  // namespace fases
