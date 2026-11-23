# O MeterWatt no celular

O site do MeterWatt é também um app instalável (um *PWA*): ganha ícone na tela inicial, abre em tela cheia, sem a barra do navegador, abre mesmo sem internet e manda os avisos como notificação, com o app fechado. É o mesmo código do site: não há um segundo app para manter.

Este guia explica como instalar, como ligar as notificações, o que configurar no servidor e o caminho para a Play Store e a App Store.

## O que funciona em cada aparelho

| | Instalar | Notificações |
|---|---|---|
| Android: Chrome e navegadores baseados nele, como o Samsung Internet | Sim: botão "Instalar o app" no painel e na aba Conta | Sim, com ou sem o app instalado |
| iPhone e iPad, iOS 16.4 ou mais novo | Sim, pelo Safari: Compartilhar, Adicionar à Tela de Início | Só com o app aberto pelo ícone da tela de início |
| Computador: Chrome e Edge | Sim (ícone de instalar na barra de endereço ou o botão do painel) | Sim |
| Computador: Safari (macOS 14 ou mais novo) | Sim, pelo menu Arquivo, Adicionar ao Dock | Sim |
| Computador: Firefox | Na maioria das versões, não instala | Sim |

Tudo isso precisa de **HTTPS**. A única exceção é `http://localhost`, que os navegadores tratam como seguro (serve para testar no computador).

## Para quem usa

**Instalar.** No Android, o painel mostra o convite "Instale o app" (e a aba Conta, o item "Instalar o app"). No iPhone, o mesmo lugar abre o passo a passo: no Safari, Compartilhar (no iOS 26, dentro do menu de três pontinhos ao lado do endereço), Adicionar à Tela de Início, Adicionar.

**Ligar as notificações.** Em Avisos, bloco Notificações, botão "Ligar notificações". O navegador pede permissão e, a partir daí, cada aviso (medidor sem sinal, meta do mês, consumo fora do normal, resumo da semana) chega como notificação. Em "Como cada aviso chega", cada tipo tem duas chaves: notificação e e-mail. "Mandar um teste" confere se está tudo certo.

**Desligar.** "Desligar neste aparelho", ou "Remover" na lista de aparelhos (de qualquer aparelho da conta). Sair da conta num aparelho também desliga as notificações nele. Trocar a senha, ativar a verificação em duas etapas ou encerrar as outras sessões desliga nos outros aparelhos: quem perdeu o acesso à conta também para de receber os avisos dela.

**Sem internet.** O app abre e diz que está sem conexão; quando a internet volta, ele continua sozinho. Com o app aberto, uma faixa avisa que os números pararam de atualizar. Nenhum dado da conta fica gravado no aparelho: o que fica é só a "casca" (página, estilo, código, fontes e ícones).

**Versão nova.** Quando o servidor é atualizado, o app baixa a versão nova sozinho e mostra "Saiu uma versão nova do app. Atualizar agora". A pessoa escolhe a hora (para não perder o que estava digitando).

## Para quem roda o servidor

1. **Chave das notificações (VAPID).** Gere uma vez e guarde junto da `MW_CHAVE_CRIPTO`:
   ```bash
   python -m meterwatt --gerar-vapid
   ```
   Cole a linha `MW_VAPID_CHAVE_PRIVADA=...` no `.env`. Sem ela, em produção, as notificações ficam desligadas e o resto funciona normalmente (o app explica isso na tela Avisos). Em desenvolvimento, uma chave local é criada sozinha em `instancia/vapid-desenvolvimento.key`.
   **Trocar a chave desliga as notificações de todos os aparelhos**: cada pessoa precisa ligar de novo.
2. **Contato.** `MW_VAPID_CONTATO` (`mailto:...` ou `https://...`) vai junto de cada envio, para os serviços de notificação avisarem se algo der errado. Em branco, usa o `MW_EMAIL_PRIVACIDADE` ou o endereço do site.
3. **Saída para a internet.** O servidor entrega as notificações nos serviços dos navegadores, pela porta 443: `fcm.googleapis.com` (Chrome e Android), `*.push.apple.com` (iPhone e Safari), `updates.push.services.mozilla.com` (Firefox) e `*.notify.windows.com` (Edge no Windows). Se o firewall do servidor limitar a saída, libere esses endereços.
4. Não há nada a fazer no Caddy: `/sw.js`, `/manifest.webmanifest` e `/.well-known/assetlinks.json` saem do próprio aplicativo.

Não há serviço pago nem biblioteca nova: a cifra das mensagens (RFC 8291) e a assinatura do servidor (VAPID, RFC 8292) estão em `src/meterwatt/notificacoes.py`, com o pacote `cryptography`, e conferem byte a byte com o exemplo da RFC (`tests/servidor/test_notificacoes.py`).

## Testar no celular durante o desenvolvimento

O celular não acessa o `localhost` do computador, e um endereço como `http://192.168.0.10:8000` não é HTTPS. Dois jeitos:

**Android (mais simples).** Suba o servidor ouvindo na rede da casa (`MW_HTTP_HOST=0.0.0.0`) e, no Chrome do celular, abra `chrome://flags`, procure *Insecure origins treated as secure*, ative e escreva o endereço do computador (`http://192.168.0.10:8000`). Reinicie o Chrome. A partir daí, instalar e notificações funcionam como num site com HTTPS. Desligue a opção quando terminar.

**iPhone (e Android, sem mexer em opções).** Use um túnel HTTPS de teste, como o Cloudflare Quick Tunnel (grátis e sem conta, só para testes):
```bash
cloudflared tunnel --url http://localhost:8000
```
Ele mostra um endereço `https://...trycloudflare.com`, que muda a cada vez. Pare o servidor do MeterWatt e suba de novo com esse endereço:
```bash
MW_URL_PUBLICA=https://ENDERECO.trycloudflare.com MW_CONFIAR_PROXY=1 python -m meterwatt
```
(no Windows, no PowerShell: `$env:MW_URL_PUBLICA="https://..."; $env:MW_CONFIAR_PROXY="1"; python -m meterwatt`). Abra o endereço no Safari do iPhone, instale pela tela de início e ligue as notificações pelo app instalado.

## Play Store (Android)

O app instalado pelo Chrome já é um app de verdade no Android (o Chrome gera e instala um pacote para ele). Ir para a Play Store serve para ser encontrado lá e passar confiança. O caminho é o **Trusted Web Activity (TWA)**: um app Android pequeno que abre o site em tela cheia, sem barra de endereço, provando que o site e o app são do mesmo dono.

1. **Conta de desenvolvedor no Google Play Console:** taxa única de US$ 25. Contas **pessoais** criadas depois de 13/11/2023 precisam de um teste fechado com **pelo menos 12 testadores, por 14 dias seguidos**, antes de pedir acesso à produção. Conta de **organização** (com CNPJ) pede o número D-U-N-S (gratuito, pode levar semanas para sair).
2. **Gerar o app com o Bubblewrap** (precisa de Node.js; ele baixa sozinho o Java e as ferramentas do Android na primeira vez):
   ```bash
   npm i -g @bubblewrap/cli
   bubblewrap init --manifest=https://SEU-DOMINIO/manifest.webmanifest
   bubblewrap build
   ```
   No `init`, ele lê nome, cores e ícones do manifesto e pergunta o resto. Sugestões: pacote `br.com.meterwatt.app` (não muda nunca depois de publicado), notificações **ligadas** (*enable notifications*). Ele cria a chave que assina o app: **guarde o arquivo e as senhas**, sem eles não dá para atualizar o app. O `build` gera o pacote para a Play (`.aab`) e um `.apk` para instalar direto num celular de teste.
   (Alternativa sem linha de comando: pwabuilder.com, opção Android, que usa o Bubblewrap por baixo.)
3. **Provar que o site é de vocês.** O servidor publica `/.well-known/assetlinks.json` quando o `.env` tem:
   ```
   MW_ANDROID_PACOTE=br.com.meterwatt.app
   MW_ANDROID_SHA256=AA:BB:...,CC:DD:...
   ```
   São as impressões SHA-256 da chave que assina o app: a da chave criada pelo Bubblewrap (`keytool -list -v -keystore android.keystore`) e, depois do primeiro envio, a da **assinatura de apps da Play** (Play Console, página Integridade do app, Assinatura de apps). Coloque as duas, separadas por vírgula. Se a barra de endereço aparecer no app, quase sempre é a impressão errada ou faltando.
4. **Ficha da loja:** link da política de privacidade (`https://SEU-DOMINIO/#politica`), formulário de segurança dos dados (o que coletamos está em `docs/lgpd.md`), capturas de tela (as do manifesto, em `src/meterwatt/estatico/app/`, servem de ponto de partida) e classificação de conteúdo.

Desde 30/09/2026, no Brasil, o Android também passou a exigir desenvolvedor verificado para apps instalados fora da Play Store (um `.apk` passado direto para testadores). Para poucos aparelhos de teste existe uma conta de distribuição limitada. O app instalado pelo Chrome (PWA) não entra nessa regra.

## App Store (iPhone)

O Apple Developer Program custa US$ 99 por ano. As regras da App Store (item 4.2, funcionalidade mínima) recusam app que é só um site empacotado, e um app nativo para iPhone seria um segundo app para manter. Por enquanto, a recomendação é ficar no PWA no iPhone: instala pela tela de início e recebe notificações desde o iOS 16.4.

## Como funciona por dentro

- `src/meterwatt/estatico/manifest.webmanifest`: nome, cores, ícones (incluindo os *maskable*, que o Android recorta em círculo), atalhos (Agora, E se eu…?, Avisos) e telas para o convite de instalação.
- `src/meterwatt/estatico/sw.js`, servido em `/sw.js` (`src/meterwatt/instalavel.py`): guarda a casca do app. A versão é um resumo do conteúdo dos arquivos, então qualquer mudança gera uma versão nova, baixada inteira de uma vez. Nunca guarda `/api/`.
- `src/meterwatt/notificacoes.py`: inscrições (endereço e chaves cifrados no banco), cifra das mensagens, assinatura do servidor, e o que fazer com cada resposta dos serviços (inscrição vencida sai; falha de rede não conta contra o aparelho). Só aceita endereços HTTPS dos serviços conhecidos e não segue redirecionamentos, para o servidor não virar ponte para outros endereços.
- `tools/gerar_icones.py`: gera os ícones e as telas do manifesto (com Playwright).
