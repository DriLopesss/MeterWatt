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
| Novidades por e-mail | não | sim | consentimento |

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

## Pendências jurídicas e de operação
- [ ] [ADVOGADO] Revisar política e termos (rascunhos em `src/meterwatt/estatico/index.html`).
- [ ] [ADVOGADO] Nomear o encarregado (DPO) ou registrar a dispensa para agente de pequeno porte (Resolução CD/ANPD nº 2/2022).
- [ ] [ADVOGADO] Contratos com operadores (hospedagem, e-mail) e transferência internacional (art. 33) se os servidores ficarem fora do Brasil.
- [ ] Registro das operações de tratamento (art. 37) e relatório de impacto (RIPD) para o dado de consumo.
- [ ] Plano de resposta a incidentes com prazo de comunicação à ANPD (Resolução CD/ANPD nº 15/2024: 3 dias úteis).
- [ ] Imóvel com mais moradores: quem paira o medidor responde por informar os outros [ADVOGADO: cláusula nos termos].
