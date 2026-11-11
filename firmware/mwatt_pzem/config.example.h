// Copie este arquivo para config.h (na mesma pasta) e preencha.
// O config.h está no .gitignore: ele NUNCA deve ir para o GitHub.
#pragma once

// --- Rede ---
#define WIFI_SSID     "nome-da-rede"
#define WIFI_SENHA    "senha-da-rede"

// --- MQTT (broker com usuário e senha; veja o README) ---
#define MQTT_HOST     "192.168.0.10"   // IP do computador que roda o broker
#define MQTT_PORTA    1883
#define MQTT_USUARIO  "meterwatt"
#define MQTT_SENHA    "troque-esta-senha"

// --- Serial do PZEM-004T v3.0 (Serial2 do ESP32) ---
// GPIO16 recebe (liga no TX do PZEM) e GPIO17 envia (liga no RX do PZEM).
// O sinal precisa chegar ao GPIO16 em 3,3 V. Veja "Ligação do PZEM" no README.
#define PINO_RX_PZEM  16
#define PINO_TX_PZEM  17

// Endereço Modbus. 0xF8 é o endereço geral: funciona com UM módulo na serial.
// Só troque se for ligar mais de um PZEM no mesmo par de fios (cada um com seu endereço).
#define PZEM_ENDERECO 0xF8

// Tempo máximo de espera pela resposta do módulo.
#define PZEM_TIMEOUT_MS 300

// 1 = aceita o comando MQTT "zerar_energia", que apaga o contador de kWh do módulo.
// Deixe em 0 fora dos testes: zerar o contador apaga o histórico guardado no PZEM.
#define PERMITIR_ZERAR_ENERGIA 0

// A cada quanto tempo lê o módulo e envia (o PZEM atualiza as medidas a cada ~1 s).
#define INTERVALO_ENVIO_MS  5000
