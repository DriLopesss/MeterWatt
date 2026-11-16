# Segurança do aplicativo MeterWatt

O que está no código (`src/meterwatt`) e o que ainda falta para receber clientes de verdade.

## Contas e senhas
- Senha guardada com **Argon2id** (t=2, 19 MiB, p=1, parâmetros mínimos da OWASP). Hash antigo é refeito sozinho no próximo login.
- Regras: 10 a 128 caracteres, fora de uma lista de senhas comuns, sem o e-mail dentro, pelo menos 5 caracteres diferentes.
- **Ninguém descobre se um e-mail tem conta**: cadastro, "esqueci a senha" e reenvio respondem igual; a entrada gasta o mesmo tempo com ou sem conta. Quem tenta cadastrar um e-mail já usado só gera um aviso para o dono do e-mail.
- E-mail precisa ser confirmado (link de 24 h). Links de e-mail levam o token depois do `#`: o navegador não manda essa parte ao servidor, então ela não aparece em registros de acesso.
- Redefinir senha: link de 30 minutos, uso único; encerra todas as sessões.

## Verificação em duas etapas
- TOTP (RFC 6238, 6 dígitos, 30 s, ±1 passo), testado com os vetores da RFC. Código usado não vale de novo.
- Segredo **cifrado** no banco (Fernet, chave `MW_CHAVE_CRIPTO` fora do banco).
- 10 códigos de backup de uso único, guardados como HMAC. Usar um avisa por e-mail.
- 5 códigos errados derrubam a meia-sessão; ela também expira em 5 minutos.
- `MW_EXIGIR_2FA=1` bloqueia painel e medidores para contas sem 2FA. Privacidade continua liberada (direitos do titular).

## Sessões
- Token aleatório de 256 bits num cookie `HttpOnly`, `SameSite=Strict`, `Secure` e com prefixo `__Host-` em produção. No banco fica só o SHA-256 do token.
- Token novo ao concluir a entrada (contra fixação de sessão). Expira com 24 h sem uso e, no máximo, em 14 dias.
- A pessoa vê onde a conta está aberta (tipo de navegador e rede /24) e encerra qualquer sessão.
- Trocar senha ou ativar 2FA encerra as outras sessões e avisa por e-mail.

## Pedidos
- **CSRF**: token por sessão, obrigatório no cabeçalho `X-CSRF-Token` em tudo que altera dados; comparação em tempo constante.
- Pedidos que alteram dados precisam ser JSON e vir da própria origem (`Origin` e `Sec-Fetch-Site`).
- Corpo máximo de 64 KB. Erros sempre em JSON, sem detalhes internos.
- Cabeçalhos: CSP só com `'self'` (sem `unsafe-inline`, sem nada de fora: as fontes são servidas pelo próprio servidor), HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP e CORP.
- Limites de tentativas: login 5 por e-mail e 30 por rede a cada 15 min; formulários públicos 10 por hora por rede; senha pedida de novo em ações sensíveis 5 a cada 15 min; cópia dos dados 5 por hora.
- Recurso de outra conta responde **404**, igual a um que não existe.
- No navegador, nada de `innerHTML` com dado do servidor.

## Medidores e MQTT
- Pareamento por código de 8 caracteres (31 símbolos, ~10¹² combinações), 15 minutos, no máximo 3 ativos por conta, guardado como HMAC. 10 códigos errados por aparelho em 15 min bloqueiam aquele aparelho.
- Medidor que já é de outra conta não é transferido.
- Mensagem de aparelho não pareado é descartada (nada é guardado). Mensagens acima de 2 KB, fora do formato ou mais rápidas que 0,8 s são ignoradas.
- **Senha própria por medidor** (`MW_MQTT_DYNSEC=1`, Mosquitto com o plugin dynamic-security): no pareamento, o medidor manda um segredo de 128 bits que ele mesmo sorteou; o servidor cria no broker um usuário só dele, preso ao id de cliente dele, com permissão apenas para `meterwatt/<id>/...`. Antes de parear, o medidor usa uma conta de "provisão" que só consegue pedir pareamento. Remover o medidor apaga o usuário. Um código válido de outra conta nunca troca a senha de um medidor que já tem dono. Testado contra o Mosquitto 2.0.18.
- **Conexão criptografada**: firmware 3.3 usa TLS na porta 8883 e confere o certificado do servidor (raiz da Let's Encrypt em `ca_raiz.h`). Não testado em placa.
- Limite conhecido: quem extrair a senha de provisão de um medidor consegue derrubar a conexão de outro (conectando com o mesmo id), mas não ler nem enviar consumo.
- Sem o dynamic-security (rede de casa), vale o esquema antigo: `infra/mosquitto-acl.exemplo`.

## Banco
- SQLite com WAL, chaves estrangeiras e `secure_delete` (o que é apagado é sobrescrito). Arquivo com permissão 600.
- Migrações versionadas (`PRAGMA user_version`). O esquema passa para PostgreSQL quando houver muitos clientes.

## Produção
`MW_MODO=producao` se recusa a subir sem chave de criptografia, sem HTTPS na URL pública, com cookie inseguro ou sem SMTP. Use atrás de Caddy ou nginx com HTTPS e `MW_CONFIAR_PROXY=1`. Faça backup do banco **e** da chave (separados).

## Pagamentos
- O número do cartão nunca passa pelo MeterWatt: a pessoa paga na página do Mercado Pago.
- O aviso do Mercado Pago (webhook) só é aceito com a assinatura HMAC-SHA256 do cabeçalho `x-signature`; o conteúdo do aviso serve apenas para saber qual assinatura consultar na API.
- O pagamento simulado é recusado em produção (aprovaria assinaturas sem cobrar).
- Excluir a conta cancela a cobrança antes; se o cancelamento falhar, a conta não é excluída.

## Cópias de segurança
- Diárias, compactadas e cifradas com a `MW_CHAVE_CRIPTO` (arquivo com permissão 600). Restaurar nunca sobrescreve o banco em uso.
- Levar as cópias para fora do servidor é tarefa de quem opera (veja `docs/implantacao.md`).

## Ainda falta
- [ ] Pentest independente antes do lançamento.
- [ ] Limitador de tentativas compartilhado (Redis) quando houver mais de um processo.
- [ ] Verificar senhas vazadas (Have I Been Pwned, consulta k-anonymity).
- [ ] Alertas de login em aparelho novo.
- [ ] Cópia automática dos backups para fora do servidor e ensaio de restauração.
- [ ] Testar em placa: senha própria, TLS e a volta para a conta de pareamento.
- [ ] Rodar o conjunto Docker num servidor de verdade.
