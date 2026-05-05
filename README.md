# MeterWatt ⚡ 
**Projeto Finalista NEXT FIAP 2023**

O MeterWatt é um sistema de monitoramento de energia inteligente (IoT) desenvolvido para medir o consumo em tempo real, gerando dashboards interativos para análise de eficiência energética e controle de gastos.

## 📖 Visão e Propósito

O **MeterWatt** nasceu da necessidade de transformar a relação entre consumidores e o consumo elétrico, promovendo conscientização e eficiência operacional. A solução está fundamentada em quatro pilares estratégicos:

*   **🏠 Conscientização Residencial:** Permite que o usuário identifique com precisão quais dispositivos estão elevando a conta de luz, facilitando a gestão de uma *Smart Home* com controle total de gastos e consumo.
*   **🛡️ Segurança e Detecção de Falhas:** O monitoramento contínuo possibilita a identificação precoce de fugas de energia, falhas em fiações antigas ou possíveis curto-circuitos, atuando diretamente na prevenção de acidentes domésticos e industriais.
*   **📉 Combate a Perdas Não Técnicas (Gatos):** No mercado de baixa tensão, as perdas não técnicas (furtos e fraudes) atingiram **16,02%** em 2024, gerando um prejuízo nacional de **R$ 10,3 bilhões**, segundo dados da ANEEL. O MeterWatt atua na detecção dessas anomalias em tempo real, auxiliando empresas e concessionárias a identificar desvios que chegam a elevar as tarifas em até **13,4%** para o consumidor final.
*   **🏛️ Integração Governamental:** A proposta inclui uma camada de conectividade para que governos e concessionárias acessem dados precisos sobre a demanda e produção de energia, otimizando a distribuição e o planejamento energético em nível municipal e estadual.

## 📂 Estrutura do Repositório

Para facilitar a navegação, o projeto está dividido nas seguintes pastas:

*   [**📁 firmware**](./firmware/mwatt.ino)**: Contém o código fonte em C++ desenvolvido para o ESP32/Arduino, responsável pela leitura dos sensores e comunicação MQTT.
*   [**📁 src**](./src/dashboard.py): Contém os scripts em Python para o processamento de dados e geração do Dashboard interativo.
*   [**📁 docs**](./docs): Documentação técnica, esquemas do circuito e registros visuais do projeto funcionando.

## 🚀 Tecnologias Utilizadas
* **Hardware:** ESP32, Sensores de Corrente (SCT-013).
* **Linguagens:** C++ (Firmware) e Python (Data Visualization).
* **Protocolos:** MQTT para telemetria e HTTP para integração com APIs.

## 📋 Funcionalidades Principais
* Medição de corrente e cálculo de potência ativa em tempo real.
* Integração com Broker MQTT para monitoramento remoto.
* Dashboard interativo para visualização histórica de consumo.
* Sistema de alertas configuráveis para limite de gastos mensais.

## 🏆 Reconhecimento
Este projeto foi selecionado como **finalista no NEXT FIAP 2023**, sendo avaliado por sua viabilidade técnica e impacto em sustentabilidade.

![Draft](./docs/Draft.jpg)
---
**Desenvolvido por:** [Adriano Lopes Santana](https://www.linkedin.com/in/adriano-lopes-santana-352692293)
**Instituição:** FIAP - Engenharia de Software.
