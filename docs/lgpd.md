# LGPD no MeterWatt

Como o aplicativo trata dados pessoais e o que precisa de advogado. Itens **[ADVOGADO]** são decisões jurídicas que o código não resolve.

## Por que é dado pessoal (e sensível na prática)
A curva de consumo de uma casa mostra quando há gente, quando dorme, quando viaja e quais aparelhos usa. Não está na lista de dados sensíveis do art. 5º, II, mas tratamos com o mesmo cuidado.

## Finalidades e bases legais
| Finalidade | Obrigatória? | Pode revogar? | Base legal |
|---|---|---|---|
| Termos de uso | sim | não (é sair da conta) | contrato (art. 7º, V) |
| Política de privacidade | sim (ciência) | não | — |
| Dados de consumo | sim no cadastro | **sim**: param as leituras novas e o pareamento | consentimento (art. 7º, I) [ADVOGADO: ou execução de contrato?] |
| Histórico de 2 anos | não | sim | consentimento |
| Detetive de aparelhos | não (desligado por padrão) | sim: **apaga** tudo o que ele guardou | consentimento |
| Novidades por e-mail | não | sim | consentimento |
| Lista de espera (só e-mail) | — | sim, pelo link de cada e-mail | consentimento |
| Assinatura (situação e valor) | só para quem assina | cancelando | contrato [ADVOGADO/CONTADOR: guarda fiscal] |

Cada decisão fica registrada com versão do texto, data e origem (cadastro ou painel). A pessoa vê o histórico.

## Direitos do titular (art. 18), sem precisar escrever para ninguém
- **Acesso e portabilidade**: botão "Baixar meus dados" gera JSON completo (conta, autorizações, medidores, consumo, sessões, atividade), sem hashes nem segredos.
- **Eliminação**: "Apagar consumo" (mantém a conta) e "Excluir conta" (tudo, em cascata). Pedem senha e, com 2FA, o código.
- **Revogação**: chaves na tela Privacidade.
- **Informação**: tela mostra prazos de retenção, atividade da conta e contato do encarregado.
- **Correção**: nome e e-mail [falta tela para trocar e-mail com confirmação].

## Minimização
- Só medidor pareado a uma conta com autorização tem leitura guardada.
- Endereço de rede guardado só até /24 (IPv4) ou /48 (IPv6); navegador só como "Chrome no Windows".
- Leituras de segundo a segundo duram 48 h; depois ficam só totais de 5 em 5 minutos.

## Retenção automática (roda a cada 15 min)
| Dado | Prazo |
|---|---|
| Leituras detalhadas | 48 horas |
| Consumo de 5 em 5 min | 90 dias (730 com histórico longo) |
| Atividade da conta | 180 dias [ADVOGADO: Marco Civil, art. 15, pede 6 meses de registros de acesso a aplicação] |
| Conta sem e-mail confirmado | 7 dias |
| Sessões, links e códigos vencidos | 1 dia depois de vencer |
| Registro de exclusão (HMAC do e-mail + data) | indefinido [ADVOGADO: definir] |

## O que entrou no bloco 4
- **Avisos**: fazem parte do serviço autorizado em "dados de consumo". Vão por e-mail com totais (kWh e reais); cada tipo tem uma chave para desligar o e-mail. Avisos são apagados com o consumo e em 90 dias.
- **Detetive de aparelhos**: dado derivado mais invasivo que o total (qual aparelho, a que horas). Autorização separada, desligada por padrão; ciclos guardados por 90 dias; entra na cópia dos dados; some ao revogar, ao apagar o consumo e ao remover o medidor.
- **Lista de espera**: só e-mail, com confirmação (duplo aceite). Quem não confirma é apagado em 7 dias. O link de saída apaga o registro. [ADVOGADO: definir até quando guardar os confirmados se o lançamento demorar.]
- **Pagamento**: o Mercado Pago passa a ser operador (recebe e-mail e valor). Não guardamos dado de cartão. A referência enviada a ele é um código aleatório, não o e-mail nem o id da conta.
- **Página inicial**: sem cookies e sem rastreadores. A origem da visita (`?o=instagram`) só é guardada junto do e-mail de quem entra na lista.

## O que entrou no bloco 5
- **Plano de economia** (aba Economia, "E se eu…?"): guarda as ações que a pessoa marcou e os números que ela ajustou (banhos por semana, minutos de uso, potência, preço de uma geladeira nova). É dado sobre os hábitos da casa, então segue o consumo: faz parte do serviço autorizado em "dados de consumo", entra na cópia dos dados (`plano_de_economia`), some ao apagar o consumo e ao excluir a conta, e cada mudança fica na atividade da conta ("Plano de economia alterado").
- **Conferência "Deu certo?"**: compara o consumo por dia antes e depois de cada ação. Junto de cada ação fica guardada só a "linha de base": a média de consumo por dia das duas semanas anteriores (e, quando o Detetive estava ligado, a dos aparelhos dela; no consumo em espera, a base das madrugadas). Sem isso, a conferência de um plano com mais de ~2 meses sumiria quando a retenção apagasse o consumo de 5 em 5 minutos daqueles dias. A linha de base sai junto com a ação, entra na cópia dos dados e some ao apagar o consumo. Sem a autorização de consumo, a conferência não roda; aparelho por aparelho, só entram dias em que o Detetive já estava ligado.
- **Simulador**: as contas de quem só mexe nos controles rodam no navegador e não vão para o servidor. Só vai o que a pessoa põe no plano.
- O resumo semanal por e-mail passa a dizer quanto a casa economizou desde o início do plano (mesma chave de e-mail do resumo).

## O que entrou no bloco 6 (app no celular)
- **App instalável**: o que fica gravado no aparelho é só a casca do app (página, estilo, código, fontes, ícones). Nada de `/api/` é guardado: o consumo, os avisos e os dados da conta não ficam no celular. De uma visita para outra, o app guarda no navegador só escolhas de tela: o "Agora não" do convite de instalação e, desde o bloco 8, se a casa em 3D do painel fica ligada. Enquanto a aba está aberta, ele também lembra se está no modo demonstração ou dentro do app da Play.
- **Notificações**: só nos aparelhos em que a pessoa liga (o navegador também pede permissão). Guardamos o endereço que o navegador cria para receber notificações e as duas chaves que cifram cada mensagem, **cifrados** no banco, mais o tipo de aparelho e as datas. Base: parte do serviço que a pessoa pediu ao ligar. Saem quando a pessoa desliga, tira o aparelho da lista, sai da conta naquele aparelho, troca ou redefine a senha, ativa a verificação em duas etapas, encerra as outras sessões, exclui a conta, ou quando o serviço do navegador diz que a inscrição venceu. A sessão vencer por falta de uso **não** desliga (os avisos servem justamente para quando o app está fechado).
- **Transferência internacional**: cada notificação passa pelo serviço do navegador (Google, Apple, Mozilla ou Microsoft), fora do Brasil. A mensagem vai cifrada de ponta a ponta (RFC 8291): o serviço não lê o conteúdo, mas sabe que um aparelho recebeu uma notificação e quando. O tamanho cifrado sobe de 256 em 256 bytes, para o tamanho não entregar qual aviso foi. Está na política de privacidade. [ADVOGADO: enquadrar no art. 33.]
- **Na tela de bloqueio**: o texto do aviso (com kWh e reais) aparece como a pessoa configurou o celular. Cada tipo de aviso tem a sua chave de notificação, separada da de e-mail.
- **Cópia dos dados**: `aparelhos_com_notificacoes` traz o aparelho e as datas. Endereço e chaves ficam de fora: são segredos que só servem para entregar mensagens naquele aparelho.
- **Atividade da conta**: "Notificações ligadas num aparelho" e "Notificações desligadas num aparelho".
- A política de privacidade passou para a versão 2026-11-23 (item novo "Notificações" e o trecho sobre os serviços dos navegadores).

## O que entrou no bloco 7 (Play Store)
- **Páginas abertas sem login**: `/politica-de-privacidade`, `/termos-de-uso` e `/excluir-conta`, com o mesmo texto das telas do app (os `<template>` do `index.html`), como a Play pede. A de exclusão explica o caminho no app, o pedido por e-mail para quem não consegue entrar (resposta em até 15 dias), o que sai na hora e o que fica: o registro de exclusão, os registros de pagamento [ADVOGADO: prazo] e as cópias de segurança, que perdem o dado em até `MW_BACKUP_MANTER` dias. Os valores vêm da configuração (`MW_CONTROLADOR`, `MW_EMAIL_PRIVACIDADE`); em produção, o servidor avisa ao subir se faltarem.
- **Política** (ainda a versão 2026-11-23, que não foi ao ar): ganhou a linha das cópias de segurança e o link para o passo a passo da exclusão.
- **Modo demonstração** ("Ver o app funcionando"): roda inteiro no navegador, com dados inventados. Não cria conta, não grava nada no servidor e não liga notificações.
- **App da Play**: é o mesmo site em tela cheia; o app Android não coleta nada além do que o site já coleta e não tem anúncios nem ID de publicidade. Não vende a assinatura (dentro do app da Play não há pagamento).
- **Formulário de segurança dos dados da Play**: as respostas têm de bater com a política. Proposta: nome, e-mail, ID da conta, consumo e o que a pessoa digita, registro de segurança, identificadores (notificações e medidor) e, com a assinatura ligada, histórico de compras; nada compartilhado. [ADVOGADO: conferir junto com a política.]

## O que entrou no bloco 8 (gráficos e vários medidores)
- **Gráficos do painel** (*Para onde vai agora*, *Perfil do dia*, mapa de 14 dias): saem na hora do consumo de 5 em 5 minutos que já está guardado. Não gravam nada novo; o "sempre ligado" de cada medidor fica alguns minutos só na memória do servidor. A divisão por aparelho usa o Detetive e só aparece com a autorização dele ligada; sem ela, o gráfico mostra só "sempre ligado" e "outros".
- **O que cada medidor mede** (a entrada de um imóvel ou um circuito dentro de uma entrada): fica no cadastro do medidor, entra na cópia dos dados (`mede` e `dentro_de`, em cada medidor) e cada mudança vai para a atividade da conta ("O que o medidor mede mudou").
- **Casa em 3D**: a escolha de mostrar ou esconder fica só no navegador daquele aparelho (`mw-casa-3d`), como o "Agora não" do convite. Não vai para o servidor.

## O que entrou no bloco 9 (a conta de verdade)
- **Tarifa Branca**: o horário de ponta e os quatro preços da distribuidora ficam no cadastro da pessoa (são escolhas dela, não medição), entram na cópia dos dados (`conta.tarifa_branca`) e cada mudança vai para a atividade da conta ("Horário e preços da Tarifa Branca alterados"). A comparação sai na hora do consumo de 5 em 5 minutos que já está guardado; nada novo é gravado.
- **Quem mais gasta na ponta**: usa os ciclos do Detetive e só aparece com a autorização dele.
- **Tipo de ligação** (monofásica, bifásica ou trifásica): no cadastro, na cópia dos dados (`conta.ligacao`) e na atividade ("Tarifa, meta ou tipo de ligação alterados").
- **Bandeira do mês**: é um dado público da ANEEL, igual para todas as contas, registrado por quem opera o servidor.

## O que entrou no bloco 11 (conferir com a conta de luz)
- **Contas de luz digitadas**: período, kWh faturado e total em reais, junto do consumo medido no período e de quanto dele foi medido (guardados na hora, porque o consumo de 5 em 5 minutos some com a retenção). É dado de consumo, então segue o consumo: faz parte do serviço autorizado em "dados de consumo", entra na cópia dos dados (`contas_de_luz`), some ao apagar o consumo e ao excluir a conta, e cada conta guardada ou apagada vai para a atividade. No máximo 36 contas por pessoa.

## O que entrou no bloco 12 (relatório do mês)
- O relatório sai na hora do consumo já guardado; nada novo é gravado. A planilha é montada no navegador da pessoa, sem passar por outro serviço.
- **Resumo do mês**: um aviso novo (dia 1º), com a mesma base dos outros avisos (parte do serviço autorizado em "dados de consumo"); cada canal (e-mail e notificação) tem a sua chave em Avisos. O texto do e-mail tem kWh e reais do mês.

## Pendências jurídicas e de operação
- [ ] [ADVOGADO] Revisar política e termos (rascunhos em `src/meterwatt/estatico/index.html`).
- [ ] [ADVOGADO] Nomear o encarregado (DPO) ou registrar a dispensa para agente de pequeno porte (Resolução CD/ANPD nº 2/2022).
- [ ] [ADVOGADO] Contratos com operadores (hospedagem, e-mail) e transferência internacional (art. 33) se os servidores ficarem fora do Brasil.
- [ ] Registro das operações de tratamento (art. 37) e relatório de impacto (RIPD) para o dado de consumo.
- [ ] Plano de resposta a incidentes com prazo de comunicação à ANPD (Resolução CD/ANPD nº 15/2024: 3 dias úteis).
- [ ] Imóvel com mais moradores: quem paira o medidor responde por informar os outros [ADVOGADO: cláusula nos termos].
