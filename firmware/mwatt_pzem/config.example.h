// Copie este arquivo para config.h (na mesma pasta) e preencha.
// O config.h está no .gitignore: ele NUNCA deve ir para o GitHub.
#pragma once

// --- Rede ---
// 1 = o Wi-Fi e o servidor MQTT são configurados por uma página no celular (recomendado).
//     Os valores de MQTT abaixo viram só o ponto de partida da página.
// 0 = usa WIFI_SSID e WIFI_SENHA daqui, como nas versões anteriores.
#define USAR_PORTAL_WIFI 1

// Senha da rede "MeterWatt-XXXX" que o ESP32 cria para a configuração (8 a 63 caracteres).
// Troque antes de gravar: quem souber esta senha consegue mudar os ajustes do aparelho.
#define SENHA_PORTAL  "troque-esta-senha"

// Só usados com USAR_PORTAL_WIFI 0.
#define WIFI_SSID     "nome-da-rede"
#define WIFI_SENHA    "senha-da-rede"

// --- MQTT (broker com usuário e senha; veja o README) ---
#define MQTT_HOST     "192.168.0.10"   // IP do computador que roda o broker
#define MQTT_PORTA    1883
#define MQTT_USUARIO  "meterwatt"
#define MQTT_SENHA    "troque-esta-senha"

// --- Atualização do firmware pelo Wi-Fi ---
// Vazio = desligada até alguém definir uma senha na página. Se preencher, use de 8 a 32
// caracteres sem espaços. Sem essa senha, ninguém na rede consegue gravar outro firmware.
#define OTA_SENHA     ""

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

// A cada quanto tempo lê o módulo e envia. O PZEM atualiza as medidas a cada ~1 s,
// então 1000 é o mínimo. Use 1000 nos testes de calibração e para estudar a
// "assinatura" dos aparelhos; 5000 gasta menos rede e menos espaço no CSV.
#define INTERVALO_ENVIO_MS  5000
