// Teste do protocolo do PZEM-004T v3 no computador (sem ESP32 e sem o módulo).
// Rodar na raiz do repositório:
//   g++ -std=c++11 -Wall -Wextra -o /tmp/teste_pzem tests/firmware/teste_pzem_modbus.cpp && /tmp/teste_pzem
#include <cmath>
#include <cstdio>
#include <cstring>

#include "../../firmware/mwatt_pzem/pzem_modbus.h"

static int falhas = 0;
#define CONFERE(cond)                                                    \
  do {                                                                   \
    if (!(cond)) {                                                       \
      std::printf("FALHOU linha %d: %s\n", __LINE__, #cond);             \
      falhas++;                                                          \
    }                                                                    \
  } while (0)

static bool perto(float a, float b, float tol = 0.0005f) { return std::fabs(a - b) <= tol; }

// Monta uma resposta como o módulo montaria, para os valores dados.
static void montarResposta(uint8_t endereco, uint16_t v, uint32_t i, uint32_t p, uint32_t e,
                           uint16_t f, uint16_t pf, uint16_t alarme, uint8_t r[25]) {
  r[0] = endereco; r[1] = 0x04; r[2] = 20;
  auto w = [&](int pos, uint16_t x) { r[pos] = x >> 8; r[pos + 1] = x & 0xFF; };
  w(3, v);
  w(5, i & 0xFFFF);  w(7, i >> 16);
  w(9, p & 0xFFFF);  w(11, p >> 16);
  w(13, e & 0xFFFF); w(15, e >> 16);
  w(17, f); w(19, pf); w(21, alarme);
  uint16_t crc = pzem::crc16(r, 23);
  r[23] = crc & 0xFF; r[24] = crc >> 8;
}

int main() {
  // 1. CRC: vetor clássico do Modbus (01 03 00 00 00 01 -> 84 0A)
  const uint8_t classico[] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x01};
  CONFERE(pzem::crc16(classico, 6) == 0x0A84);

  // 2. Pedido de leitura igual ao exemplo do manual do PZEM: 01 04 00 00 00 0A 70 0D
  uint8_t pedido[8];
  pzem::montarPedidoLeitura(0x01, pedido);
  const uint8_t esperado[] = {0x01, 0x04, 0x00, 0x00, 0x00, 0x0A, 0x70, 0x0D};
  CONFERE(std::memcmp(pedido, esperado, 8) == 0);

  // 3. Resposta normal: 127,3 V; 4,321 A; 548,2 W; 1.234.567 Wh; 60,0 Hz; FP 0,98
  uint8_t r[25];
  montarResposta(0x01, 1273, 4321, 5482, 1234567, 600, 98, 0, r);
  pzem::Medidas m;
  CONFERE(pzem::interpretar(r, 25, 0x01, &m) == pzem::SEM_ERRO);
  CONFERE(perto(m.tensao_v, 127.3f, 0.01f));
  CONFERE(perto(m.corrente_a, 4.321f));
  CONFERE(perto(m.potencia_w, 548.2f, 0.01f));
  CONFERE(m.energia_wh == 1234567u);  // usa a palavra alta (acima de 65.535 Wh)
  CONFERE(perto(m.frequencia_hz, 60.0f, 0.01f));
  CONFERE(perto(m.fp, 0.98f));
  CONFERE(!m.alarme);

  // 4. Corrente e potência grandes (palavra alta): 80,000 A e 10.160,0 W
  montarResposta(0x01, 1270, 80000, 101600, 0, 600, 100, 0xFFFF, r);
  CONFERE(pzem::interpretar(r, 25, 0x01, &m) == pzem::SEM_ERRO);
  CONFERE(perto(m.corrente_a, 80.0f, 0.001f));
  CONFERE(perto(m.potencia_w, 10160.0f, 0.01f));
  CONFERE(m.alarme);

  // 5. Endereço geral aceita a resposta de qualquer módulo
  CONFERE(pzem::interpretar(r, 25, pzem::ENDERECO_GERAL, &m) == pzem::SEM_ERRO);
  // ...mas um endereço específico recusa a resposta de outro
  CONFERE(pzem::interpretar(r, 25, 0x02, &m) == pzem::ENDERECO);

  // 6. Um byte trocado no caminho: o CRC precisa acusar, e `m` não pode mudar
  montarResposta(0x01, 1273, 4321, 5482, 10, 600, 98, 0, r);
  pzem::Medidas antes = m;
  r[4] ^= 0x01;
  CONFERE(pzem::interpretar(r, 25, 0x01, &m) == pzem::CRC_INVALIDO);
  CONFERE(std::memcmp(&antes, &m, sizeof(m)) == 0);

  // 7. Sem resposta e resposta cortada
  CONFERE(pzem::interpretar(r, 0, 0x01, &m) == pzem::SEM_RESPOSTA);
  CONFERE(pzem::interpretar(r, 12, 0x01, &m) == pzem::TAMANHO);

  // 8. Exceção do Modbus (01 84 02 + CRC)
  uint8_t ex[5] = {0x01, 0x84, 0x02, 0, 0};
  uint16_t c = pzem::crc16(ex, 3);
  ex[3] = c & 0xFF; ex[4] = c >> 8;
  CONFERE(pzem::interpretar(ex, 5, 0x01, &m) == pzem::EXCECAO_MODBUS);

  // 9. Valores impossíveis com CRC certo (por exemplo FP 1,50) são recusados
  montarResposta(0x01, 1273, 4321, 5482, 10, 600, 150, 0, r);
  CONFERE(pzem::interpretar(r, 25, 0x01, &m) == pzem::FORA_DA_FAIXA);

  // 10. Pedido para zerar a energia: 4 bytes com CRC válido
  uint8_t z[4];
  pzem::montarPedidoZerar(0x01, z);
  CONFERE(z[0] == 0x01 && z[1] == 0x42 && pzem::crcConfere(z, 4));

  if (falhas == 0) std::printf("OK: todos os testes do protocolo passaram\n");
  return falhas == 0 ? 0 : 1;
}
