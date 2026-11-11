/*
 * Protocolo do PZEM-004T v3.0 (Modbus RTU sobre serial TTL, 9600 8N1).
 *
 * Este arquivo não depende do Arduino: só monta os pedidos e interpreta as respostas.
 * Por isso dá para testá-lo no computador (veja tests/firmware/teste_pzem_modbus.cpp).
 *
 * Leitura das medidas: função 0x04, 10 registradores a partir do 0x0000.
 *   0x0000 tensão          0,1 V
 *   0x0001-2 corrente      0,001 A   (palavra baixa primeiro)
 *   0x0003-4 potência      0,1 W     (palavra baixa primeiro)
 *   0x0005-6 energia       1 Wh      (palavra baixa primeiro)
 *   0x0007 frequência      0,1 Hz
 *   0x0008 fator de pot.   0,01
 *   0x0009 alarme          0xFFFF = potência acima do limite configurado no módulo
 */
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace pzem {

// Endereço que qualquer módulo responde. Só serve com UM módulo na serial.
const uint8_t ENDERECO_GERAL = 0xF8;
const uint8_t FN_LER_MEDIDAS = 0x04;
const uint8_t FN_ZERAR_ENERGIA = 0x42;

const size_t TAM_PEDIDO_LEITURA = 8;
const size_t TAM_RESPOSTA_LEITURA = 25;
const size_t TAM_PEDIDO_ZERAR = 4;

struct Medidas {
  float tensao_v;
  float corrente_a;
  float potencia_w;
  uint32_t energia_wh;
  float frequencia_hz;
  float fp;
  bool alarme;
};

enum Erro {
  SEM_ERRO = 0,
  SEM_RESPOSTA,    // nenhum byte chegou
  TAMANHO,         // resposta incompleta
  CRC_INVALIDO,    // ruído na linha ou ligação ruim
  ENDERECO,        // respondeu outro módulo
  FUNCAO,          // resposta de outra função
  EXCECAO_MODBUS,  // o módulo recusou o pedido
  FORA_DA_FAIXA    // números que o módulo não consegue medir: desconfie da leitura
};

inline const char* nomeDoErro(Erro e) {
  switch (e) {
    case SEM_ERRO: return "ok";
    case SEM_RESPOSTA: return "sem_resposta";
    case TAMANHO: return "resposta_incompleta";
    case CRC_INVALIDO: return "crc_invalido";
    case ENDERECO: return "endereco_errado";
    case FUNCAO: return "funcao_errada";
    case EXCECAO_MODBUS: return "excecao_modbus";
    case FORA_DA_FAIXA: return "fora_da_faixa";
  }
  return "desconhecido";
}

// CRC-16 do Modbus (polinômio 0xA001, valor inicial 0xFFFF). Vai na linha com o byte baixo primeiro.
inline uint16_t crc16(const uint8_t* dados, size_t n) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    crc ^= dados[i];
    for (int b = 0; b < 8; b++) {
      crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0xA001) : (uint16_t)(crc >> 1);
    }
  }
  return crc;
}

inline void montarPedidoLeitura(uint8_t endereco, uint8_t saida[TAM_PEDIDO_LEITURA]) {
  saida[0] = endereco;
  saida[1] = FN_LER_MEDIDAS;
  saida[2] = 0x00;  // registrador inicial 0x0000
  saida[3] = 0x00;
  saida[4] = 0x00;  // 10 registradores
  saida[5] = 0x0A;
  uint16_t crc = crc16(saida, 6);
  saida[6] = (uint8_t)(crc & 0xFF);
  saida[7] = (uint8_t)(crc >> 8);
}

// Zera o contador de energia (kWh) guardado no módulo. O módulo responde com os mesmos 4 bytes.
inline void montarPedidoZerar(uint8_t endereco, uint8_t saida[TAM_PEDIDO_ZERAR]) {
  saida[0] = endereco;
  saida[1] = FN_ZERAR_ENERGIA;
  uint16_t crc = crc16(saida, 2);
  saida[2] = (uint8_t)(crc & 0xFF);
  saida[3] = (uint8_t)(crc >> 8);
}

inline bool crcConfere(const uint8_t* r, size_t n) {
  if (n < 3) return false;
  uint16_t recebido = (uint16_t)(r[n - 2] | (r[n - 1] << 8));
  return crc16(r, n - 2) == recebido;
}

inline uint16_t palavra(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
inline uint32_t dupla(const uint8_t* p) { return (uint32_t)palavra(p) | ((uint32_t)palavra(p + 2) << 16); }

// Interpreta a resposta do pedido de leitura. Só preenche `m` quando devolve SEM_ERRO.
inline Erro interpretar(const uint8_t* r, size_t n, uint8_t enderecoPedido, Medidas* m) {
  if (n == 0) return SEM_RESPOSTA;
  // Resposta de exceção: endereço, função | 0x80, código, CRC (5 bytes)
  if (n == 5 && (r[1] & 0x80) && crcConfere(r, n)) return EXCECAO_MODBUS;
  if (n != TAM_RESPOSTA_LEITURA) return TAMANHO;
  if (!crcConfere(r, n)) return CRC_INVALIDO;
  if (enderecoPedido != ENDERECO_GERAL && r[0] != enderecoPedido) return ENDERECO;
  if (r[1] != FN_LER_MEDIDAS || r[2] != 20) return FUNCAO;

  Medidas lida;
  lida.tensao_v = palavra(r + 3) / 10.0f;
  lida.corrente_a = dupla(r + 5) / 1000.0f;
  lida.potencia_w = dupla(r + 9) / 10.0f;
  lida.energia_wh = dupla(r + 13);
  lida.frequencia_hz = palavra(r + 17) / 10.0f;
  lida.fp = palavra(r + 19) / 100.0f;
  lida.alarme = palavra(r + 21) == 0xFFFF;

  // Limites do próprio módulo (80 a 260 V, até 100 A, 45 a 65 Hz), com folga.
  if (lida.tensao_v > 300.0f || lida.corrente_a > 120.0f || lida.fp > 1.0f ||
      lida.frequencia_hz < 40.0f || lida.frequencia_hz > 70.0f) {
    return FORA_DA_FAIXA;
  }
  *m = lida;
  return SEM_ERRO;
}

}  // namespace pzem
