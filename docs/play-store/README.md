# Imagens da ficha do MeterWatt na Play Store

Saem do app de verdade, na casa simulada da demonstração (o botão "Ver o app funcionando"), por
`tools/gerar_loja.py`. Para gerar de novo, depois de mudar o visual do app:

```
python tools/gerar_loja.py
```

Precisa do Playwright com o Chromium e do Pillow. `tests/test_play_store.py` confere os tamanhos e
formatos que a Play exige ([regras da Play](https://support.google.com/googleplay/android-developer/answer/9866151),
[especificação do ícone](https://developer.android.com/google-play/resources/icon-design-specifications)).

| Arquivo | Onde entra na Play Console | O que a Play exige |
|---|---|---|
| `icone-512.png` | Ficha principal da loja, ícone do app (*App icon*) | 512 x 512, PNG de 32 bits, quadrado cheio: a Play arredonda os cantos e põe a sombra |
| `destaque-1024x500.png` | Ficha principal da loja, gráfico de destaque (*Feature graphic*) | 1024 x 500, JPEG ou PNG sem transparência; o essencial no centro |
| `tela-1-agora.png` a `tela-8-avisos.png` | Capturas de tela do celular (*Phone screenshots*), nesta ordem | 2 a 8 (são 8, o máximo); com 4 ou mais em 1080 x 1920 o app pode aparecer nos destaques da loja |

## Frases e texto alternativo das capturas

A Play pede um texto alternativo de até 140 caracteres para cada captura (leitores de tela).
As frases ficam em `TELAS`, no começo de `tools/gerar_loja.py`. As capturas mostram sempre o mesmo momento da casa simulada (uma quinta às 19h35,
com o chuveiro ligado), para saírem iguais toda vez que forem geradas.

| Arquivo | Frase na imagem | Texto alternativo |
|---|---|---|
| `tela-1-agora.png` | Quanto sua casa gasta agora, em reais | Painel do MeterWatt com o consumo da casa agora, em watts e em reais por hora, dentro de uma casa desenhada em 3D |
| `tela-2-para-onde.png` | Para onde vai a energia neste momento | Diagrama do consumo de agora dividido em aparelhos ligados, o que fica sempre ligado e outros, em watts e reais por hora |
| `tela-3-perfil.png` | Cada hora do dia comparada ao normal | Gráfico do consumo de hoje hora a hora, com uma linha do normal de um dia útil e o horário mais pesado do dia |
| `tela-4-meta.png` | A previsão da conta antes de ela chegar | Cartão Meta do mês com a previsão da conta de luz, a meta em reais e quanto ainda sobra no mês |
| `tela-5-fantasma.png` | O que fica ligado sem ninguém usar | Cartão Consumo fantasma com os watts sempre ligados e quanto eles custam por mês e por ano |
| `tela-6-e-se-eu.png` | Teste uma mudança e veja quanto economiza | Simulador E se eu…? com a economia por ano dos ajustes escolhidos e o plano de economia em reais |
| `tela-7-detetive.png` | Pistas de quais aparelhos pesam na conta | Detetive de aparelhos com um aparelho de 5.400 W que parece chuveiro e quanto ele custa por mês |
| `tela-8-avisos.png` | Avisos quando o consumo foge do normal | Lista de avisos com um consumo fora do normal e o resumo da semana com o gasto e a previsão da conta |
