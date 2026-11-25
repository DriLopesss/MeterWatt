# Como avisar de uma falha de segurança

Achou um jeito de ver dados de outra pessoa, entrar numa conta que não é sua, se passar por um medidor
ou qualquer outra falha no MeterWatt? Obrigado por avisar.

- **Escreva para o e-mail de segurança** que aparece em `/.well-known/security.txt` do site (ou, enquanto
  o site não estiver no ar, abra um aviso privado em *Security > Report a vulnerability* neste repositório).
  Não abra uma issue pública.
- Conte o que encontrou, como reproduzir e o que dá para fazer com isso. Uma prova simples ajuda muito.
- Respondemos em até 5 dias úteis, combinamos um prazo para corrigir e avisamos quando estiver resolvido.
  Se quiser, damos o crédito na correção.

Enquanto investiga, por favor:
- use só contas e medidores seus (ou de teste); não acesse, altere nem apague dados de outras pessoas;
- não faça testes que derrubem o serviço para os outros (carga, negação de serviço) nem engenharia social;
- não mexa na instalação elétrica de ninguém: o medidor fica ligado à rede da casa.

O que já foi feito e o que falta está em [`docs/seguranca.md`](docs/seguranca.md).
