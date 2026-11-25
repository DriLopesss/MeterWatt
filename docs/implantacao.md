# Como colocar o MeterWatt na internet

Um servidor só, com três peças: o site (`app`), o servidor de mensagens dos medidores (`mosquitto`) e o HTTPS automático (`caddy`). Tudo sobe com um comando.

> **Estado deste guia:** as peças foram testadas uma a uma (veja o fim da página), mas o conjunto com Docker **ainda não rodou num servidor de verdade**. O primeiro teste é de vocês, de preferência com um domínio de teste e sem clientes.

## O que precisa ter
- Um servidor Linux pequeno (1 vCPU, 1 GB de memória já serve para o piloto), com IP fixo. [ADVOGADO: se ficar fora do Brasil, a política de privacidade precisa dizer onde.]
- Um domínio (ex.: `painel.meterwatt.com.br`) com um registro **A** apontando para o IP do servidor.
- Uma conta de envio de e-mail com SMTP (o provedor do domínio costuma ter; serviços como Brevo, Resend e Amazon SES têm faixa gratuita).
- Portas abertas no firewall do servidor: **80** e **443** (site) e **8883** (medidores). A 1883 fica fechada.
- Saída para a internet na porta 443 (as notificações vão para os serviços do Google, da Apple, da Mozilla e da Microsoft; a lista está em [`app-celular.md`](app-celular.md)).

## Passo a passo
1. Instale o Docker no servidor (docs.docker.com/engine/install) e baixe o código:
   ```bash
   git clone https://github.com/DriLopesss/MeterWatt.git && cd MeterWatt/infra
   cp .env.exemplo .env
   ```
2. Gere a chave e cole no `.env`, em `MW_CHAVE_CRIPTO`:
   ```bash
   docker compose run --rm --no-deps app python -m meterwatt --gerar-chave
   ```
   **Guarde uma cópia dessa chave fora do servidor** (gerenciador de senhas). Sem ela, os backups não abrem e a verificação em duas etapas de todo mundo para de funcionar.
3. Preencha o resto do `.env`: `DOMINIO`, as senhas do `MQTT_*` e do `MW_MQTT_PROVISAO_*` (invente senhas longas e diferentes), o SMTP e os dados do controlador.
   Para as notificações no celular, gere também a `MW_VAPID_CHAVE_PRIVADA` e guarde junto da outra chave (detalhes em [`app-celular.md`](app-celular.md)):
   ```bash
   docker compose run --rm --no-deps app python -m meterwatt --gerar-vapid
   ```
4. Suba tudo:
   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```
   O Caddy pede o certificado HTTPS sozinho. O Mosquitto espera esse certificado aparecer e só então inicia (pode levar um minuto).
5. Abra `https://SEU-DOMINIO`. Crie a sua conta e ative a verificação em duas etapas.
6. Em cada medidor (firmware 3.6 ou mais novo), na página de configuração: servidor = o domínio, **porta = 8883**, usuário e senha = os de `MW_MQTT_PROVISAO_*`. Depois, o código de pareamento. A partir daí o medidor passa a usar a senha só dele.

## Manutenção
- **Atualizar:** `git pull && docker compose up -d --build`.
- **Certificado dos medidores:** o Caddy renova o certificado a cada ~60 dias; o Mosquitto só pega o novo ao reiniciar. Agende uma vez por mês (`crontab -e`):
  ```
  0 4 1 * * cd /caminho/MeterWatt/infra && docker compose restart mosquitto
  ```
- **Backup:** o aplicativo grava uma cópia cifrada por dia em `/dados/backups` (as 14 mais novas). Isso protege contra erro e banco corrompido, **não** contra perder o servidor. Copie para fora todo dia, por exemplo:
  ```
  30 4 * * * docker compose -f /caminho/MeterWatt/infra/docker-compose.yml cp app:/dados/backups /caminho/backups-meterwatt && rclone sync /caminho/backups-meterwatt remoto:meterwatt
  ```
- **Testar a restauração** (faça uma vez antes de ter clientes, e depois a cada alguns meses):
  ```bash
  docker compose exec app python -m meterwatt --restaurar /dados/backups/NOME.mwbackup
  ```
  Ele abre a cópia num arquivo novo e diz quantas contas encontrou, sem tocar no banco em uso.
- **Lista de espera:** `docker compose exec app python -m meterwatt --lista-espera > fila.csv`.
- **Bandeira tarifária (todo mês):** a ANEEL anuncia a bandeira do mês seguinte no fim de cada mês, em datas que ela publica (a de dezembro de 2026 sai em 27/11; o calendário de 2026 está em [gov.br/aneel](https://www.gov.br/aneel/pt-br/assuntos/noticias/2026/bandeira-tarifaria-aneel-divulga-calendario-de-acionamento-para-2026)). Registre no mesmo dia:
  ```bash
  docker compose exec app python -m meterwatt --bandeira 2026-12 amarela     # verde, amarela, vermelha1 ou vermelha2
  docker compose exec app python -m meterwatt --bandeiras                    # o que já está registrado
  ```
  Sem isso, a previsão da conta não soma a bandeira, e o log do servidor lembra uma vez por dia. Os valores de cada cor (R$ por 100 kWh) estão em `src/meterwatt/tarifas.py`: confira quando a ANEEL mudar.
- **Trocar a senha de provisão:** mude no `.env`, e no broker rode
  `docker compose exec mosquitto mosquitto_ctrl -u $MQTT_USUARIO -P 'SENHA' dynsec setClientPassword pareamento 'NOVA'`.

## Mercado Pago (quando forem testar a cobrança)
1. Crie um aplicativo em mercadopago.com.br/developers e pegue as **credenciais de teste**.
2. Em Webhooks, cadastre `https://SEU-DOMINIO/api/pagamentos/webhook/mercadopago` com o evento de assinaturas (*subscription_preapproval*) e copie a **assinatura secreta**.
3. No `.env`: `MW_PAGAMENTO_PROVEDOR=mercadopago`, `MW_MP_ACCESS_TOKEN=TEST-...`, `MW_MP_WEBHOOK_SEGREDO=...`, `MW_PLANO_PRECO=...`. Reinicie com `docker compose up -d`.
4. Use o botão "simular notificação" do painel do Mercado Pago e confira se o servidor respondeu 200. Se responder 401, a assinatura secreta não confere.

A integração foi escrita pela documentação pública e testada com um Mercado Pago de mentira (`tests/servidor/test_pagamentos.py`). **Ainda não conversou com o Mercado Pago de verdade.** Antes de cobrar alguém: fluxo completo com usuário de teste, nota fiscal e a regra de arrependimento em 7 dias. [ADVOGADO/CONTADOR]

## O que foi testado e o que não foi
| Peça | Situação |
|---|---|
| Aplicativo em modo produção (recusa configuração insegura) | Testes automáticos |
| Senha própria por medidor (plugin dynamic-security) | Testado contra o Mosquitto 2.0.18 de verdade, inclusive com o simulador |
| Firmware 3.3 (segredo, volta para a conta de pareamento, porta 8883) | Compila no GitHub (89% da memória) e passa no ESP32 simulado. **Não testado em placa** |
| Conexão criptografada do medidor (TLS, certificado Let's Encrypt) | **Não testada**: precisa do servidor no ar e de uma placa |
| Backup cifrado e restauração | Testes automáticos |
| Dockerfile, docker-compose, Caddy, script do Mosquitto | Executados juntos no GitHub a cada PR que mexe neles (`tools/ensaio_docker.py`, job "Conjunto Docker"): HTTPS pelo Caddy (autoridade interna, domínio `meterwatt.localhost`), medidor simulado pela 8883 com TLS, e-mail por SMTP com STARTTLS (servidor de mentira), cópia de segurança e reinício. **Não testados com domínio de verdade e Let's Encrypt** |
| Mercado Pago | HTTP de mentira nos testes. **Nunca falou com o serviço real** |
| App instalável (manifesto, service worker, sem internet, versão nova) | Chromium de verdade: instalável sem erros, abre sem internet, troca de versão |
| Notificações | Cifra igual ao exemplo da RFC 8291; no Chromium, a mensagem chega ao service worker e vira notificação. **Nunca entregue por um serviço real** (Google, Apple) nem testada num celular |
