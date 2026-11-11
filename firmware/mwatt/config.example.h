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

// --- Sensor: ACS712 versão 30 A ---
// Use só pinos do ADC1 (GPIO 32 a 39). O ADC2 não funciona com o Wi-Fi ligado.
#define PINO_SENSOR   34

// Sensibilidade do ACS712 30 A: 66 mV por ampere (datasheet, alimentado em 5 V).
// Outras versões: 5 A -> 185 | 20 A -> 100
#define SENSIBILIDADE_MV_POR_A 66.0

// Divisor de tensão entre a saída do ACS712 e o GPIO34: FATOR = (R1 + R2) / R2.
// Exemplo: R1 = 10 kΩ (do sensor ao pino) e R2 = 20 kΩ (do pino ao GND) -> 1.5
// Assim 2,5 V vira cerca de 1,67 V e 4,5 V vira cerca de 3,0 V no pino.
// Só use 1.0 se o sinal já chega ao pino dentro de 0 a 3,3 V.
#define FATOR_DIVISOR      1.5

// Ajuste fino obtido na calibração contra um medidor de referência (comece com 1.0).
// Novo ajuste = ajuste atual × (corrente do medidor de referência / corrente do MeterWatt)
#define AJUSTE_CALIBRACAO  1.0

// Correntes abaixo deste valor viram zero (ruído do ADC). Ajuste olhando a leitura sem carga.
#define LIMIAR_RUIDO_A     0.15

// Tensão nominal usada no cálculo. Valor FIXO: a potência resultante é aparente (VA).
#define TENSAO_NOMINAL_V   127.0

// Tempos
#define JANELA_MS           500    // duração de cada medição (30 ciclos de 60 Hz)
#define INTERVALO_ENVIO_MS  5000   // a cada quanto tempo mede e envia
