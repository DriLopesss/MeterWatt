#!/usr/bin/env bash
# Roda o firmware do PZEM num ESP32 simulado, no computador. Na raiz do repositório:
#   bash tests/firmware/simulado/rodar.sh
set -euo pipefail
cd "$(dirname "$0")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
falhou=0
for permite in 0 1; do
  # config.h montado a partir do exemplo; o de verdade nunca é usado aqui.
  sed "s/#define PERMITIR_ZERAR_ENERGIA 0/#define PERMITIR_ZERAR_ENERGIA PERMITE/" \
    ../../../firmware/mwatt_pzem/config.example.h > "$tmp/config.h"
  for teste in teste_leitura teste_portal; do
    g++ -std=c++17 -Wall -Wextra -Wno-unused-parameter -Werror -DPERMITE=$permite \
      -I"$tmp" -I. -x c++ "$teste.cpp" -o "$tmp/$teste"
    if "$tmp/$teste" > "$tmp/saida.txt"; then
      echo "ok: $teste (PERMITIR_ZERAR_ENERGIA=$permite) - $(tail -1 "$tmp/saida.txt")"
    else
      echo "FALHOU: $teste (PERMITIR_ZERAR_ENERGIA=$permite)"; grep FALHOU "$tmp/saida.txt" || true; falhou=1
    fi
  done
done
# Casa bifásica: dois PZEM no mesmo par de fios.
sed "s/#define PZEM_ENDERECOS {0xF8}/#define PZEM_ENDERECOS {0x01, 0x02}/" \
  ../../../firmware/mwatt_pzem/config.example.h > "$tmp/config.h"
g++ -std=c++17 -Wall -Wextra -Wno-unused-parameter -Werror -DPERMITE=0 -I"$tmp" -I. -x c++ teste_fases.cpp -o "$tmp/teste_fases"
if "$tmp/teste_fases" > "$tmp/saida.txt"; then
  echo "ok: teste_fases (2 PZEM) - $(tail -1 "$tmp/saida.txt")"
else
  echo "FALHOU: teste_fases (2 PZEM)"; grep FALHOU "$tmp/saida.txt" || true; falhou=1
fi
exit $falhou
