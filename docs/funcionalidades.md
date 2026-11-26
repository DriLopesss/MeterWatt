# Funcionalidades do MeterWatt (lista para conferir antes e depois de mexer no visual)

Cada item tem um código. Os que começam com **F** são conferidos sozinhos, num navegador de verdade, por
`python tools/conferir_front.py` (sobe o servidor numa pasta temporária, usa uma conta nova e o modo
demonstração). Os que começam com **M** dependem do broker MQTT e do medidor, e são conferidos por
`python tools/ensaio_completo.py` (no Linux, macOS ou WSL, e no GitHub a cada PR). Os **P** precisam de uma
pessoa (celular de verdade, HTTPS, placa).

Situação em 26/11/2026: antes do visual novo, **40 de 40 F** certos (depois de corrigir o erro 500 do relatório
numa conta sem medidor). Depois do visual novo (PR #25), **43 de 43 F**, com três itens novos (F06, F43, F44),
e **26 de 26 M** no ensaio geral.

## Página inicial e lista de espera
- **F01** A página inicial abre, com título, calculadora e lista de espera.
- **F02** A calculadora de consumo fantasma muda com os botões + e − e com a tarifa.
- **F03** Entrar na lista de espera, confirmar pelo link do e-mail e ver a posição na fila.
- **F04** Sair da lista pelo link do e-mail.
- **F05** Páginas abertas: política de privacidade, termos de uso, excluir a conta, `/.well-known/security.txt` e a tela de endereço inexistente (404), com o interruptor que acende a luz.
- **F06** Vitrine 3D: a rolagem abre o medidor passo a passo e, no fim, o visor sai do palco.

## Conta
- **F10** Cadastro recusa senha fraca, com mensagem.
- **F11** Cadastro cria a conta; o e-mail de confirmação chega e o link confirma.
- **F12** Entrar: senha errada mostra erro; a certa abre o painel.
- **F13** Painel sem medidor convida a parear.
- **F14** Todas as telas do app abrem sem erro, com título e sem rolar para o lado: painel, medidores, aparelhos, economia, segurança, privacidade, conta, avisos, plano, relatório, compartilhar, Home Assistant.
- **F15** Adicionar medidor mostra o código de pareamento com o prazo.
- **F16** Começar a verificação em duas etapas mostra o QR e o segredo.
- **F17** A lista de sessões abertas aparece em Segurança.
- **F18** Privacidade: autorizações, baixar meus dados (arquivo) e atividade da conta.
- **F19** Plano: pagamento de teste aprovado vira plano Casa, com o botão de cancelar.
- **F20** Home Assistant: criar uma chave só de leitura.
- **F21** Compartilhar a casa: convidar por e-mail.
- **F22** Avisos: preferências de cada aviso (notificação e e-mail).
- **F23** Sair da conta; o painel passa a pedir login.
- **F24** Esqueci a senha: o link chega, a tela confere o link e a senha nova funciona.
- **F25** Link de senha velho ou cortado avisa na hora e oferece pedir outro.

## Telas com dados (modo demonstração)
- **F30** A demonstração abre pelo botão da página inicial, com a faixa de aviso.
- **F31** Painel: número de agora, Hoje, meta do mês, consumo fantasma e plano de economia.
- **F32** Painel: gráficos de fluxo (para onde vai agora), perfil do dia e últimos 14 dias.
- **F33** Perfil do dia alterna entre Hoje e o mapa de 14 dias, e a tabela abre.
- **F34** Os gráficos mostram a dica ao passar o mouse.
- **F35** A casa em 3D liga e desliga pelo botão 3D.
- **F36** Medidores: medidas, fases, gráfico de 2 h, 24 h e 7 dias, e a qualidade da energia.
- **F37** O gráfico do medidor responde às setas do teclado.
- **F38** Aparelhos: lista do Detetive.
- **F39** Economia: mexer num controle muda o placar; conta de luz e Tarifa Branca aparecem.
- **F40** Relatório do mês: números, gráfico dia a dia e tabela.
- **F41** Avisos: a tela lista os avisos.
- **F42** Sair da demonstração.
- **F43** Osciloscópio de cada medidor: tensão e corrente com o ângulo do fator de potência.
- **F44** A Faísca comenta o consumo de agora.

## Celular
- **F50** Página inicial, entrar e cadastro cabem na tela, sem rolar para o lado.
- **F51** Na demonstração, as abas de baixo levam a cada tela, sem rolar para o lado.
- **F52** Tocar num gráfico mostra a dica.

## Acessibilidade e movimento
- **F60** Com "reduzir movimento" ligado no aparelho, o app abre e funciona.
- **F61** O Tab chega aos controles (começando pelo "Pular para o conteúdo").
- **F62** Todo gráfico tem descrição em texto (aria-label).

## Medidor e MQTT (ensaio geral)
- **M01** Pareamento pelo código, com senha própria por medidor e TLS na porta 8883.
- **M02** Medidor bifásico: soma das fases e cada fase separada.
- **M03** Circuito dentro de uma entrada (não soma duas vezes).
- **M04** Medidor sem Wi-Fi guarda os minutos e manda quando volta.
- **M05** Permissões no broker: um medidor não lê nem escreve no tópico de outro.
- **M06** Medidor que cai some do "online"; remoção apaga a credencial.
- **M07** Cópia dos dados e backup cifrado com o servidor ligado.

## Só com uma pessoa
- **P01** Instalar o app no computador e no celular (o celular precisa de HTTPS).
- **P02** Notificação chegando no celular.
- **P03** O app Android da Play Store.
- **P04** Leituras reais do PZEM, com o eletricista.
- **P05** Pagamento de verdade no Mercado Pago.
