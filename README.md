# MeterWatt ⚡ 
**Projeto Finalista NEXT FIAP 2023**

O MeterWatt é um sistema de monitoramento de energia inteligente (IoT) desenvolvido para medir o consumo em tempo real, gerando dashboards interativos para análise de eficiência energética e controle de gastos.

## 📖 Visão e Propósito

O **MeterWatt** nasceu da necessidade de transformar a relação entre consumidores e o consumo elétrico, promovendo conscientização e eficiência operacional. A solução está fundamentada em quatro pilares estratégicos:

*   **🏠 Conscientização Residencial:** Permite que o usuário identifique com precisão quais dispositivos estão elevando a conta de luz, facilitando a gestão de uma *Smart Home* com controle total de gastos e consumo.
*   **🛡️ Segurança e Detecção de Falhas:** O monitoramento contínuo possibilita a identificação precoce de fugas de energia, falhas em fiações antigas ou possíveis curto-circuitos, atuando diretamente na prevenção de acidentes domésticos e industriais.
*   **📉 Combate a Perdas Não Técnicas (Gatos):** No mercado de baixa tensão, as perdas não técnicas (furtos e fraudes) atingiram **16,02%** em 2024, gerando um prejuízo nacional de **R$ 10,3 bilhões**, segundo dados da ANEEL. O MeterWatt atua na detecção dessas anomalias em tempo real, auxiliando empresas e concessionárias a identificar desvios que chegam a elevar as tarifas em até **13,4%** para o consumidor final.
*   **🏛️ Integração Governamental:** A proposta inclui uma camada de conectividade para que governos e concessionárias acessem dados precisos sobre a demanda e produção de energia, otimizando a distribuição e o planejamento energético em nível municipal e estadual.

> **Status:** protótipo em testes. Os itens da visão acima (detecção de fraudes, integração com concessionárias e governo) ainda **não estão implementados**.

## 📂 Estrutura do Repositório

*   [**📁 firmware/mwatt**](./firmware/mwatt): firmware em C++ para o ESP32. Lê o sensor de corrente e publica as leituras por MQTT.
*   [**📁 src**](./src): backend em Python. Recebe as leituras por MQTT, grava um CSV para testes e expõe uma API HTTP para o painel.
*   [**📁 docs**](./docs): documentação e imagens do projeto.

## 🚀 Tecnologias Utilizadas
* **Hardware:** NodeMCU-ESP32 DevKit V1 e sensor de corrente ACS712 (versão 30 A).
* **Linguagens:** C++ (firmware) e Python (backend).
* **Protocolos:** MQTT com usuário e senha (telemetria) e HTTP (API para o painel).

## 📋 O que funciona hoje
* Medição de corrente RMS (A) e cálculo de **potência aparente (VA)** com tensão nominal fixa.
* Envio das leituras por MQTT, em JSON, a cada 5 segundos.
* Backend que acumula o consumo (kVAh), estima o custo em reais e grava cada leitura em CSV.

**Limitação importante:** como a tensão da rede não é medida, o sistema **não mede potência ativa (W) nem kWh reais**. Os valores são estimativas em VA, e o custo em reais também é estimado.

## 🧪 Como testar

### 1. Broker MQTT com senha (Mosquitto)
Instale o [Mosquitto](https://mosquitto.org/download/) no computador que vai receber os dados e crie um usuário:

```bash
mosquitto_passwd -c senhas.txt meterwatt
```

Crie um arquivo `mosquitto.conf`:

```
listener 1883
allow_anonymous false
password_file senhas.txt
```

E rode: `mosquitto -c mosquitto.conf -v`

### 2. Firmware
1. Copie `firmware/mwatt/config.example.h` para `firmware/mwatt/config.h`. O `config.h` está no `.gitignore` e **nunca deve ir para o GitHub**.
2. Preencha o Wi-Fi, o IP do computador com o broker e o usuário e senha do MQTT.
3. Confira o `FATOR_DIVISOR`: o ACS712 alimentado em 5 V pode passar de 3,3 V na saída, e o ESP32 não aceita isso no pino.
4. Na Arduino IDE, instale a biblioteca **PubSubClient**, selecione a placa **ESP32 Dev Module** e grave.
5. Abra o Monitor Serial em 115200. Cada leitura aparece em JSON.

### 3. Backend
```bash
cd src
pip install -r requirements.txt
MQTT_USUARIO=meterwatt MQTT_SENHA=sua-senha TARIFA_KWH=0.95 python dashboard.py
```
No Windows (PowerShell), defina as variáveis com `$env:MQTT_USUARIO="meterwatt"` antes de rodar.
Use em `TARIFA_KWH` o valor por kWh da sua conta de luz. O 0.95 é só exemplo.

* `GET http://localhost:5000/get`: última leitura, consumo acumulado e custo estimado.
* `GET http://localhost:5000/dispositivos`: todos os medidores conectados.
* Cada leitura é gravada em `leituras.csv`.

### 4. Calibração
1. **Ruído:** sem nenhuma carga, anote o maior `irms_a` que aparece. Coloque em `LIMIAR_RUIDO_A` um valor um pouco acima dele.
2. **Ganho:** ligue uma carga resistiva conhecida (por exemplo uma chaleira) e meça a corrente com um alicate amperímetro True RMS.
3. Calcule `AJUSTE_CALIBRACAO = corrente do alicate / irms_a do MeterWatt` e grave de novo.
4. Repita com cargas diferentes e registre o erro de cada uma.

## 🏆 Reconhecimento
Este projeto foi selecionado como **finalista no NEXT FIAP 2023**, sendo avaliado por sua viabilidade técnica e impacto em sustentabilidade.

![Draft](./docs/Draft.jpg)
---
**Desenvolvido por:** [Adriano Lopes Santana](https://www.linkedin.com/in/adriano-lopes-santana-352692293)
**Instituição:** FIAP - Engenharia de Software.
