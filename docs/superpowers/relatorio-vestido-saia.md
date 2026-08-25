# Relatório — vestido × saia no zero-shot do FashionCLIP

**Data:** 2026-08-25 · **Instrumento:** `scripts/investigate_dress_skirt.py`
**Corpus:** as 33 imagens de `test-images/` · **Modelo:** FashionCLIP, prompts
atuais de `app/services/ai/labels.py` (não alterados por esta investigação)

## Veredito: ambiguidade da imagem, não confusão sistemática

A pergunta era se `saia` ganhar de `vestido` numa peça foi azar daquela foto ou
falha na distinção entre as duas classes. O critério, fixado antes de medir:
**três ou mais** imagens com margem menor que 0.15 entre as duas seria confusão
sistemática; menos que isso, ambiguidade pontual.

**Medido: zero.** Nenhuma das 33 imagens tem margem estreita entre `vestido` e
`saia`. As duas classes separam com folga em todas.

Por isso os prompts NÃO foram alterados. Mexer em prompts que já separam bem só
arrisca regressão nas outras classes — trocar um erro de vestido/saia por um de
camisa/malha não é progresso.

## Os números

| imagem | vencedor | p(vestido) | p(saia) | margem |
|--------|----------|-----------:|--------:|-------:|
| 4.jpg  | saia     | 0.000 | 1.000 | 1.000 |
| 7.jpg  | vestido  | 0.989 | 0.008 | 0.981 |
| 9.jpg  | vestido  | 0.993 | 0.002 | 0.991 |
| 12.jpg | vestido  | 0.997 | 0.001 | 0.997 |
| 33.jpg | vestido  | 0.983 | 0.006 | 0.978 |

As outras 28 imagens são de outras categorias e têm p(vestido) e p(saia)
praticamente zero — nenhuma delas chega perto de confundir as duas.

O caso que originou a investigação, `12.jpg`, é hoje o mais folgado de todos:
vestido 0.997 contra saia 0.001.

## Antes e depois

Não há "depois": nenhuma mudança foi feita, porque o "antes" já respondia a
pergunta. A confiança não melhorou nem piorou — ela já estava alta, e é isso que
o relatório registra. Os prompts distintivos que uma investigação anterior
introduziu (`vestido` enfatizando peça única que cobre tronco e pernas, `saia`
enfatizando só da cintura para baixo) resolveram o problema; esta rodada apenas
mediu e confirmou.

## Calibração do limiar de categoria

`scripts/calibrate_fashion_clip.py` sobre as mesmas 33 imagens, com
`FASHION_CLIP_THRESHOLD_CATEGORIA = 0.80`:

- 31 de 33 imagens passam do limiar; a mediana dos aprovados é **0.995** e o
  menor aprovado é **0.819**.
- As 2 que ficam abaixo são `31.jpg` (camisa, 0.782) e `30.jpg` (camisa, 0.605)
  — as duas com o rótulo CERTO em primeiro lugar, apenas sem confiança
  suficiente para preencher o campo sozinhas.

O limiar continua no lugar certo: quase tudo que ele aprova vem com confiança
quase total, e o que ele barra são exatamente os casos em que o modelo hesita.
Baixá-lo para 0.6 preencheria esses dois campos automaticamente e passaria a
aceitar palpite como se fosse certeza — o campo em branco é o comportamento
desejado ali.

## O que fica em aberto

O erro relatado pelo dono do projeto (uma peça com aparência de vestido
classificada como `saia`) **não se reproduz** neste corpus. Se voltar a
acontecer, a foto específica precisa entrar em `test-images/` — sem ela, não há
o que investigar além do que já está aqui.

A limitação estrutural que permanece, independentemente destes números, é que a
composição de look confia na `category` gravada na peça: uma categorização
errada vira um erro de composição depois. Isso é tratado na Task 14.
