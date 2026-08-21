# Spec — Migrar a geração do look do dia para a API do Claude

**Data:** 2026-08-21
**Origem:** pedido do usuário (Arthur), transcrito na íntegra e anotado com as
decisões tomadas na sessão de planejamento.

## Escopo

**Fora de escopo — não muda nada:** a análise de peça de roupa (FashionCLIP,
k-means, regras) que determina categoria, cor, formalidade e peso térmico no
cadastro. Continua exatamente como está, self-hosted e sem custo.

**Em escopo — o que sai de `services/ai/look_generation.py`:**
- a montagem de looks por regras de cor e formalidade;
- a geração de justificativa por templates.

**Em escopo — o que fica:** o pré-filtro por temperatura de referência e
condição climática. É gratuito, reduz o payload enviado à API e resolve bem
uma decisão que não precisa de modelo de linguagem.

## Requisitos

1. **Dependência e configuração.** SDK oficial `anthropic` no `requirements.txt`.
   Chave via `ANTHROPIC_API_KEY` lida do `.env`, nunca hardcoded. Identificador
   do modelo em variável separada `ANTHROPIC_MODEL` para troca sem mexer no código.

2. **Pré-filtro determinístico como primeira etapa.** Roda antes de qualquer
   chamada à API, reduzindo o guarda-roupa ao subconjunto compatível com o clima.

3. **Manual de estilo da Miranda como system prompt.** A parte mais importante.
   Cobre persona (editora de moda de altíssimo padrão, tom frio e decisivo, mas
   conteúdo tecnicamente correto sobre moda), estrutura do look (baixo+cima ou
   vestido sozinho, nunca vestido com peça de baixo, acessórios opcionais),
   coerência de formalidade, coordenação de cor com bom senso real (neutros como
   base, combinações ativas), adequação ao clima, e 2 a 3 looks variados entre si
   com justificativa curta e editorial.

4. **Formato de saída estruturado.** JSON exclusivo, schema fixo documentado no
   código, compatível com o que o frontend já renderiza. Parse com tratamento de
   erro robusto: texto fora do JSON, JSON malformado ou id inexistente no
   subconjunto = falha da chamada, nunca quebra da aplicação.

5. **Contexto por chamada.** Subconjunto filtrado em JSON compacto (nome,
   categoria, cores, estampa, formalidade), clima do dia, e resumo dos looks
   recentes de `looks_history` para evitar repetir a combinação anterior.

6. **Configuração da chamada.** Consistência acima de criatividade. Limite de
   tokens de saída condizente. Retry com backoff para falhas transitórias
   (poucas tentativas, não um loop agressivo).

7. **Degradação graciosa.** Falha após os retries retorna um estado claro de
   "não foi possível gerar o look agora", que a rota traduz numa mensagem
   elegante. A lógica antiga por regras pode ser removida — não precisa
   sobreviver como fallback. Nunca um 500 cru para o usuário.

8. **Persistência.** `looks_history` continua gravando como hoje, incluindo os
   looks vindos da API.

9. **Logging e custo.** Registrar tokens de entrada e saída por geração. Sem
   controle de quota nesta fase.

10. **Testes.** Pré-filtro isolado (não pode regredir), parse com JSON válido /
    malformado / id inexistente, e degradação graciosa. SDK mockado na suíte
    padrão; um teste opcional de chamada real, marcado para rodar manualmente.

11. **Validação final com a API real** contra o guarda-roupa do banco, em dois
    cenários de clima, mostrando looks, justificativas, tokens e custo.

12. **README** documentando a nova dependência paga, configuração da chave,
    modelo usado, e removendo menções desatualizadas a "geração sem custo".

## Decisões tomadas no planejamento

### D1 — Modelo e controle de geração
O spec original pedia `claude-sonnet-5` com `temperature` entre 0.4 e 0.6.
**Verificado contra a API real:** `temperature` retorna HTTP 400
(`"temperature is deprecated for this model"`) tanto em `claude-sonnet-5` quanto
em `claude-opus-5`. O controle equivalente hoje é `output_config.effort`.

**Decisão do usuário:** usar `claude-opus-5`. O parâmetro `temperature` sai;
`output_config.effort="medium"` ocupa seu lugar como controle de profundidade e
custo. `ANTHROPIC_MODEL` continua sendo a variável de troca.

### D2 — Ocasião no pré-filtro
`occasions.py` tem hoje dois filtros determinísticos por ocasião: categorias
PROIBIDAS (inviolável — nada de blazer para academia) e filtro de REGISTRO por
formalidade.

**Decisão do usuário:** manter apenas as **categorias proibidas** no pré-filtro.
O filtro de registro por formalidade sai — passa a ser julgamento do modelo, que
recebe a ocasião no prompt. `occasions.py` permanece no projeto como fonte das
categorias proibidas e dos rótulos de ocasião.

### D3 — Saída estruturada
**Decisão do usuário:** além da instrução de JSON no system prompt (item 4),
ativar também `output_config.format` com `json_schema`. A camada de parse robusto
e seus testes continuam existindo como rede de segurança — o schema não protege
contra id inexistente nem contra estrutura de look inválida.

**Verificado contra a API real:** `output_config` aceita `effort` e `format`
simultaneamente. O schema **não** aceita `minItems`/`maxItems` (HTTP 400: "For
'array' type, property 'maxItems' is not supported"). Cardinalidade (2 a 3 looks,
núcleo não repetido) tem de vir do system prompt e da validação em código.

### D4 — Guarda-roupa da validação final
**Decisão do usuário:** `teste@gmail.com` (21 peças). O outro usuário do banco
tem 2 peças, insuficiente para compor.
