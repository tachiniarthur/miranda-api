# Look do dia via API do Claude — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir a composição de look por regras determinísticas em `app/services/ai/look_generation.py` por uma chamada à API do Claude, mantendo o pré-filtro climático gratuito como primeira etapa.

**Architecture:** Três camadas novas e separadas. `look_prompt.py` guarda o conteúdo (o manual de estilo da Miranda, o schema JSON e a montagem da mensagem de usuário). `claude_client.py` guarda o transporte (SDK, retry com backoff, log de tokens) e não sabe nada sobre moda. `look_generation.py` continua sendo o ponto de entrada com a mesma assinatura pública: pré-filtra o guarda-roupa por clima, chama o transporte, valida a resposta contra o subconjunto enviado e degrada graciosamente quando algo falha.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic 2, SDK `anthropic==1.0.0`, pytest 9.

**Spec:** `docs/superpowers/specs/2026-08-21-look-do-dia-via-claude-api.md`

## Global Constraints

- **Modelo padrão:** `claude-opus-5`, lido de `ANTHROPIC_MODEL`. Nunca hardcoded numa chamada.
- **`temperature` é proibido.** Retorna HTTP 400 neste modelo (verificado contra a API). Use `output_config.effort="medium"`.
- **`minItems`/`maxItems` são proibidos no JSON schema.** Retornam HTTP 400 (verificado). Cardinalidade vem do system prompt e da validação em código.
- **Chave nunca no código.** Só `ANTHROPIC_API_KEY` via `.env`. O `.env` está no `.gitignore`; o `.env.example` é versionado e nunca recebe a chave real.
- **A aplicação sobe sem a chave.** `ANTHROPIC_API_KEY` tem default `""` e a ausência degrada graciosamente — nunca falha no boot, ao contrário de `JWT_SECRET_KEY`.
- **Nunca um 500 cru.** Qualquer falha da API vira `looks=[]` + `note` explicativa, HTTP 200.
- **Comentários e mensagens de usuário em português**, no mesmo registro editorial do resto do projeto. O código existente comenta o *porquê*, não o *o quê* — siga isso.
- **A análise de peça não é tocada.** `clothing_analysis.py`, `fashion_clip.py`, `color_extraction.py`, `labels.py`, `rules.py` ficam intactos.
- **Papéis exibidos ao usuário** (valores exatos, o frontend renderiza verbatim): `peça de baixo`, `peça de cima`, `sobreposição`, `peça única`, `calçado`, `acessório`.
- **`label` de look é só o numeral romano** — `"I"`, `"II"`, `"III"`. O frontend já imprime a palavra "Look" ao lado (`app/look/page.tsx:495-497`).
- **Preços para o log de custo:** Opus 5 = US$ 5,00 / MTok de entrada, US$ 25,00 / MTok de saída.

---

### Task 1: Dependência e configuração

**Files:**
- Modify: `requirements.txt`
- Modify: `app/core/config.py:105-120` (bloco "IA (análise de peça, self-hosted)" — adicionar um bloco novo depois dele)
- Modify: `.env`
- Modify: `.env.example`
- Test: `tests/test_anthropic_config.py`

**Interfaces:**
- Consumes: nada.
- Produces: `settings.ANTHROPIC_API_KEY: str`, `settings.ANTHROPIC_MODEL: str`, `settings.ANTHROPIC_MAX_OUTPUT_TOKENS: int`, `settings.ANTHROPIC_EFFORT: str`, `settings.ANTHROPIC_MAX_ATTEMPTS: int`, `settings.ANTHROPIC_TIMEOUT_SECONDS: float`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_anthropic_config.py`:

```python
"""
A configuração da API do Claude tem uma regra própria: a aplicação DEVE subir
sem a chave.

Isso é deliberado e diferente do JWT_SECRET_KEY, que derruba o boot quando é
fraco. Uma chave de JWT ausente torna a autenticação forjável em silêncio; uma
chave da Anthropic ausente apenas desliga a geração de look, que já sabe
degradar com uma mensagem clara. Derrubar a API inteira por isso trocaria uma
funcionalidade quebrada por um serviço fora do ar.
"""

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    """Instancia Settings sem depender do .env real da máquina."""
    base = {
        "DATABASE_URL": "postgresql+psycopg2://u:p@localhost:5432/db",
        "JWT_SECRET_KEY": "x" * 48,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_boots_without_an_anthropic_key():
    s = _settings()
    assert s.ANTHROPIC_API_KEY == ""


def test_model_defaults_to_opus_5():
    s = _settings()
    assert s.ANTHROPIC_MODEL == "claude-opus-5"


def test_model_can_be_swapped_without_touching_code():
    s = _settings(ANTHROPIC_MODEL="claude-sonnet-5")
    assert s.ANTHROPIC_MODEL == "claude-sonnet-5"


def test_call_budget_defaults_are_conservative():
    s = _settings()
    assert s.ANTHROPIC_MAX_OUTPUT_TOKENS == 4000
    assert s.ANTHROPIC_EFFORT == "medium"
    assert s.ANTHROPIC_MAX_ATTEMPTS == 3
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_anthropic_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'ANTHROPIC_API_KEY'`

- [ ] **Step 3: Adicionar as variáveis em `app/core/config.py`**

Insira este bloco logo depois de `FASHION_CLIP_THRESHOLD_FORMALIDADE: float = 0.60` e antes do bloco `# ── Storage local ──`:

```python
    # ── IA (composição de look, API paga da Anthropic) ────────────────
    # Diferente da análise de peça — que é self-hosted e gratuita —, a
    # composição do look do dia chama a API do Claude e CUSTA DINHEIRO por
    # geração. Ver README, seção 12.
    #
    # A chave fica vazia por padrão de propósito: a aplicação DEVE subir sem
    # ela. Sem chave, a geração de look devolve uma nota explicando que não
    # foi possível gerar agora — o resto da API (guarda-roupa, autenticação,
    # análise de peça) continua funcionando normalmente. Derrubar o boot aqui
    # trocaria uma funcionalidade indisponível por um serviço fora do ar.
    ANTHROPIC_API_KEY: str = ""

    # Identificador do modelo, isolado numa variável para poder ser trocado
    # sem tocar em código. Preços (US$ por milhão de tokens) em
    # `services/ai/claude_client.py`; ao trocar de modelo, atualize-os lá.
    ANTHROPIC_MODEL: str = "claude-opus-5"

    # Teto de tokens de saída. O JSON de 3 looks fica na casa dos 600 tokens;
    # 4000 dá folga para o raciocínio adaptativo do modelo sem desperdiçar.
    ANTHROPIC_MAX_OUTPUT_TOKENS: int = 4000

    # Profundidade de raciocínio: low | medium | high | xhigh | max.
    #
    # Ocupa o lugar do antigo `temperature`, que os modelos atuais REJEITAM com
    # HTTP 400 ("temperature is deprecated for this model"). O objetivo aqui é
    # consistência e bom senso, não criatividade dispersiva — "medium" entrega
    # isso sem pagar o preço de "high" numa tarefa de escopo pequeno.
    ANTHROPIC_EFFORT: str = "medium"

    # Tentativas TOTAIS por geração (não tentativas adicionais). Cobre falha de
    # rede, rate limit e resposta impossível de interpretar. Poucas de
    # propósito: o usuário está esperando na tela.
    ANTHROPIC_MAX_ATTEMPTS: int = 3

    # Timeout por tentativa, em segundos.
    ANTHROPIC_TIMEOUT_SECONDS: float = 60.0
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_anthropic_config.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Adicionar o SDK ao `requirements.txt`**

Substitua o cabeçalho da seção de IA para separar o que é gratuito do que é pago. Localize a linha `# ── IA self-hosted (análise de peça) — SEM nenhuma API paga ───────────` e adicione, ao FINAL do arquivo, o bloco novo:

```
# ── IA paga (composição do look do dia) ──────────────────────────────
# SDK oficial da Anthropic. A composição do look do dia (POST /api/looks/generate)
# chama a API do Claude e CUSTA DINHEIRO por geração — ver README, seção 12.
# Exige ANTHROPIC_API_KEY no .env; sem ela a geração degrada com uma nota e o
# resto da API continua funcionando.
anthropic==1.0.0
```

- [ ] **Step 6: Instalar e verificar**

Run:
```bash
.venv/bin/python -m pip install anthropic==1.0.0
.venv/bin/python -c "import anthropic; print(anthropic.__version__)"
```
Expected: `1.0.0`

> **Nota para quem executa:** os shebangs dos scripts em `.venv/bin/` apontam
> para um caminho antigo (a pasta do projeto foi movida). Use sempre
> `.venv/bin/python -m <módulo>` em vez de `.venv/bin/<script>`.

- [ ] **Step 7: Escrever a chave no `.env`**

O bloco abaixo **já foi escrito no `.env`** durante o planejamento, com a chave
real fornecida pelo dono do projeto. Confira que ele está lá:

```bash
grep -c "^ANTHROPIC_API_KEY=sk-ant-" .env   # deve imprimir 1
```

Se estiver faltando, peça a chave ao dono do projeto e acrescente ao final de
`.env` (NUNCA ao `.env.example`, que é versionado):

```
# ── IA paga: composição do look do dia (API da Anthropic) ────────────
# A geração de look CUSTA DINHEIRO por chamada. Sem esta chave a aplicação sobe
# normalmente e a geração devolve uma nota explicando que não foi possível.
ANTHROPIC_API_KEY=<a chave, começando com sk-ant-api03->
ANTHROPIC_MODEL=claude-opus-5
```

> ⚠️ A chave nunca entra neste plano, no README, no `.env.example` nem em
> nenhum arquivo versionado. Ela vive só no `.env`, que está no `.gitignore`.

- [ ] **Step 8: Documentar no `.env.example` SEM a chave real**

Acrescente ao final de `.env.example`:

```
# ── IA paga: composição do look do dia (API da Anthropic) ────────────
# ATENÇÃO: diferente da análise de peça (self-hosted e gratuita), a geração do
# look do dia chama a API do Claude e CUSTA DINHEIRO por requisição.
#
# Crie uma chave em https://console.anthropic.com/settings/keys e cole abaixo.
# Deixar em branco NÃO derruba a aplicação: a geração de look passa a devolver
# uma nota explicando que não foi possível gerar agora.
ANTHROPIC_API_KEY=

# Modelo usado na composição. Trocar aqui basta — nenhum código referencia o
# identificador diretamente. Ao trocar, revise os preços em
# services/ai/claude_client.py, que alimentam o log de custo estimado.
ANTHROPIC_MODEL=claude-opus-5

# Teto de tokens de saída por geração.
ANTHROPIC_MAX_OUTPUT_TOKENS=4000

# Profundidade de raciocínio: low | medium | high | xhigh | max.
# Ocupa o lugar do antigo `temperature`, que os modelos atuais rejeitam com
# HTTP 400. "medium" prioriza consistência sobre criatividade.
ANTHROPIC_EFFORT=medium

# Tentativas totais por geração (rede, rate limit, resposta ilegível).
ANTHROPIC_MAX_ATTEMPTS=3
ANTHROPIC_TIMEOUT_SECONDS=60.0
```

- [ ] **Step 9: Confirmar que o `.env` não é versionado**

Run: `git check-ignore -v .env`
Expected: `.gitignore:6:.env	.env` — se NÃO imprimir nada, PARE e corrija o `.gitignore` antes de seguir.

- [ ] **Step 10: Commit**

```bash
git add requirements.txt app/core/config.py .env.example tests/test_anthropic_config.py
git commit -m "feat(look): adiciona SDK e configuração da API do Claude"
```

> `.env` não entra no commit — é ignorado e contém a chave real.

---
### Task 2: O manual de estilo da Miranda (system prompt, schema e contexto)

Esta é a peça central da migração. O arquivo guarda **conteúdo**, não
comportamento: o manual que a Miranda segue, o schema que a resposta obedece e a
montagem da mensagem de usuário. Nenhuma chamada de rede acontece aqui.

**Files:**
- Create: `app/services/ai/look_prompt.py`
- Test: `tests/test_look_prompt.py`

**Interfaces:**
- Consumes: `settings` (não usa), nada de tarefas anteriores.
- Produces:
  - `MIRANDA_SYSTEM_PROMPT: str`
  - `LOOK_RESPONSE_SCHEMA: dict[str, Any]`
  - `ROLE_BOTTOM/ROLE_TOP/ROLE_OUTER/ROLE_DRESS/ROLE_FOOTWEAR/ROLE_ACCESSORY: str`
  - `VALID_ROLES: frozenset[str]`
  - `build_user_message(pieces: list[dict[str, Any]], weather: dict[str, Any], ocasiao_label: str, recent_item_ids: list[list[str]]) -> str`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_look_prompt.py`:

```python
"""
Testes do manual de estilo e da montagem de contexto.

O texto do system prompt é conteúdo editorial e não se testa palavra por
palavra — testar a prosa engessaria justamente a parte que mais vai ser
ajustada. O que se testa aqui é o CONTRATO: que o manual cobre os temas
obrigatórios do spec, que o schema é aceitável pela API (sem minItems/maxItems)
e que a mensagem de usuário carrega tudo que o modelo precisa para decidir.
"""

import json

from app.services.ai.look_prompt import (
    LOOK_RESPONSE_SCHEMA,
    MIRANDA_SYSTEM_PROMPT,
    VALID_ROLES,
    build_user_message,
)


# ── O manual cobre os temas obrigatórios ────────────────────────────────────
def test_system_prompt_covers_every_required_topic():
    prompt = MIRANDA_SYSTEM_PROMPT.lower()
    for topic in (
        "vestido",        # estrutura: vestido nunca com peça de baixo
        "formalidade",    # coerência de registro
        "neutro",         # cor: neutros como base
        "chuva",          # adequação ao clima
        "json",           # formato de saída
    ):
        assert topic in prompt, f"o manual não fala de {topic}"


def test_system_prompt_forbids_dress_with_a_bottom():
    assert "nunca acompanha peça de baixo" in MIRANDA_SYSTEM_PROMPT


def test_system_prompt_asks_for_two_or_three_varied_looks():
    assert "de 2 a 3 looks" in MIRANDA_SYSTEM_PROMPT
    assert "não repita a mesma peça de cima" in MIRANDA_SYSTEM_PROMPT.lower()


def test_system_prompt_lists_every_role_the_frontend_renders():
    for role in VALID_ROLES:
        assert role in MIRANDA_SYSTEM_PROMPT, f"papel ausente do manual: {role}"


# ── O schema é aceitável pela API ───────────────────────────────────────────
def test_schema_avoids_array_bounds_the_api_rejects():
    """
    A API responde HTTP 400 a `minItems`/`maxItems` em schema de saída
    ("For 'array' type, property 'maxItems' is not supported"). A cardinalidade
    vem do system prompt e da validação em código — nunca do schema.
    """
    raw = json.dumps(LOOK_RESPONSE_SCHEMA)
    assert "minItems" not in raw
    assert "maxItems" not in raw


def test_schema_pins_roles_to_the_values_the_frontend_renders():
    item_props = (
        LOOK_RESPONSE_SCHEMA["properties"]["looks"]["items"]["properties"]["items"]
        ["items"]["properties"]
    )
    assert set(item_props["role"]["enum"]) == set(VALID_ROLES)


def test_schema_closes_every_object():
    """`additionalProperties: false` impede campo inventado virar ruído."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(LOOK_RESPONSE_SCHEMA)


# ── A mensagem de usuário carrega o contexto ────────────────────────────────
_PIECES = [
    {
        "id": "aaa",
        "name": "Camisa oxford azul",
        "category": "camisa",
        "cor_primaria": "azul",
        "cor_secundaria": None,
        "estampa": "liso",
        "formalidade": "smart_casual",
        "peso_termico": "leve",
        "serve_chuva": False,
    },
    {
        "id": "bbb",
        "name": "Calça alfaiataria preta",
        "category": "calca",
        "cor_primaria": "preto",
        "cor_secundaria": None,
        "estampa": "liso",
        "formalidade": "social",
        "peso_termico": "medio",
        "serve_chuva": False,
    },
]
_WEATHER = {"temperatura_min": 16.0, "temperatura_max": 24.0, "condicoes": ["sol", "vento"]}


def test_user_message_carries_every_piece_with_its_id():
    msg = build_user_message(_PIECES, _WEATHER, "Trabalho", [])
    assert "aaa" in msg and "bbb" in msg
    assert "Camisa oxford azul" in msg
    assert "smart_casual" in msg


def test_user_message_carries_the_weather_and_the_occasion():
    msg = build_user_message(_PIECES, _WEATHER, "Trabalho", [])
    assert "16" in msg and "24" in msg
    assert "sol" in msg and "vento" in msg
    assert "Trabalho" in msg


def test_user_message_carries_recent_looks_to_avoid_repeating_them():
    msg = build_user_message(_PIECES, _WEATHER, "Trabalho", [["aaa", "bbb"]])
    assert "recente" in msg.lower()
    assert "aaa" in msg


def test_user_message_omits_the_recent_section_when_there_is_no_history():
    msg = build_user_message(_PIECES, _WEATHER, "Trabalho", [])
    assert "recente" not in msg.lower()


def test_user_message_is_compact_json_without_indentation():
    """
    Cada espaço de indentação é um token pago em toda geração. O JSON vai
    compacto de propósito.
    """
    msg = build_user_message(_PIECES, _WEATHER, "Trabalho", [])
    assert '\n    "' not in msg
    assert '", "' in msg or '","' in msg
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_look_prompt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai.look_prompt'`

- [ ] **Step 3: Escrever `app/services/ai/look_prompt.py`**

```python
"""
O manual de estilo da Miranda — o conteúdo enviado à API do Claude a cada
composição de look.

Este módulo é deliberadamente SEM COMPORTAMENTO: guarda o texto do manual, o
schema que a resposta obedece e a montagem da mensagem de usuário. Quem fala com
a rede é `claude_client.py`; quem decide o que fazer com a resposta é
`look_generation.py`. A separação existe porque estas três coisas mudam por
motivos diferentes — o manual muda quando a Miranda erra de gosto, o transporte
muda quando o SDK muda, a orquestração muda quando o produto muda.

── Por que um manual longo ─────────────────────────────────────────────────
O modelo já sabe moda. O que ele não sabe é o que a MIRANDA considera um erro:
que esporte com social é inaceitável aqui, que a justificativa tem uma voz
específica, que um vestido nunca ganha companhia embaixo. O manual não ensina
moda — ele fixa o julgamento da casa, para que duas gerações no mesmo
guarda-roupa não pareçam vir de duas editoras diferentes.

── Por que o texto não é testado palavra por palavra ───────────────────────
Os testes conferem que os TEMAS obrigatórios estão cobertos, não a prosa.
Travar a redação engessaria justamente a parte que mais vai ser ajustada com o
uso.
"""

from __future__ import annotations

import json
from typing import Any

# ── Papéis exibidos ao usuário ──────────────────────────────────────────────
# Estes valores são renderizados VERBATIM pelo frontend (app/look/page.tsx, o
# `<span>{piece.role}</span>`). Mudar uma string aqui muda o que a pessoa lê na
# tela — e o schema abaixo os fixa como enum para o modelo não inventar um sexto.
ROLE_BOTTOM = "peça de baixo"
ROLE_TOP = "peça de cima"
ROLE_OUTER = "sobreposição"
ROLE_DRESS = "peça única"
ROLE_FOOTWEAR = "calçado"
ROLE_ACCESSORY = "acessório"

VALID_ROLES: frozenset[str] = frozenset(
    {ROLE_BOTTOM, ROLE_TOP, ROLE_OUTER, ROLE_DRESS, ROLE_FOOTWEAR, ROLE_ACCESSORY}
)

# Numerais aceitos como rótulo. O frontend imprime a palavra "Look" ao lado, por
# isso o rótulo é SÓ o numeral. O código reatribui por posição de qualquer forma
# (ver `look_generation._parse_reply`); o enum existe para o modelo não devolver
# "Look 1", "Primeiro" ou "A".
LOOK_LABELS: tuple[str, ...] = ("I", "II", "III")


MIRANDA_SYSTEM_PROMPT = """\
Você é Miranda: editora de moda de altíssimo padrão. Sua função é olhar um \
guarda-roupa real e determinar o que a pessoa veste hoje.

## Tom
Frio, preciso, decisivo. Você não sugere: você determina. Nunca escreva \
"talvez", "você pode", "que tal", "fica a seu critério". Nunca elogie a pessoa, \
nunca peça desculpas, nunca faça perguntas. Nenhum emoji, nenhuma exclamação.

O tom é frio, mas o conteúdo é técnico e correto. Elegância de frase nunca \
justifica um erro de moda: se a frase soa bem e a combinação está errada, a \
combinação manda. Você é uma profissional antes de ser um estilo de escrita.

## Estrutura de um look
Regras invioláveis:
- Um look é UMA peça de baixo mais UMA peça de cima, OU UMA peça única \
(vestido) sozinha.
- Vestido nunca acompanha peça de baixo — nem calça, nem saia. Vestido também \
não acompanha outra peça de cima.
- Nunca duas peças de baixo, nunca duas peças de cima no mesmo look.
- Sobreposição (blazer, casaco) é opcional: no máximo uma por look, e só quando \
o clima ou a ocasião a justificarem. Sobreposição pesada em dia quente é erro.
- Calçado entra sempre que houver um adequado no conjunto.
- Acessório (cachecol, bolsa, cinto) é complemento opcional: no máximo um por \
look, e só quando somar. Cachecol pede frio ou vento. Em dúvida, não use.
- Cada peça aparece no máximo uma vez dentro do mesmo look.

## Formalidade
A escala é: esporte, casual, smart casual, social.
- As peças de um mesmo look ficam no mesmo degrau ou em degraus vizinhos.
- Esporte com social é o erro clássico e é proibido: legging com blazer de \
alfaiataria não é um look, é um acidente.
- Casual com smart casual funciona e é a base da maior parte do vestir real.
- Peça sem formalidade declarada é curinga: julgue pelo nome e pela categoria.
- A ocasião informada define o alvo. Puxe o look para esse alvo com o que \
existe. Se o guarda-roupa não alcança o registro pedido, chegue o mais perto \
possível e diga isso na nota — nunca force uma peça errada para cumprir o alvo.

## Cor
Não basta evitar conflito: componha ativamente.
- Neutros (preto, branco, off-white, cinza, bege, caramelo, marrom, areia, \
creme, nude, azul-marinho) são a base. Dois neutros bem escolhidos valem mais \
que uma cor forte mal colocada.
- No máximo UMA cor forte por look. Duas cores fortes diferentes competem entre \
si: vermelho com verde, laranja com rosa, azul-royal com mostarda.
- Havendo uma cor forte, ancore-a em neutros. A cor conduz, o resto obedece.
- Tons da mesma família em intensidades diferentes (camel com marrom, \
cinza-claro com grafite) leem como intenção, não como acaso.
- Estampa é ponto focal. Estampa com estampa só se uma for discreta e as \
escalas forem claramente diferentes. Na dúvida, uma estampa por look.
- Preto com azul-marinho exige intenção; separe-os quando houver alternativa.

## Clima
- O conjunto que você recebe já foi filtrado por peso térmico compatível com o \
dia. Confie nele: as peças listadas cabem na temperatura informada.
- Chuva: prefira peças com "serve_chuva": true nas posições expostas — \
sobreposição e calçado. Não mande ninguém para a chuva de camurça.
- Vento: peça uma sobreposição mesmo com temperatura amena.
- Sol e calor: nada de sobreposição pesada; privilegie peças leves.
- Frio: sobreposição é obrigatória se houver uma disponível.

## Variedade entre os looks
Componha de 2 a 3 looks. Sempre que o conjunto permitir:
- Não repita a mesma peça de cima nem a mesma peça de baixo entre dois looks.
- Não repita a mesma peça única entre dois looks.
- Se o guarda-roupa for pequeno demais para variar o núcleo, componha MENOS \
looks em vez de repetir o mesmo núcleo com um acessório trocado. Dois looks \
distintos valem mais que três quase iguais.
- Recebendo os looks recentes da pessoa, não repita o núcleo que acabou de ser \
sugerido a ela.

## Justificativa
Uma frase por look, no máximo duas. Curta, editorial, decisiva. Ela nomeia a \
peça que conduz o look e diz por que ela conduz hoje — clima, ocasião ou cor.

Modelo de tom, para calibrar a voz. Não copie estas frases, escreva as suas:
- "Para o sol, camisa oxford azul conduz. O resto obedece."
- "Alfaiataria em cima, conforto embaixo. É o que a reunião pede."
- "O vestido resolve sozinho. Acrescentar seria estragar."

Nunca descreva o óbvio ("calça preta com camisa branca é uma combinação \
clássica"). Diga o que a escolha faz.

## Saída
Responda EXCLUSIVAMENTE com um objeto JSON válido. Nenhum texto antes ou \
depois, nenhuma cerca de código, nenhum comentário.

Formato:
{"looks": [{"label": "I", "items": [{"item_id": "<id exato recebido>", \
"role": "<papel>"}], "commentary": "<a justificativa>"}], "note": null}

- "label": numeral romano na ordem em que você apresenta os looks — "I", "II", \
"III". Nada além disso.
- "item_id": copie LITERALMENTE um id do conjunto recebido. Nunca invente um \
id, nunca abrevie, nunca use o nome da peça no lugar do id.
- "role": exatamente um destes valores — "peça de baixo", "peça de cima", \
"sobreposição", "peça única", "calçado", "acessório".
- "note": uma frase quando o guarda-roupa limitou a composição (poucas peças, \
registro inalcançável, nenhum calçado adequado). null quando não houver \
ressalva. A nota informa; não se desculpa.
"""


# ── Schema da resposta ──────────────────────────────────────────────────────
# Enviado em `output_config.format` para a API GARANTIR JSON bem formado no
# formato certo — o que torna o caminho "JSON malformado" raro, não impossível.
#
# ⚠️ `minItems` e `maxItems` NÃO são aceitos aqui: a API responde HTTP 400
# ("For 'array' type, property 'maxItems' is not supported"). Por isso a
# cardinalidade — de 2 a 3 looks, núcleo não repetido — é responsabilidade do
# system prompt e da validação em `look_generation._parse_reply`.
#
# O schema também não protege contra id inexistente nem contra estrutura de look
# inválida (vestido com calça). Essas duas continuam sendo verificadas em código.
LOOK_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "looks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": list(LOOK_LABELS)},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {"type": "string"},
                                "role": {"type": "string", "enum": sorted(VALID_ROLES)},
                            },
                            "required": ["item_id", "role"],
                            "additionalProperties": False,
                        },
                    },
                    "commentary": {"type": "string"},
                },
                "required": ["label", "items", "commentary"],
                "additionalProperties": False,
            },
        },
        "note": {"type": ["string", "null"]},
    },
    "required": ["looks", "note"],
    "additionalProperties": False,
}


# ── Mensagem de usuário ─────────────────────────────────────────────────────
# Campos da peça enviados à API. `peso_termico` e `serve_chuva` entram mesmo o
# pré-filtro já tendo usado o primeiro: o modelo precisa deles para escolher a
# posição de cada peça (o que vai por cima num dia de vento, o que enfrenta
# chuva), não só para saber se a peça cabe no dia.
_PIECE_FIELDS = (
    ("name", "nome"),
    ("category", "categoria"),
    ("cor_primaria", "cor_primaria"),
    ("cor_secundaria", "cor_secundaria"),
    ("estampa", "estampa"),
    ("formalidade", "formalidade"),
    ("peso_termico", "peso_termico"),
    ("serve_chuva", "serve_chuva"),
)


def _compact_piece(piece: dict[str, Any]) -> dict[str, Any]:
    """Reduz uma peça aos campos que importam para a decisão de moda.

    Campos nulos são OMITIDOS: `"cor_secundaria": null` custa tokens em toda
    geração e não diz nada que a ausência já não diga.
    """
    out: dict[str, Any] = {"id": str(piece.get("id"))}
    for src, dst in _PIECE_FIELDS:
        value = piece.get(src)
        if value is not None:
            out[dst] = value
    return out


def build_user_message(
    pieces: list[dict[str, Any]],
    weather: dict[str, Any],
    ocasiao_label: str,
    recent_item_ids: list[list[str]],
) -> str:
    """
    Monta a mensagem de usuário de uma composição.

    Args:
        pieces: subconjunto JÁ filtrado pelo clima (ver `look_generation`).
        weather: `temperatura_min`, `temperatura_max` e a lista `condicoes`.
        ocasiao_label: rótulo legível da ocasião (ex.: "Jantar romântico").
        recent_item_ids: núcleos dos looks recentes do usuário, cada um como uma
            lista de ids. Lista vazia quando não há histórico.

    Returns:
        Texto pronto para o papel "user". O JSON vai COMPACTO (sem indentação):
        cada espaço é um token pago em toda geração.
    """
    condicoes = ", ".join(weather.get("condicoes") or []) or "sem particularidade"
    catalog = json.dumps(
        [_compact_piece(p) for p in pieces], ensure_ascii=False, separators=(",", ":")
    )

    blocks = [
        f"OCASIÃO: {ocasiao_label}",
        (
            f"CLIMA DO DIA: mínima {weather['temperatura_min']}°C, "
            f"máxima {weather['temperatura_max']}°C, condições: {condicoes}"
        ),
        f"GUARDA-ROUPA DISPONÍVEL (já filtrado pelo clima):\n{catalog}",
    ]

    if recent_item_ids:
        recent = json.dumps(recent_item_ids, ensure_ascii=False, separators=(",", ":"))
        blocks.append(
            "LOOKS RECENTES desta pessoa (não repita estas mesmas combinações de "
            f"núcleo):\n{recent}"
        )

    blocks.append("Componha os looks do dia.")
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_look_prompt.py -v`
Expected: PASS (12 testes)

- [ ] **Step 5: Commit**

```bash
git add app/services/ai/look_prompt.py tests/test_look_prompt.py
git commit -m "feat(look): manual de estilo da Miranda como system prompt"
```

---
### Task 3: Transporte — o cliente da API, uma tentativa por chamada

**Files:**
- Create: `app/services/ai/claude_client.py`
- Test: `tests/test_claude_client.py`

**Interfaces:**
- Consumes: `settings.ANTHROPIC_*` (Task 1); `LOOK_RESPONSE_SCHEMA` só como argumento do chamador.
- Produces:
  - `class LookApiError(Exception)` — base.
  - `class LookApiTransient(LookApiError)` — vale a pena tentar de novo.
  - `class LookApiFatal(LookApiError)` — não vale (chave inválida, modelo inexistente).
  - `@dataclass(frozen=True) class ClaudeUsage` com `input_tokens: int`, `output_tokens: int`, `model: str` e a propriedade `estimated_cost_usd: float`.
  - `@dataclass(frozen=True) class ClaudeReply` com `text: str` e `usage: ClaudeUsage`.
  - `def request_composition(system: str, user_message: str, schema: dict[str, Any]) -> ClaudeReply` — **uma** tentativa, sem retry.
  - `def reset_client_cache() -> None` — usada só por testes.

**Design:** este módulo não sabe nada sobre moda e não tenta de novo. Uma
chamada, um resultado ou uma exceção classificada. O laço de retry vive em
`look_generation` (Task 5), onde ele também cobre falha de interpretação da
resposta — que é tão retentável quanto um 429 e não faria sentido tratar em
outro lugar.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_claude_client.py`:

```python
"""
Testes do transporte. Nenhum toca a rede: o SDK é substituído por dublês.

O que importa aqui é a CLASSIFICAÇÃO do erro — decidir o que merece nova
tentativa e o que não merece é a única regra de verdade deste módulo, e errar
nela custa dinheiro (retentar um 400 três vezes) ou disponibilidade (desistir de
um 429 na primeira).
"""

import logging

import anthropic
import httpx
import pytest

from app.services.ai import claude_client
from app.services.ai.claude_client import (
    ClaudeUsage,
    LookApiFatal,
    LookApiTransient,
    request_composition,
)


class _Usage:
    def __init__(self, i=100, o=200):
        self.input_tokens = i
        self.output_tokens = o


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text='{"looks":[],"note":null}', usage=None):
        self.content = [_Block(text)]
        self.usage = usage or _Usage()
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeClient:
    def __init__(self, outcome):
        self.messages = _FakeMessages(outcome)


@pytest.fixture(autouse=True)
def _clear_cache():
    claude_client.reset_client_cache()
    yield
    claude_client.reset_client_cache()


def _install(monkeypatch, outcome, api_key="sk-ant-test"):
    fake = _FakeClient(outcome)
    monkeypatch.setattr(claude_client.settings, "ANTHROPIC_API_KEY", api_key)
    monkeypatch.setattr(claude_client, "_build_client", lambda: fake)
    return fake


def _api_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": "x"}})
    return anthropic.APIStatusError("boom", response=response, body=None)


# ── Caminho feliz ───────────────────────────────────────────────────────────
def test_returns_the_text_of_the_first_text_block(monkeypatch):
    _install(monkeypatch, _Response(text='{"looks":[]}'))
    reply = request_composition("sys", "user", {"type": "object"})
    assert reply.text == '{"looks":[]}'


def test_reports_token_usage(monkeypatch):
    _install(monkeypatch, _Response(usage=_Usage(1234, 567)))
    reply = request_composition("sys", "user", {"type": "object"})
    assert reply.usage.input_tokens == 1234
    assert reply.usage.output_tokens == 567


def test_sends_effort_and_schema_but_never_temperature(monkeypatch):
    """
    `temperature` retorna HTTP 400 nos modelos atuais. Este teste é a trava
    que impede alguém de "restaurar" o parâmetro achando que foi esquecido.
    """
    schema = {"type": "object", "properties": {}}
    fake = _install(monkeypatch, _Response())
    request_composition("sys", "user", schema)

    sent = fake.messages.calls[0]
    assert "temperature" not in sent
    assert sent["output_config"]["effort"] == claude_client.settings.ANTHROPIC_EFFORT
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"] is schema
    assert sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "user"}]


def test_logs_tokens_and_estimated_cost(monkeypatch, caplog):
    _install(monkeypatch, _Response(usage=_Usage(1_000_000, 1_000_000)))
    with caplog.at_level(logging.INFO, logger="miranda.ai.claude_client"):
        request_composition("sys", "user", {"type": "object"})
    logged = caplog.text
    assert "input_tokens=1000000" in logged
    assert "output_tokens=1000000" in logged
    assert "30.0" in logged  # US$ 5 de entrada + US$ 25 de saída


# ── Classificação de erro ───────────────────────────────────────────────────
def test_missing_key_is_fatal_and_never_touches_the_network(monkeypatch):
    fake = _install(monkeypatch, _Response(), api_key="")
    with pytest.raises(LookApiFatal):
        request_composition("sys", "user", {"type": "object"})
    assert fake.messages.calls == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
def test_rate_limit_and_server_errors_are_transient(monkeypatch, status):
    _install(monkeypatch, _api_error(status))
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


def test_connection_errors_are_transient(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    _install(monkeypatch, anthropic.APIConnectionError(request=request))
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_fatal(monkeypatch, status):
    _install(monkeypatch, _api_error(status))
    with pytest.raises(LookApiFatal):
        request_composition("sys", "user", {"type": "object"})


def test_a_response_without_text_is_transient(monkeypatch):
    """Resposta cortada por max_tokens ou recusa: vazia, mas vale retentar."""
    response = _Response()
    response.content = []
    _install(monkeypatch, response)
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


# ── Custo estimado ──────────────────────────────────────────────────────────
def test_cost_uses_the_price_table_of_the_model():
    usage = ClaudeUsage(input_tokens=200_000, output_tokens=40_000, model="claude-opus-5")
    # 0,2 MTok × US$5 + 0,04 MTok × US$25 = 1,00 + 1,00
    assert usage.estimated_cost_usd == pytest.approx(2.0)


def test_cost_of_an_unknown_model_is_zero_not_a_crash():
    """
    Trocar ANTHROPIC_MODEL não pode derrubar a geração só porque o preço do
    modelo novo ainda não foi cadastrado. O custo vira 0.0 e o log avisa.
    """
    usage = ClaudeUsage(input_tokens=1000, output_tokens=1000, model="modelo-inexistente")
    assert usage.estimated_cost_usd == 0.0
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_claude_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai.claude_client'`

- [ ] **Step 3: Escrever `app/services/ai/claude_client.py`**

```python
"""
Transporte da composição de look: uma chamada à API do Claude, um resultado.

Este módulo não sabe nada sobre moda e NÃO tenta de novo. Ele faz uma chamada,
classifica o que deu errado e registra quanto custou. O laço de retry vive em
`look_generation`, onde ele também cobre a falha de INTERPRETAÇÃO da resposta —
que é tão retentável quanto um 429 e não faria sentido tratar noutro lugar.

── Por que a classificação do erro é a regra central daqui ─────────────────
Errar nela custa dos dois lados: retentar um 400 três vezes é dinheiro jogado
fora numa chamada que nunca vai passar; desistir de um 429 na primeira é
indisponibilidade gratuita. Por isso a separação entre `LookApiTransient` e
`LookApiFatal` é explícita e testada caso a caso, em vez de um `except Exception`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from app.core.config import settings

logger = logging.getLogger("miranda.ai.claude_client")


# ── Erros ───────────────────────────────────────────────────────────────────
class LookApiError(Exception):
    """Falha ao falar com a API do Claude."""


class LookApiTransient(LookApiError):
    """Falha que pode passar numa nova tentativa (rede, rate limit, 5xx)."""


class LookApiFatal(LookApiError):
    """Falha que NÃO passa em nova tentativa (chave inválida, modelo inexistente)."""


# ── Preços ──────────────────────────────────────────────────────────────────
# US$ por milhão de tokens: (entrada, saída). Alimentam apenas o log de custo
# estimado — nenhuma decisão do sistema depende deles.
#
# ⚠️ Ao trocar ANTHROPIC_MODEL para um modelo fora desta tabela, o custo passa a
# ser registrado como 0.0 e o log avisa. Acrescente a linha em vez de confiar no
# silêncio.
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass(frozen=True)
class ClaudeUsage:
    """Consumo de uma chamada. Existe para o dono do projeto acompanhar custo."""

    input_tokens: int
    output_tokens: int
    model: str

    @property
    def estimated_cost_usd(self) -> float:
        prices = MODEL_PRICES_USD_PER_MTOK.get(self.model)
        if prices is None:
            return 0.0
        price_in, price_out = prices
        return (
            self.input_tokens / 1_000_000 * price_in
            + self.output_tokens / 1_000_000 * price_out
        )


@dataclass(frozen=True)
class ClaudeReply:
    """Texto bruto devolvido pelo modelo, ainda não interpretado."""

    text: str
    usage: ClaudeUsage


# ── Cliente ─────────────────────────────────────────────────────────────────
_client: Optional[anthropic.Anthropic] = None


def _build_client() -> anthropic.Anthropic:
    """
    Constrói o cliente do SDK.

    `max_retries=0` é deliberado: o SDK tentaria 2 vezes por conta própria e,
    somado ao nosso laço de 3, daria até 9 chamadas por geração — um "loop
    agressivo" que o usuário esperando na tela pagaria em latência. As tentativas
    ficam num lugar só, visível e testável, em `look_generation`.
    """
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        max_retries=0,
        timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
    )


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def reset_client_cache() -> None:
    """Descarta o cliente memoizado. Usado por testes; não chame em produção."""
    global _client
    _client = None


def request_composition(
    system: str, user_message: str, schema: dict[str, Any]
) -> ClaudeReply:
    """
    Faz UMA chamada de composição de look. Não tenta de novo.

    Args:
        system: o manual de estilo (ver `look_prompt.MIRANDA_SYSTEM_PROMPT`).
        user_message: guarda-roupa filtrado, clima, ocasião e histórico recente.
        schema: JSON schema da resposta (ver `look_prompt.LOOK_RESPONSE_SCHEMA`).

    Returns:
        ClaudeReply com o texto bruto e o consumo da chamada.

    Raises:
        LookApiFatal: chave ausente/inválida, requisição malformada, modelo
            inexistente. Nova tentativa não ajuda.
        LookApiTransient: rede, timeout, rate limit, 5xx, resposta sem texto.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise LookApiFatal(
            "ANTHROPIC_API_KEY não está configurada. Defina-a no .env "
            "(ver .env.example) para habilitar a geração de look."
        )

    model = settings.ANTHROPIC_MODEL

    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=settings.ANTHROPIC_MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            # `effort` no lugar de `temperature`: os modelos atuais REJEITAM
            # `temperature` com HTTP 400. `format` faz a própria API garantir
            # JSON bem formado no formato certo — o que torna resposta
            # ilegível rara, não impossível (ver `_parse_reply`).
            output_config={
                "effort": settings.ANTHROPIC_EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise LookApiFatal(f"Credencial recusada pela API: {exc}") from exc
    except anthropic.NotFoundError as exc:
        raise LookApiFatal(f"Modelo '{model}' não encontrado: {exc}") from exc
    except anthropic.BadRequestError as exc:
        raise LookApiFatal(f"Requisição recusada pela API: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LookApiTransient(f"Rate limit da API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise LookApiTransient(f"Erro do servidor ({exc.status_code}): {exc}") from exc
        raise LookApiFatal(f"Erro da API ({exc.status_code}): {exc}") from exc
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
        raise LookApiTransient(f"Falha de rede ao chamar a API: {exc}") from exc

    usage = ClaudeUsage(
        input_tokens=getattr(response.usage, "input_tokens", 0),
        output_tokens=getattr(response.usage, "output_tokens", 0),
        model=model,
    )

    # Log de custo: é o único instrumento de acompanhamento nesta fase — não há
    # controle de quota (isso é uma fase à parte do projeto).
    cost = usage.estimated_cost_usd
    logger.info(
        "composição de look — modelo=%s input_tokens=%d output_tokens=%d "
        "custo_estimado_usd=%.6f",
        model,
        usage.input_tokens,
        usage.output_tokens,
        cost,
    )
    if cost == 0.0 and model not in MODEL_PRICES_USD_PER_MTOK:
        logger.warning(
            "Modelo '%s' não está na tabela de preços de claude_client; o custo "
            "estimado será sempre 0.0 até alguém acrescentá-lo.",
            model,
        )

    text = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"), ""
    )
    if not text.strip():
        raise LookApiTransient(
            f"A API respondeu sem texto (stop_reason="
            f"{getattr(response, 'stop_reason', '?')})."
        )

    return ClaudeReply(text=text, usage=usage)
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_claude_client.py -v`
Expected: PASS (16 testes)

- [ ] **Step 5: Commit**

```bash
git add app/services/ai/claude_client.py tests/test_claude_client.py
git commit -m "feat(look): cliente da API do Claude com log de tokens e custo"
```

---
### Task 4: Reescrever `look_generation.py` — pré-filtro preservado, motor de regras removido, API no lugar

Esta é a tarefa que remove código. `look_generation.py` sai de 1047 linhas para
cerca de 350: o pré-filtro climático permanece, tudo que compunha look por
regras de cor e formalidade e tudo que montava frase por template desaparece.

**Files:**
- Rewrite: `app/services/ai/look_generation.py` (arquivo inteiro)
- Rewrite: `tests/test_look_generation.py` (arquivo inteiro)

**O que é PRESERVADO de `look_generation.py`** (copie os corpos existentes, não
os reescreva de memória): `WeatherInfo`, `SuggestedLookItem`, `SuggestedLook`,
`DailyLookResult`, os conjuntos de categoria (`BOTTOMS`, `TOPS`, `OUTERS`,
`DRESSES`, `FOOTWEAR`, `SCARVES`, `ACCESSORIES`), as constantes de temperatura
(`TEMP_MIN_WEIGHT`, `TEMP_MAX_WEIGHT`, `COLD_MAX`, `MILD_MAX`, `BAND_*`,
`ACCEPTABLE_PESO`), `_reference_temp`, `_band_for`, `_thermal_prefilter`,
`_drop_forbidden`, `_partition`, `_have_core`, `MAX_LOOKS`.

**O que é REMOVIDO:** `_condition_flags`, `_color_family`, `_max_strong_families`,
`_colors_ok`, `_formality_ok`, `_look_formality_ok`, `_formality_distance`,
`_in_register`, `_occasion_score`, `_piece_priority`, `_register_filter`,
`_Base`, `_build_bases`, `_select_varied`, `_pick_complement`, `_assemble_look`,
`_compose_commentary`, `_seed_from`, `_roman`, todo o bloco de templates de
justificativa, `FORMALITY_RANK`, `NEUTRAL_COLOR_PREFIXES`,
`NEUTRAL_STRONG_LOOKALIKES`, `STRONG_COLOR_FAMILIES`, os pesos `OCCASION_*` /
`COMFORT_HEAVY_PENALTY` / `STATEMENT_COLOR_BONUS` / `LAYERING_THRESHOLD`,
`MIN_DESIRED_LOOKS`, e os imports `random` / `hashlib`.

**`occasions.py` não é tocado.** Continua sendo a fonte de
`forbidden_categories` (pré-filtro inviolável, decisão D2 do spec) e de
`profile.label` (rótulo legível enviado ao modelo). Os campos que só o motor de
regras usava — `formality_target`, `comfort_bias`, `color_discipline` etc. —
ficam onde estão, sem consumidor; removê-los é ruído fora do escopo desta
migração.

**Interfaces:**
- Consumes: `look_prompt.MIRANDA_SYSTEM_PROMPT`, `look_prompt.LOOK_RESPONSE_SCHEMA`, `look_prompt.build_user_message`, `look_prompt.VALID_ROLES`, `look_prompt.LOOK_LABELS`, `look_prompt.ROLE_*` (Task 2); `claude_client.request_composition`, `claude_client.LookApiTransient`, `claude_client.LookApiFatal` (Task 3); `settings.ANTHROPIC_MAX_ATTEMPTS` (Task 1); `occasions.get_profile`.
- Produces:
  - `class LookParseError(Exception)`
  - `DailyLookResult` agora com a chave `unavailable: bool`
  - `def generate_daily_look(items, weather, ocasiao=None, recent_item_ids=None) -> DailyLookResult`
  - `def _parse_reply(text: str, by_id: dict[str, dict], profile) -> tuple[list[SuggestedLook], Optional[str]]`

- [ ] **Step 1: Escrever o novo `tests/test_look_generation.py`**

Substitua o arquivo INTEIRO. Os testes antigos de composição por regras
(`test_neutral_discipline_rejects_strong_colors`,
`test_occasion_targets_the_right_formality_register`,
`test_dress_bonus_can_win_against_a_pair`, `test_condition_phrase_*`,
`test_layering_occasions_*`, `test_occasion_relaxes_register_*` e os demais que
exercitam o motor removido) desaparecem junto com o motor: manter um teste de
comportamento que não existe mais é dívida, não cobertura.

```python
"""
Testes da geração de look.

Duas metades, com propósitos diferentes:

1. PRÉ-FILTRO — comportamento determinístico e gratuito que a migração para a
   API preservou. Estes testes são de REGRESSÃO: se algum falhar, o pré-filtro
   regrediu e o guarda-roupa enviado à API deixou de ser o subconjunto certo.

2. INTERPRETAÇÃO E DEGRADAÇÃO — o que acontece com a resposta da API e o que
   acontece quando ela não vem. Nenhum destes toca a rede: a chamada ao SDK é
   substituída por dublês, para a suíte não custar dinheiro a cada execução.

O teste de chamada REAL vive em `tests/test_look_generation_live.py` e só roda
quando pedido explicitamente.
"""

import json

import pytest

from app.services.ai import look_generation
from app.services.ai.claude_client import (
    ClaudeReply,
    ClaudeUsage,
    LookApiFatal,
    LookApiTransient,
)
from app.services.ai.look_generation import (
    ACCEPTABLE_PESO,
    BAND_COLD,
    BAND_HOT,
    BAND_MILD,
    LookParseError,
    _band_for,
    _drop_forbidden,
    _have_core,
    _parse_reply,
    _partition,
    _reference_temp,
    _thermal_prefilter,
    generate_daily_look,
)
from app.services.ai.occasions import get_profile


# ── Fábrica de peças ────────────────────────────────────────────────────────
def piece(pid, category, *, peso="medio", cor="preto", formalidade="casual", chuva=False):
    return {
        "id": pid,
        "name": f"{category} {pid}",
        "category": category,
        "cor_primaria": cor,
        "cor_secundaria": None,
        "estampa": "liso",
        "formalidade": formalidade,
        "peso_termico": peso,
        "serve_chuva": chuva,
        "estacoes": [],
    }


WARDROBE = [
    piece("b1", "calca", peso="medio"),
    piece("t1", "camisa", peso="leve", cor="branco"),
    piece("t2", "malha", peso="pesado", cor="cinza"),
    piece("o1", "blazer", peso="medio", formalidade="social"),
    piece("f1", "calcado", peso="leve"),
    piece("d1", "vestido", peso="leve", cor="vermelho"),
]

MILD_DAY = {"temperatura_min": 16.0, "temperatura_max": 24.0, "condicoes": ["sol"]}
HOT_DAY = {"temperatura_min": 26.0, "temperatura_max": 34.0, "condicoes": ["sol"]}


# ═══════════════════════════════════════════════════════════════════════════
# 1. PRÉ-FILTRO — regressão
# ═══════════════════════════════════════════════════════════════════════════
def test_reference_temperature_leans_on_the_minimum():
    """Vestir por segurança: passar frio é pior que passar calor."""
    assert _reference_temp({"temperatura_min": 10.0, "temperatura_max": 20.0,
                            "condicoes": []}) == pytest.approx(14.0)


@pytest.mark.parametrize(
    "temp_ref, expected",
    [(-5.0, BAND_COLD), (14.9, BAND_COLD), (15.0, BAND_MILD), (25.0, BAND_MILD),
     (25.1, BAND_HOT), (40.0, BAND_HOT)],
)
def test_band_boundaries(temp_ref, expected):
    assert _band_for(temp_ref) == expected


def test_thermal_prefilter_drops_incompatible_known_weights():
    kept = _thermal_prefilter(WARDROBE, BAND_HOT)
    assert {p["id"] for p in kept} == {"t1", "f1", "d1"}
    assert ACCEPTABLE_PESO[BAND_HOT] == {"leve"}


def test_thermal_prefilter_keeps_pieces_without_a_declared_weight():
    """
    Peso nulo é curinga por política: o cadastro deixa o campo vazio quando a
    análise não foi conclusiva, e cortar a peça por isso puniria o usuário por
    uma limitação nossa.
    """
    unknown = piece("x1", "camisa", peso=None)
    kept = _thermal_prefilter([unknown], BAND_HOT)
    assert kept == [unknown]


def test_forbidden_categories_are_dropped_before_anything_else():
    profile = get_profile("esporte")
    kept = _drop_forbidden(WARDROBE, profile)
    categories = {p["category"] for p in kept}
    assert not (categories & profile.forbidden_categories)
    assert "blazer" not in categories and "vestido" not in categories


def test_partition_and_core_detection():
    slots = _partition(WARDROBE)
    assert {p["id"] for p in slots["bottoms"]} == {"b1"}
    assert {p["id"] for p in slots["tops"]} == {"t1", "t2"}
    assert _have_core(slots) is True
    assert _have_core(_partition([piece("f1", "calcado")])) is False
    assert _have_core(_partition([piece("d1", "vestido")])) is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. INTERPRETAÇÃO DA RESPOSTA
# ═══════════════════════════════════════════════════════════════════════════
BY_ID = {p["id"]: p for p in WARDROBE}
PROFILE = get_profile("dia_a_dia")


def reply(looks, note=None) -> str:
    return json.dumps({"looks": looks, "note": note}, ensure_ascii=False)


VALID_LOOK = {
    "label": "I",
    "items": [
        {"item_id": "b1", "role": "peça de baixo"},
        {"item_id": "t1", "role": "peça de cima"},
        {"item_id": "f1", "role": "calçado"},
    ],
    "commentary": "Para o sol, camisa branca conduz. O resto obedece.",
}


def test_valid_json_becomes_looks():
    looks, note = _parse_reply(reply([VALID_LOOK], note="uma ressalva"), BY_ID, PROFILE)
    assert len(looks) == 1
    assert looks[0]["label"] == "I"
    assert looks[0]["commentary"].startswith("Para o sol")
    assert [i["item_id"] for i in looks[0]["items"]] == ["b1", "t1", "f1"]
    assert [i["role"] for i in looks[0]["items"]] == ["peça de baixo", "peça de cima", "calçado"]
    assert note == "uma ressalva"


def test_labels_are_reassigned_by_position():
    """
    O rótulo do modelo é conferido, não confiado: se ele devolver dois "I" ou
    pular para "III", a numeração na tela sairia errada. A posição manda.
    """
    a = dict(VALID_LOOK, label="III")
    b = dict(VALID_LOOK, label="III",
             items=[{"item_id": "d1", "role": "peça única"}])
    looks, _ = _parse_reply(reply([a, b]), BY_ID, PROFILE)
    assert [lk["label"] for lk in looks] == ["I", "II"]


def test_malformed_json_is_a_parse_error():
    with pytest.raises(LookParseError):
        _parse_reply('{"looks": [', BY_ID, PROFILE)


def test_text_outside_the_json_is_a_parse_error():
    """
    `output_config.format` deveria impedir isto, mas "deveria" não é garantia.
    Aceitar prosa em volta convidaria a extrair JSON de um texto qualquer — e a
    tolerância certa aqui é nova tentativa, não adivinhação.
    """
    with pytest.raises(LookParseError):
        _parse_reply("Aqui estão os looks:\n" + reply([VALID_LOOK]), BY_ID, PROFILE)


def test_an_unknown_item_id_is_a_parse_error():
    """
    Id fora do subconjunto significa que o modelo inventou uma peça. Não dá para
    aproveitar o resto: um look com peça fantasma é um look errado.
    """
    ghost = dict(VALID_LOOK, items=[
        {"item_id": "b1", "role": "peça de baixo"},
        {"item_id": "NAO-EXISTE", "role": "peça de cima"},
    ])
    with pytest.raises(LookParseError):
        _parse_reply(reply([ghost]), BY_ID, PROFILE)


def test_an_unknown_role_is_a_parse_error():
    odd = dict(VALID_LOOK, items=[{"item_id": "b1", "role": "chapéu"}])
    with pytest.raises(LookParseError):
        _parse_reply(reply([odd]), BY_ID, PROFILE)


def test_an_empty_look_list_is_a_parse_error():
    """O pré-filtro já garantiu que dá para compor; nada é resposta errada."""
    with pytest.raises(LookParseError):
        _parse_reply(reply([]), BY_ID, PROFILE)


def test_a_dress_with_a_bottom_is_discarded_not_returned():
    """
    A regra estrutural é da casa e não pode depender do modelo obedecer. O look
    inválido cai; os válidos passam.
    """
    broken = dict(VALID_LOOK, items=[
        {"item_id": "d1", "role": "peça única"},
        {"item_id": "b1", "role": "peça de baixo"},
    ])
    looks, _ = _parse_reply(reply([broken, VALID_LOOK]), BY_ID, PROFILE)
    assert len(looks) == 1
    assert [i["item_id"] for i in looks[0]["items"]] == ["b1", "t1", "f1"]


def test_all_looks_invalid_is_a_parse_error():
    broken = dict(VALID_LOOK, items=[
        {"item_id": "d1", "role": "peça única"},
        {"item_id": "b1", "role": "peça de baixo"},
    ])
    with pytest.raises(LookParseError):
        _parse_reply(reply([broken]), BY_ID, PROFILE)


def test_a_forbidden_category_in_the_reply_is_discarded():
    """Rede de segurança: a proibição da ocasião é reconferida na saída."""
    sport = get_profile("esporte")
    with pytest.raises(LookParseError):
        _parse_reply(reply([dict(VALID_LOOK, items=[
            {"item_id": "b1", "role": "peça de baixo"},
            {"item_id": "o1", "role": "sobreposição"},
        ])]), BY_ID, sport)


def test_more_than_three_looks_are_truncated():
    looks, _ = _parse_reply(reply([VALID_LOOK] * 5), BY_ID, PROFILE)
    assert len(looks) == 3


def test_a_piece_repeated_inside_one_look_discards_that_look():
    dupe = dict(VALID_LOOK, items=[
        {"item_id": "b1", "role": "peça de baixo"},
        {"item_id": "b1", "role": "peça de cima"},
    ])
    with pytest.raises(LookParseError):
        _parse_reply(reply([dupe]), BY_ID, PROFILE)


# ═══════════════════════════════════════════════════════════════════════════
# 3. ORQUESTRAÇÃO E DEGRADAÇÃO GRACIOSA
# ═══════════════════════════════════════════════════════════════════════════
USAGE = ClaudeUsage(input_tokens=10, output_tokens=20, model="claude-opus-5")


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """As esperas de backoff não devem custar segundos de suíte."""
    monkeypatch.setattr(look_generation.time, "sleep", lambda _s: None)


def _stub_api(monkeypatch, outcomes):
    """Substitui a chamada real; `outcomes` é consumido uma entrada por tentativa."""
    calls = []

    def fake(system, user_message, schema):
        calls.append({"system": system, "user_message": user_message})
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return ClaudeReply(text=outcome, usage=USAGE)

    monkeypatch.setattr(look_generation.claude_client, "request_composition", fake)
    return calls


def test_a_successful_call_returns_the_looks(monkeypatch):
    _stub_api(monkeypatch, [reply([VALID_LOOK], note="nota do modelo")])
    result = generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia")
    assert result["unavailable"] is False
    assert len(result["looks"]) == 1
    assert result["note"] == "nota do modelo"


def test_the_prefiltered_subset_is_what_reaches_the_api(monkeypatch):
    """A peça pesada não pode aparecer na mensagem de um dia de 30 graus."""
    calls = _stub_api(monkeypatch, [reply([dict(VALID_LOOK, items=[
        {"item_id": "d1", "role": "peça única"},
        {"item_id": "f1", "role": "calçado"},
    ])])])
    generate_daily_look(WARDROBE, HOT_DAY, ocasiao="dia_a_dia")
    sent = calls[0]["user_message"]
    assert '"id":"t2"' not in sent   # malha pesada, cortada pelo pré-filtro
    assert '"id":"t1"' in sent


def test_an_insufficient_wardrobe_never_calls_the_api(monkeypatch):
    """Chamada paga só depois de o pré-filtro provar que há o que compor."""
    calls = _stub_api(monkeypatch, [reply([VALID_LOOK])])
    result = generate_daily_look([piece("f1", "calcado")], MILD_DAY, ocasiao="dia_a_dia")
    assert calls == []
    assert result["looks"] == []
    assert result["unavailable"] is False
    assert "Cadastre" in (result["note"] or "")


def test_a_transient_failure_is_retried_then_degrades(monkeypatch):
    calls = _stub_api(monkeypatch, [LookApiTransient("429")])
    result = generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia")
    assert len(calls) == look_generation.settings.ANTHROPIC_MAX_ATTEMPTS
    assert result["looks"] == []
    assert result["unavailable"] is True
    assert "não conseguiu compor" in (result["note"] or "")


def test_a_transient_failure_that_clears_succeeds(monkeypatch):
    _stub_api(monkeypatch, [LookApiTransient("429"), reply([VALID_LOOK])])
    result = generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia")
    assert result["unavailable"] is False
    assert len(result["looks"]) == 1


def test_a_fatal_failure_is_not_retried(monkeypatch):
    """Chave inválida não melhora na segunda tentativa — e cada tentativa custa."""
    calls = _stub_api(monkeypatch, [LookApiFatal("chave inválida")])
    result = generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia")
    assert len(calls) == 1
    assert result["unavailable"] is True


def test_persistent_parse_failure_degrades_gracefully(monkeypatch):
    calls = _stub_api(monkeypatch, ["isto não é json"])
    result = generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia")
    assert len(calls) == look_generation.settings.ANTHROPIC_MAX_ATTEMPTS
    assert result["looks"] == []
    assert result["unavailable"] is True


def test_degradation_never_raises(monkeypatch):
    """
    O contrato da rota é HTTP 200 sempre. Um erro inesperado do SDK — um que
    nem `claude_client` classificou — não pode escapar daqui.
    """
    _stub_api(monkeypatch, [RuntimeError("algo que ninguém previu")])
    result = generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia")
    assert result["unavailable"] is True
    assert result["looks"] == []


def test_the_relaxation_note_explains_a_wardrobe_that_did_not_fit_the_day(monkeypatch):
    """Só peça pesada num dia quente: o filtro cede e a nota diz que cedeu."""
    heavy_only = [piece("b9", "calca", peso="pesado"), piece("t9", "malha", peso="pesado")]
    _stub_api(monkeypatch, [reply([dict(VALID_LOOK, items=[
        {"item_id": "b9", "role": "peça de baixo"},
        {"item_id": "t9", "role": "peça de cima"},
    ])])])
    result = generate_daily_look(heavy_only, HOT_DAY, ocasiao="dia_a_dia")
    assert result["looks"]
    assert "temperatura" in (result["note"] or "").lower()


def test_recent_looks_reach_the_prompt(monkeypatch):
    calls = _stub_api(monkeypatch, [reply([VALID_LOOK])])
    generate_daily_look(WARDROBE, MILD_DAY, ocasiao="dia_a_dia",
                        recent_item_ids=[["b1", "t1"]])
    assert "recente" in calls[0]["user_message"].lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_look_generation.py -v`
Expected: FAIL — `ImportError: cannot import name 'LookParseError'`

- [ ] **Step 3: Reescrever `app/services/ai/look_generation.py`**

Substitua o arquivo INTEIRO pelo conteúdo abaixo.

```python
"""
Composição do "look do dia" — pré-filtro determinístico + API do Claude.

Duas etapas, com naturezas deliberadamente diferentes:

  1. PRÉ-FILTRO (gratuito, determinístico, local). Descarta o que a OCASIÃO não
     admite (inviolável) e reduz o guarda-roupa às peças cujo peso térmico cabe
     no dia. Roda ANTES de qualquer chamada paga, por dois motivos: corta
     tokens — e portanto custo — em toda geração, e resolve com uma regra de
     três linhas uma decisão que não precisa de modelo de linguagem.

  2. COMPOSIÇÃO (API do Claude). O subconjunto filtrado, o clima, a ocasião e os
     looks recentes vão para o modelo, que decide as combinações e escreve as
     justificativas seguindo o manual de estilo da Miranda
     (`look_prompt.MIRANDA_SYSTEM_PROMPT`).

── O que mudou nesta migração ──────────────────────────────────────────────
A composição por regras de cor e formalidade e as justificativas por template
foram REMOVIDAS. Não sobraram como fallback: um motor de regras mantido só para
emergências apodrece sem ninguém perceber, e a degradação honesta ("não foi
possível gerar agora") é melhor produto que um look mediano assinado pela
Miranda. A análise de peça (FashionCLIP, k-means, regras) não foi tocada e
continua self-hosted e gratuita.

── Custo ──────────────────────────────────────────────────────────────────
Cada geração é uma chamada paga. O consumo de tokens e o custo estimado saem em
log a cada chamada (ver `claude_client`). Não há controle de quota nesta fase.

── Filosofia, inalterada ───────────────────────────────────────────────────
Degradar graciosamente. Esta função NUNCA lança: guarda-roupa insuficiente, API
fora do ar ou resposta ilegível viram `looks: []` com uma `note` que explica o
que houve. A rota devolve HTTP 200 em todos os casos.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, TypedDict

from app.core.config import settings
from app.services.ai import claude_client
from app.services.ai.claude_client import LookApiFatal, LookApiTransient
from app.services.ai.look_prompt import (
    LOOK_LABELS,
    LOOK_RESPONSE_SCHEMA,
    MIRANDA_SYSTEM_PROMPT,
    ROLE_ACCESSORY,
    ROLE_BOTTOM,
    ROLE_DRESS,
    ROLE_FOOTWEAR,
    ROLE_OUTER,
    ROLE_TOP,
    VALID_ROLES,
    build_user_message,
)
from app.services.ai.occasions import OccasionProfile, get_profile

logger = logging.getLogger("miranda.ai.look_generation")


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de entrada/saída
# ─────────────────────────────────────────────────────────────────────────────
class WeatherInfo(TypedDict):
    temperatura_min: float
    temperatura_max: float
    # Condições combináveis do dia (ex.: ["sol", "vento"]). Lista vazia é
    # tolerada e equivale a "dia sem particularidade".
    condicoes: list[str]


class SuggestedLookItem(TypedDict):
    item_id: str
    role: str


class SuggestedLook(TypedDict):
    label: str
    items: list[SuggestedLookItem]
    commentary: str


class DailyLookResult(TypedDict):
    looks: list[SuggestedLook]
    # Nota opcional: guarda-roupa limitado, filtro relaxado, ou a explicação da
    # indisponibilidade. None quando a composição foi plena e sem ressalva.
    note: Optional[str]
    # True somente quando a FALHA foi nossa (API fora do ar, chave inválida,
    # resposta ilegível). Guarda-roupa insuficiente é `False`: não é falha, é
    # uma resposta legítima sobre o acervo da pessoa. A distinção existe para o
    # log e para o histórico — o frontend renderiza `note` nos dois casos.
    unavailable: bool


class LookParseError(Exception):
    """A resposta da API não pôde ser interpretada como uma composição válida."""


# ─────────────────────────────────────────────────────────────────────────────
# Constantes de domínio — ajustáveis e documentadas
# ─────────────────────────────────────────────────────────────────────────────

# Categorias agrupadas por "posição" no look.
BOTTOMS = {"calca", "saia"}          # parte de baixo
TOPS = {"camisa", "malha"}           # parte de cima
OUTERS = {"blazer", "casaco"}        # sobreposição
DRESSES = {"vestido"}                # peça única (cobre o corpo inteiro)
FOOTWEAR = {"calcado"}               # calçado
SCARVES = {"cachecol"}               # complemento de aquecimento
ACCESSORIES = {"acessorio", "outros"}  # complemento opcional

# ── Faixas de temperatura (°C) → pesos térmicos aceitáveis ───────────────────
# A temperatura de referência dá MAIS peso à mínima ("vestir por segurança":
# é pior passar frio do que calor), por isso 0.6*min + 0.4*max.
TEMP_MIN_WEIGHT = 0.6
TEMP_MAX_WEIGHT = 0.4

COLD_MAX = 15.0   # temp_ref < 15  → frio
MILD_MAX = 25.0   # 15 <= temp_ref <= 25 → ameno ; > 25 → quente

BAND_COLD = "frio"
BAND_MILD = "ameno"
BAND_HOT = "quente"

# Pesos aceitos em cada faixa. Peça com peso NULO passa sempre (política
# permissiva): o campo fica vazio quando a análise não foi conclusiva, e cortar
# a peça por isso puniria o usuário por uma limitação nossa.
ACCEPTABLE_PESO: dict[str, set[str]] = {
    BAND_COLD: {"pesado", "medio"},
    BAND_MILD: {"medio", "leve"},
    BAND_HOT: {"leve"},
}

MAX_LOOKS = 3

# Espera entre tentativas, em segundos. Curta de propósito: há uma pessoa
# olhando a tela de carregamento. Três tentativas somam menos de 2s de espera.
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.6, 1.5)

_UNAVAILABLE_NOTE = (
    "A Miranda não conseguiu compor o look agora. Tente novamente em instantes."
)

# Papel → conjunto de categorias que podem legitimamente ocupá-lo. Usado para
# reconferir a saída do modelo: um "vestido" declarado como "peça de baixo"
# passaria pelo schema (o enum só valida a string) e produziria um look errado.
_ROLE_CATEGORIES: dict[str, set[str]] = {
    ROLE_BOTTOM: BOTTOMS,
    ROLE_TOP: TOPS,
    ROLE_OUTER: OUTERS,
    ROLE_DRESS: DRESSES,
    ROLE_FOOTWEAR: FOOTWEAR,
    ROLE_ACCESSORY: SCARVES | ACCESSORIES,
}


# ─────────────────────────────────────────────────────────────────────────────
# Pré-filtro (etapa 1 — gratuita)
# ─────────────────────────────────────────────────────────────────────────────
def _reference_temp(weather: WeatherInfo) -> float:
    return (
        TEMP_MIN_WEIGHT * float(weather["temperatura_min"])
        + TEMP_MAX_WEIGHT * float(weather["temperatura_max"])
    )


def _band_for(temp_ref: float) -> str:
    if temp_ref < COLD_MAX:
        return BAND_COLD
    if temp_ref <= MILD_MAX:
        return BAND_MILD
    return BAND_HOT


def _drop_forbidden(
    items: list[dict[str, Any]], profile: OccasionProfile
) -> list[dict[str, Any]]:
    """
    Remove as categorias que a ocasião NÃO ADMITE.

    Inviolável: nunca é relaxado, nem em guarda-roupa pobre. É preferível não
    montar um look de academia a sugerir um blazer para ela — e é barato demais
    para delegar ao modelo.
    """
    if not profile.forbidden_categories:
        return list(items)
    return [i for i in items if i.get("category") not in profile.forbidden_categories]


def _thermal_prefilter(items: list[dict[str, Any]], band: str) -> list[dict[str, Any]]:
    """
    Mantém peças cujo peso térmico é compatível com a faixa OU nulo (permissivo).
    Peças com peso conhecido e incompatível são removidas.
    """
    acceptable = ACCEPTABLE_PESO[band]
    return [
        it for it in items
        if it.get("peso_termico") is None or it.get("peso_termico") in acceptable
    ]


def _partition(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    slots: dict[str, list[dict[str, Any]]] = {
        "bottoms": [], "tops": [], "outers": [],
        "dresses": [], "footwear": [], "scarves": [], "accessories": [],
    }
    for it in items:
        cat = it.get("category")
        if cat in BOTTOMS:
            slots["bottoms"].append(it)
        elif cat in TOPS:
            slots["tops"].append(it)
        elif cat in OUTERS:
            slots["outers"].append(it)
        elif cat in DRESSES:
            slots["dresses"].append(it)
        elif cat in FOOTWEAR:
            slots["footwear"].append(it)
        elif cat in SCARVES:
            slots["scarves"].append(it)
        elif cat in ACCESSORIES:
            slots["accessories"].append(it)
    return slots


def _have_core(slots: dict[str, list[dict[str, Any]]]) -> bool:
    """Há núcleo possível: um vestido, ou um par baixo+cima."""
    return bool(slots["dresses"]) or (bool(slots["bottoms"]) and bool(slots["tops"]))


# ─────────────────────────────────────────────────────────────────────────────
# Interpretação da resposta (etapa 2 — validação da saída do modelo)
# ─────────────────────────────────────────────────────────────────────────────
def _structure_is_valid(
    categories: list[str], profile: OccasionProfile
) -> tuple[bool, str]:
    """
    Confere as regras estruturais que são da CASA, não do modelo.

    O manual de estilo pede tudo isto, e o modelo obedece na esmagadora maioria
    das vezes — mas "quase sempre" não é uma garantia que se possa mostrar ao
    usuário. Esta função é a garantia.

    Returns:
        (válido, motivo). O motivo entra no log quando um look é descartado.
    """
    if profile.forbidden_categories and (set(categories) & profile.forbidden_categories):
        return False, f"categoria proibida em {profile.key}"

    n_dress = sum(1 for c in categories if c in DRESSES)
    n_bottom = sum(1 for c in categories if c in BOTTOMS)
    n_top = sum(1 for c in categories if c in TOPS)
    n_outer = sum(1 for c in categories if c in OUTERS)

    if n_outer > 1:
        return False, "mais de uma sobreposição"

    if n_dress:
        if n_dress > 1:
            return False, "mais de uma peça única"
        if n_bottom or n_top:
            return False, "peça única acompanhada de peça de baixo ou de cima"
        return True, ""

    if n_bottom != 1 or n_top != 1:
        return False, f"núcleo inválido ({n_bottom} de baixo, {n_top} de cima)"
    return True, ""


def _parse_reply(
    text: str, by_id: dict[str, dict[str, Any]], profile: OccasionProfile
) -> tuple[list[SuggestedLook], Optional[str]]:
    """
    Interpreta a resposta da API e a valida contra o subconjunto que foi enviado.

    A tolerância aqui é deliberadamente estreita. `output_config.format` já faz a
    API garantir JSON bem formado no formato certo, então uma resposta que não
    passe daqui indica algo de fato errado — e a resposta certa a isso é nova
    tentativa, não adivinhação. Extrair JSON de um texto com prosa em volta
    mascararia o problema e um dia entregaria um look montado a partir de um
    fragmento.

    Args:
        text: corpo bruto devolvido pelo modelo.
        by_id: subconjunto ENVIADO, indexado por id. É contra ele que os ids da
            resposta são conferidos — um id de fora significa peça inventada.
        profile: perfil da ocasião, para reconferir as categorias proibidas.

    Returns:
        (looks válidos, nota do modelo). Os rótulos são reatribuídos por posição.

    Raises:
        LookParseError: JSON malformado, prosa fora do JSON, id inexistente,
            papel desconhecido, ou nenhum look estruturalmente válido.
    """
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise LookParseError(f"resposta não é JSON válido: {exc}") from exc

    if not isinstance(payload, dict):
        raise LookParseError("resposta não é um objeto JSON")

    raw_looks = payload.get("looks")
    if not isinstance(raw_looks, list) or not raw_looks:
        raise LookParseError("resposta sem a lista 'looks'")

    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        note = None

    looks: list[SuggestedLook] = []
    for raw in raw_looks[:MAX_LOOKS]:
        if not isinstance(raw, dict):
            raise LookParseError("entrada de look que não é um objeto")

        raw_items = raw.get("items")
        commentary = raw.get("commentary")
        if not isinstance(raw_items, list) or not raw_items:
            raise LookParseError("look sem peças")
        if not isinstance(commentary, str) or not commentary.strip():
            raise LookParseError("look sem justificativa")

        items: list[SuggestedLookItem] = []
        categories: list[str] = []
        seen: set[str] = set()
        duplicated = False

        for entry in raw_items:
            if not isinstance(entry, dict):
                raise LookParseError("peça que não é um objeto")
            item_id = entry.get("item_id")
            role = entry.get("role")

            if not isinstance(item_id, str) or item_id not in by_id:
                raise LookParseError(f"id de peça fora do subconjunto: {item_id!r}")
            if role not in VALID_ROLES:
                raise LookParseError(f"papel desconhecido: {role!r}")

            category = str(by_id[item_id].get("category"))
            if category not in _ROLE_CATEGORIES[role]:
                raise LookParseError(
                    f"peça de categoria {category!r} declarada como {role!r}"
                )

            if item_id in seen:
                duplicated = True
                break
            seen.add(item_id)

            items.append(SuggestedLookItem(item_id=item_id, role=role))
            categories.append(category)

        if duplicated:
            logger.warning("Look descartado: peça repetida dentro do mesmo look.")
            continue

        ok, reason = _structure_is_valid(categories, profile)
        if not ok:
            logger.warning("Look descartado (%s) — categorias: %s", reason, categories)
            continue

        looks.append(
            SuggestedLook(
                # O rótulo do modelo é conferido, não confiado: dois "I" ou um
                # salto para "III" sairiam errados na tela. A posição manda.
                label=LOOK_LABELS[len(looks)],
                items=items,
                commentary=commentary.strip(),
            )
        )

    if not looks:
        raise LookParseError("nenhum look estruturalmente válido na resposta")

    return looks, note


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────
def generate_daily_look(
    items: list[dict[str, Any]],
    weather: WeatherInfo,
    ocasiao: Optional[str] = None,
    recent_item_ids: Optional[list[list[str]]] = None,
) -> DailyLookResult:
    """
    Gera de 2 a 3 sugestões de look para o dia.

    Args:
        items: peças do usuário (cada uma como dict com id e atributos de moda).
        weather: mínima, máxima e a LISTA de condições climáticas do dia.
        ocasiao: para o que a pessoa precisa do look (chave de `Ocasiao`).
            Ausente ou desconhecida cai em `dia_a_dia`, o registro mais elástico.
        recent_item_ids: núcleos dos looks recentes, para o modelo não repetir a
            combinação que acabou de sugerir. Cada entrada é uma lista de ids.

    Returns:
        DailyLookResult. NUNCA lança: toda falha vira `looks: []` mais uma
        `note` explicativa, e `unavailable=True` quando a falha foi nossa.
    """
    profile = get_profile(ocasiao)
    band = _band_for(_reference_temp(weather))
    notes: list[str] = []

    # ── Etapa 1: pré-filtro (gratuito) ──────────────────────────────────────
    allowed = _drop_forbidden(items, profile)
    n_forbidden = len(items) - len(allowed)
    thermal = _thermal_prefilter(allowed, band)

    # Uma única relaxação: se o corte térmico não deixa nem um núcleo possível,
    # é melhor vestir a pessoa com o que ela tem e avisar do descompasso do que
    # devolver a tela vazia. A proibição da ocasião NÃO participa disso.
    if _have_core(_partition(thermal)):
        selection = thermal
    elif _have_core(_partition(allowed)):
        selection = allowed
        notes.append(
            "Poucas peças combinam com esta temperatura; ampliei a seleção para "
            "conseguir compor."
        )
    else:
        if n_forbidden:
            note = (
                f"Nenhuma peça do guarda-roupa serve para {profile.phrase}: "
                f"{n_forbidden} peça(s) foram descartadas por não caberem nesta "
                "ocasião. Cadastre uma parte de baixo e uma de cima adequadas."
            )
        else:
            note = (
                "Ainda não há peças suficientes para compor um look completo. "
                "Cadastre ao menos uma parte de baixo e uma de cima — ou um "
                "vestido — para a Miranda trabalhar."
            )
        # Guarda-roupa insuficiente NÃO é indisponibilidade: é uma resposta
        # legítima sobre o acervo. E não gasta uma chamada paga.
        return DailyLookResult(looks=[], note=note, unavailable=False)

    # ── Etapa 2: composição (chamada paga) ──────────────────────────────────
    by_id = {str(p["id"]): p for p in selection}
    user_message = build_user_message(
        selection, dict(weather), profile.label, recent_item_ids or []
    )

    max_attempts = max(1, settings.ANTHROPIC_MAX_ATTEMPTS)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            reply = claude_client.request_composition(
                MIRANDA_SYSTEM_PROMPT, user_message, LOOK_RESPONSE_SCHEMA
            )
            looks, model_note = _parse_reply(reply.text, by_id, profile)

        except LookApiFatal as exc:
            # Chave inválida ou requisição recusada não melhoram na tentativa
            # seguinte — e cada tentativa custa. Desiste na hora.
            logger.error("Composição indisponível (falha definitiva): %s", exc)
            return DailyLookResult(
                looks=[], note=_UNAVAILABLE_NOTE, unavailable=True
            )

        except (LookApiTransient, LookParseError) as exc:
            # Interpretação falha entra no mesmo laço que o 429 de propósito:
            # uma resposta ilegível é tão retentável quanto uma rede instável, e
            # separá-las daria dois laços com a mesma forma.
            last_error = exc
            logger.warning(
                "Tentativa %d/%d de composição falhou: %s", attempt, max_attempts, exc
            )
            if attempt < max_attempts:
                time.sleep(
                    RETRY_BACKOFF_SECONDS[
                        min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                    ]
                )
            continue

        except Exception as exc:  # noqa: BLE001
            # Rede de segurança final. O contrato da rota é HTTP 200 sempre, e
            # um erro que ninguém previu não pode ser o que quebra isso.
            logger.exception("Erro inesperado ao compor o look: %s", exc)
            return DailyLookResult(
                looks=[], note=_UNAVAILABLE_NOTE, unavailable=True
            )

        if model_note:
            notes.append(model_note)
        return DailyLookResult(
            looks=looks, note=" ".join(notes) if notes else None, unavailable=False
        )

    logger.error(
        "Composição indisponível após %d tentativas. Última falha: %s",
        max_attempts,
        last_error,
    )
    return DailyLookResult(looks=[], note=_UNAVAILABLE_NOTE, unavailable=True)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_look_generation.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Rodar a suíte inteira — nada mais pode ter quebrado**

Run: `.venv/bin/python -m pytest -q`
Expected: só `tests/test_analysis_regression.py` pode falhar se o FashionCLIP não estiver baixado; qualquer outra falha é regressão desta tarefa e deve ser corrigida antes do commit.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai/look_generation.py tests/test_look_generation.py
git commit -m "refactor(look)!: substitui composição por regras pela API do Claude"
```

---
### Task 5: `look_service` — histórico recente como contexto, persistência preservada

**Files:**
- Modify: `app/services/look_service.py` (docstring do módulo, novo `_recent_item_ids`, chamada a `generate_daily_look`)
- Modify: `app/api/routes/looks.py:1-8` (docstring do módulo)
- Modify: `app/schemas/look.py:62` (docstring de `GenerateLookResponse`)
- Test: `tests/test_look_service_history.py`

**Interfaces:**
- Consumes: `generate_daily_look(items, weather, ocasiao, recent_item_ids)` (Task 4).
- Produces: `def _recent_item_ids(db: Session, *, user_id: uuid.UUID, limit: int = 3) -> list[list[str]]`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_look_service_history.py`:

```python
"""
O histórico recente vira contexto da próxima geração.

Roda contra o Postgres de DATABASE_URL — é `looks_history` que está sob teste, e
ela vive no banco. Sem banco acessível, os testes são PULADOS (mesmo padrão de
tests/test_wardrobe_image_access.py).
"""

from __future__ import annotations

import uuid

import pytest

from app.core.database import SessionLocal, engine
from app.models.look_history import LookHistory
from app.models.user import User
from app.services.look_service import _recent_item_ids


@pytest.fixture(scope="module")
def db():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db) -> User:
    u = User(
        name="Dona do Histórico",
        email=f"hist-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(LookHistory).filter(LookHistory.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def _record(db, user, looks):
    db.add(LookHistory(
        user_id=user.id,
        temperatura_min=15.0,
        temperatura_max=25.0,
        condicao_climatica="sol",
        ocasiao="dia_a_dia",
        itens_sugeridos={"looks": looks, "note": None},
        justificativa="x",
    ))
    db.commit()


def test_no_history_yields_an_empty_context(db, user):
    assert _recent_item_ids(db, user_id=user.id) == []


def test_recent_looks_are_returned_as_id_lists(db, user):
    _record(db, user, [{"label": "I", "item_ids": ["a", "b"], "commentary": "c"}])
    assert _recent_item_ids(db, user_id=user.id) == [["a", "b"]]


def test_the_context_is_capped_so_the_prompt_does_not_grow_forever(db, user):
    """
    Cada look no histórico é token pago em TODA geração seguinte. O corte existe
    para o custo por chamada não crescer com o tempo de uso do produto.
    """
    for i in range(5):
        _record(db, user, [{"label": "I", "item_ids": [f"x{i}"], "commentary": "c"}])
    assert len(_recent_item_ids(db, user_id=user.id, limit=3)) == 3


def test_a_failed_generation_contributes_nothing(db, user):
    """Registro sem looks (API indisponível) não polui o contexto."""
    _record(db, user, [])
    assert _recent_item_ids(db, user_id=user.id) == []


def test_another_users_history_is_never_leaked(db, user):
    other = User(
        name="Outra",
        email=f"outra-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    try:
        _record(db, other, [{"label": "I", "item_ids": ["segredo"], "commentary": "c"}])
        assert _recent_item_ids(db, user_id=user.id) == []
    finally:
        db.query(LookHistory).filter(LookHistory.user_id == other.id).delete()
        db.delete(other)
        db.commit()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_look_service_history.py -v`
Expected: FAIL — `ImportError: cannot import name '_recent_item_ids'`

- [ ] **Step 3: Editar `app/services/look_service.py`**

**3a.** Substitua a docstring do módulo (linhas 1-10):

```python
"""
Lógica de negócio do "look do dia".

Orquestra a composição (services/ai/look_generation — pré-filtro determinístico
mais uma chamada à API do Claude), resolve as peças sugeridas para os dados que
o frontend precisa renderizar, persiste o registro em `looks_history` e devolve
a resposta pronta.

O histórico não é só arquivo: os looks recentes voltam como CONTEXTO da próxima
geração, para a Miranda não repetir na terça a combinação que ditou na segunda.

⚠️ A composição chama uma API PAGA. A análise de peça (services/ai/clothing_analysis)
continua self-hosted e gratuita — são coisas diferentes. Ver README, seção 12.
"""
```

**3b.** Acrescente o import de `desc` ao bloco de imports:

```python
from sqlalchemy import desc
```

**3c.** Acrescente a função abaixo, logo depois de `_item_to_payload`:

```python
# Quantos looks recentes viram contexto da próxima geração. Cada um é token
# pago em TODA chamada seguinte, então o corte é baixo de propósito: o objetivo
# é "não repita o que você acabou de sugerir", não uma memória longa.
_RECENT_LOOKS_LIMIT = 3


def _recent_item_ids(
    db: Session, *, user_id: uuid.UUID, limit: int = _RECENT_LOOKS_LIMIT
) -> list[list[str]]:
    """
    Lê os núcleos dos looks recentes do usuário, do mais novo para o mais antigo.

    Returns:
        Uma lista de listas de ids, no máximo `limit` entradas. Registros de
        gerações que falharam não têm looks e simplesmente não contribuem.
    """
    rows = (
        db.query(LookHistory)
        .filter(LookHistory.user_id == user_id)
        .order_by(desc(LookHistory.data_gerado))
        .limit(limit)
        .all()
    )

    recent: list[list[str]] = []
    for row in rows:
        payload = row.itens_sugeridos
        if not isinstance(payload, dict):
            continue
        for look in payload.get("looks") or []:
            ids = look.get("item_ids") if isinstance(look, dict) else None
            if ids:
                recent.append([str(i) for i in ids])
            if len(recent) >= limit:
                return recent
    return recent
```

**3d.** Na função `generate_look`, substitua a chamada a `generate_daily_look`:

```python
    result = generate_daily_look(
        items_payload,  # type: ignore[arg-type]
        weather,  # type: ignore[arg-type]
        ocasiao=payload.ocasiao.value,
        recent_item_ids=_recent_item_ids(db, user_id=user_id),
    )
```

**3e.** Substitua a docstring de `generate_look`:

```python
    """
    Gera o look do dia, persiste em `looks_history` e devolve a resposta.

    Degrada graciosamente em duas situações distintas, ambas com HTTP 200:
    guarda-roupa insuficiente (nem chega a chamar a API) e API indisponível.
    Nos dois casos a resposta vem com `looks` vazio e uma `note` explicativa.
    """
```

> A persistência em `looks_history` NÃO muda nesta tarefa. Ela continua gravando
> em toda geração, inclusive nas que falharam — o registro da falha é barato e
> não polui o contexto (um registro sem looks não contribui com id nenhum).

- [ ] **Step 4: Atualizar a docstring de `app/api/routes/looks.py`**

Substitua as linhas 1-8:

```python
"""
Rotas do "look do dia".

A composição usa a API do Claude (services/ai/look_generation), precedida de um
pré-filtro determinístico e gratuito por clima e ocasião. A rota chama a
geração, persiste o registro e devolve os looks.

Nunca devolve erro por falta de peças nem por indisponibilidade da API: nesses
casos a resposta vem com `looks` vazio e uma `note` explicativa, em HTTP 200.
"""
```

- [ ] **Step 5: Atualizar a docstring de `GenerateLookResponse` em `app/schemas/look.py:62`**

```python
class GenerateLookResponse(BaseModel):
    """Resposta da geração de look (pré-filtro determinístico + API do Claude)."""
```

E o comentário do campo `note`, logo abaixo de `looks`:

```python
    # Nota opcional: guarda-roupa limitado para o clima ou para a ocasião, ou a
    # explicação de que não foi possível gerar agora.
    note: str | None = None
```

- [ ] **Step 6: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_look_service_history.py -v`
Expected: PASS (5 testes) — ou SKIPPED se o Postgres não estiver de pé.

- [ ] **Step 7: Verificar que a rota ainda responde 200 sem chave configurada**

Run:
```bash
ANTHROPIC_API_KEY= .venv/bin/python - <<'EOF'
from app.services.ai.look_generation import generate_daily_look
from app.services.ai import claude_client
claude_client.reset_client_cache()
from app.core.config import settings
settings.ANTHROPIC_API_KEY = ""
r = generate_daily_look(
    [{"id": "a", "name": "Calça", "category": "calca", "peso_termico": "medio"},
     {"id": "b", "name": "Camisa", "category": "camisa", "peso_termico": "leve"}],
    {"temperatura_min": 16.0, "temperatura_max": 24.0, "condicoes": ["sol"]},
    ocasiao="dia_a_dia",
)
print(r)
EOF
```
Expected: `{'looks': [], 'note': 'A Miranda não conseguiu compor o look agora...', 'unavailable': True}` — sem exceção.

- [ ] **Step 8: Commit**

```bash
git add app/services/look_service.py app/api/routes/looks.py app/schemas/look.py tests/test_look_service_history.py
git commit -m "feat(look): histórico recente como contexto da composição"
```

---

### Task 6: Teste de chamada real, desligado por padrão

**Files:**
- Create: `tests/test_look_generation_live.py`
- Modify: `pytest.ini`

**Interfaces:**
- Consumes: `generate_daily_look` (Task 4), `settings.ANTHROPIC_API_KEY` (Task 1).
- Produces: nada consumido por outras tarefas.

- [ ] **Step 1: Registrar o marcador em `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests

markers =
    live: chama a API PAGA da Anthropic de verdade. Desligado por padrão.
        Rode com MIRANDA_LIVE_API_TESTS=1 quando quiser validar a qualidade
        contra a API real.
```

- [ ] **Step 2: Escrever `tests/test_look_generation_live.py`**

```python
"""
Validação de qualidade contra a API REAL. Custa dinheiro — por isso não roda na
suíte padrão.

A suíte normal mocka o SDK: ela prova que o código se comporta, não que a
Miranda tem bom gosto. Este arquivo cobre a outra metade, e por isso precisa ser
pedido explicitamente:

    MIRANDA_LIVE_API_TESTS=1 .venv/bin/python -m pytest tests/test_look_generation_live.py -v -s

As asserções são propositalmente FROUXAS. Um modelo de linguagem não devolve a
mesma frase duas vezes, e um teste que exigisse isso quebraria por motivo
errado. O que se afirma aqui é o que não pode variar: a estrutura do look, a
procedência dos ids e a existência de uma justificativa. O julgamento de gosto é
humano, e é para isso que o teste imprime o resultado com `-s`.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.services.ai.look_generation import BOTTOMS, DRESSES, TOPS, generate_daily_look

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("MIRANDA_LIVE_API_TESTS") != "1",
        reason="teste de API paga: rode com MIRANDA_LIVE_API_TESTS=1",
    ),
    pytest.mark.skipif(
        not settings.ANTHROPIC_API_KEY,
        reason="ANTHROPIC_API_KEY não configurada",
    ),
]


WARDROBE = [
    {"id": "p1", "name": "Calça de alfaiataria preta", "category": "calca",
     "cor_primaria": "preto", "estampa": "liso", "formalidade": "social",
     "peso_termico": "medio", "serve_chuva": False},
    {"id": "p2", "name": "Jeans reto azul", "category": "calca",
     "cor_primaria": "azul", "estampa": "liso", "formalidade": "casual",
     "peso_termico": "medio", "serve_chuva": False},
    {"id": "p3", "name": "Camisa oxford azul-clara", "category": "camisa",
     "cor_primaria": "azul", "estampa": "liso", "formalidade": "smart_casual",
     "peso_termico": "leve", "serve_chuva": False},
    {"id": "p4", "name": "Camiseta branca de algodão", "category": "camisa",
     "cor_primaria": "branco", "estampa": "liso", "formalidade": "casual",
     "peso_termico": "leve", "serve_chuva": False},
    {"id": "p5", "name": "Malha de lã cinza", "category": "malha",
     "cor_primaria": "cinza", "estampa": "liso", "formalidade": "casual",
     "peso_termico": "pesado", "serve_chuva": False},
    {"id": "p6", "name": "Trench coat caramelo", "category": "casaco",
     "cor_primaria": "caramelo", "estampa": "liso", "formalidade": "smart_casual",
     "peso_termico": "pesado", "serve_chuva": True},
    {"id": "p7", "name": "Scarpin preto", "category": "calcado",
     "cor_primaria": "preto", "estampa": "liso", "formalidade": "social",
     "peso_termico": "leve", "serve_chuva": False},
    {"id": "p8", "name": "Tênis branco de couro", "category": "calcado",
     "cor_primaria": "branco", "estampa": "liso", "formalidade": "casual",
     "peso_termico": "leve", "serve_chuva": False},
    {"id": "p9", "name": "Vestido midi vermelho", "category": "vestido",
     "cor_primaria": "vermelho", "estampa": "liso", "formalidade": "social",
     "peso_termico": "leve", "serve_chuva": False},
]


def _print(result, titulo):
    print(f"\n═══ {titulo} ═══")
    if result["note"]:
        print(f"[nota] {result['note']}")
    for look in result["looks"]:
        pecas = ", ".join(f"{i['item_id']} ({i['role']})" for i in look["items"])
        print(f"  Look {look['label']}: {pecas}")
        print(f"    — {look['commentary']}")


def _assert_structure(result):
    by_id = {p["id"]: p for p in WARDROBE}
    assert result["unavailable"] is False, result["note"]
    assert 1 <= len(result["looks"]) <= 3

    for look in result["looks"]:
        ids = [i["item_id"] for i in look["items"]]
        assert len(ids) == len(set(ids)), "peça repetida dentro do look"
        assert all(i in by_id for i in ids), "id que não veio do guarda-roupa"
        assert look["commentary"].strip(), "look sem justificativa"

        cats = [by_id[i]["category"] for i in ids]
        if any(c in DRESSES for c in cats):
            assert not any(c in BOTTOMS or c in TOPS for c in cats), \
                "vestido acompanhado de peça de baixo ou de cima"
        else:
            assert sum(1 for c in cats if c in BOTTOMS) == 1
            assert sum(1 for c in cats if c in TOPS) == 1


def test_live_warm_sunny_day():
    result = generate_daily_look(
        WARDROBE,
        {"temperatura_min": 22.0, "temperatura_max": 31.0, "condicoes": ["sol"]},
        ocasiao="dia_a_dia",
    )
    _print(result, "Dia quente e ensolarado — dia a dia")
    _assert_structure(result)


def test_live_cold_rainy_day():
    result = generate_daily_look(
        WARDROBE,
        {"temperatura_min": 6.0, "temperatura_max": 13.0,
         "condicoes": ["chuva", "frio", "vento"]},
        ocasiao="trabalho",
    )
    _print(result, "Dia frio e chuvoso — trabalho")
    _assert_structure(result)
```

- [ ] **Step 3: Confirmar que a suíte padrão NÃO chama a API**

Run: `.venv/bin/python -m pytest tests/test_look_generation_live.py -v`
Expected: 2 SKIPPED, com o motivo "teste de API paga".

- [ ] **Step 4: Rodar de verdade, uma vez, para ver se passa**

Run: `MIRANDA_LIVE_API_TESTS=1 .venv/bin/python -m pytest tests/test_look_generation_live.py -v -s`
Expected: 2 PASSED, com os looks impressos.

- [ ] **Step 5: Commit**

```bash
git add tests/test_look_generation_live.py pytest.ini
git commit -m "test(look): validação opcional contra a API real"
```

---
### Task 7: Documentação — README e a referência órfã em `occasions.py`

O README hoje afirma em cinco lugares que a geração de look não custa nada.
Isso deixou de ser verdade e é o tipo de desatualização que custa dinheiro a
quem confia nela.

**Files:**
- Modify: `../README.md` (índice linha 36; nota de dependências linha ~292; seção 12 inteira, linhas 526-719; árvore de arquivos linha ~774-778; parágrafo "Camada de IA" linha ~793; seção 15 linhas ~810-830)
- Modify: `app/services/ai/occasions.py:36` (referência a função removida)

**Interfaces:** nenhuma — tarefa de documentação.

- [ ] **Step 1: Corrigir a referência órfã em `occasions.py:36`**

A linha cita `_look_structure_is_valid`, que deixou de existir na Task 4.
Substitua:

```
    A rede de segurança em `_structure_is_valid` reconfere isso na saída da API.
```

- [ ] **Step 2: Verificar que nenhuma outra referência ficou órfã**

Run:
```bash
grep -rn "_compose_commentary\|_build_bases\|_select_varied\|_look_structure_is_valid\|_register_filter\|_condition_flags\|_seed_from\|FORMALITY_RANK\|STRONG_COLOR_FAMILIES" app scripts
```
Expected: nenhuma saída.

- [ ] **Step 3: Atualizar o título da seção 12 no índice (README linha 36)**

```
12. [Look do dia — composição pela API do Claude](#12-look-do-dia--composição-pela-api-do-claude)
```

- [ ] **Step 4: Atualizar a nota de dependências (README, bloco iniciando em ~linha 292)**

Substitua o blockquote inteiro por:

```markdown
> **Sobre as dependências de IA — são DUAS, com naturezas opostas:**
>
> · `torch`, `transformers`, `scikit-learn`, `numpy` e `Pillow` sustentam a
>   **análise automática de peça** (seção 10), que é **100% self-hosted e
>   gratuita**. Instalar o `torch` CPU-only antes do `requirements.txt` evita
>   puxar os pacotes CUDA (GPU), bem maiores. Na **primeira** análise, os pesos
>   do FashionCLIP (~600 MB) são baixados do HuggingFace (exige internet **uma
>   vez**); depois tudo roda localmente.
>
> · `anthropic` sustenta a **composição do look do dia** (seção 12), que chama a
>   API do Claude e **custa dinheiro por geração**. Exige `ANTHROPIC_API_KEY` no
>   `.env`. Sem a chave a aplicação sobe normalmente e a geração de look devolve
>   uma nota explicando que não foi possível — nada mais é afetado.
```

- [ ] **Step 5: Reescrever a seção 12 inteira (README, linhas 526 a 719)**

Apague tudo entre `## 12. Look do dia — composição determinística` e a linha
`---` que antecede `## 13. Solução de problemas`, e escreva no lugar:

````markdown
## 12. Look do dia — composição pela API do Claude

A geração do look (`POST /api/looks/generate`) tem **duas etapas de naturezas
deliberadamente diferentes**: um pré-filtro determinístico e gratuito, e uma
chamada à **API do Claude** que faz a composição de fato.

> ⚠️ **Esta etapa custa dinheiro.** Cada geração é uma requisição paga à API da
> Anthropic. Isso vale inclusive para `scripts/seed_and_test_looks.py`, que
> passa por `look_service` e portanto chama a API a cada cenário. A análise de
> peça (seção 10) continua **self-hosted e gratuita** — são coisas separadas.

A lógica vive em `miranda-api/app/services/ai/`:

| Arquivo | Responsabilidade |
|---|---|
| `look_generation.py` | Pré-filtro, orquestração, validação da resposta, degradação. |
| `look_prompt.py` | O manual de estilo da Miranda, o schema JSON e a montagem do contexto. |
| `claude_client.py` | Transporte: uma chamada por vez, classificação de erro, log de custo. |
| `occasions.py` | Tabela de perfis de ocasião (hoje: categorias proibidas e rótulos). |

`look_service.generate_look` resolve as peças, persiste em `looks_history` e
devolve a resposta; a rota é fina.

### Configuração

```bash
# miranda-api/.env
ANTHROPIC_API_KEY=sk-ant-...        # crie em console.anthropic.com/settings/keys
ANTHROPIC_MODEL=claude-opus-5       # trocar aqui basta; nenhum código fixa o modelo
ANTHROPIC_MAX_OUTPUT_TOKENS=4000
ANTHROPIC_EFFORT=medium             # low | medium | high | xhigh | max
ANTHROPIC_MAX_ATTEMPTS=3
ANTHROPIC_TIMEOUT_SECONDS=60.0
```

**Modelo:** o padrão é **`claude-opus-5`**. Para trocar, basta mudar
`ANTHROPIC_MODEL` — nenhum código referencia o identificador diretamente. Ao
trocar, acrescente o preço do modelo novo em
`MODEL_PRICES_USD_PER_MTOK` (`services/ai/claude_client.py`), senão o log de
custo passa a registrar `0.0` (e avisa uma vez no log).

**Por que `ANTHROPIC_EFFORT` e não `temperature`:** os modelos atuais **rejeitam
`temperature` com HTTP 400** (`"temperature is deprecated for this model"`).
`output_config.effort` é o controle equivalente de profundidade e custo.
`medium` é o padrão porque o objetivo aqui é consistência e bom senso, não
criatividade dispersiva.

**Sem a chave, a aplicação sobe.** `ANTHROPIC_API_KEY` vazia não derruba o boot
(diferente de `JWT_SECRET_KEY`): só a geração de look fica indisponível, com uma
nota explicativa na tela. Derrubar a API inteira por isso trocaria uma
funcionalidade ausente por um serviço fora do ar.

### Etapa 1 — Pré-filtro determinístico (gratuito)

Roda **antes** de qualquer chamada paga, por dois motivos: corta tokens — e
portanto custo — em toda geração, e resolve com uma regra de três linhas uma
decisão que não precisa de modelo de linguagem.

1. **Categorias proibidas pela ocasião** (`occasions.py`) são descartadas. É
   **inviolável** e nunca relaxa: é preferível não montar um look de academia a
   sugerir um blazer para ela.
2. **Peso térmico compatível com o dia.** A temperatura de referência é
   `0.6 × mínima + 0.4 × máxima` — mais peso na mínima porque passar frio é pior
   que passar calor. As faixas: `< 15 °C` frio, `15–25 °C` ameno, `> 25 °C`
   quente. Peça com peso **nulo** passa sempre: o campo fica vazio quando a
   análise não foi conclusiva, e cortar a peça por isso puniria o usuário por uma
   limitação nossa.
3. **Uma única relaxação.** Se o corte térmico não deixa nem um núcleo possível
   (um vestido, ou um par baixo+cima), a seleção volta a ser o guarda-roupa
   inteiro e a resposta vem com uma nota dizendo que o filtro cedeu. A proibição
   da ocasião **não participa** disso.

Se nem assim houver núcleo, a função devolve `looks: []` com uma nota e **não
gasta uma chamada paga**.

### Etapa 2 — Composição pela API

O subconjunto filtrado vai para o Claude junto do clima, da ocasião e dos
**núcleos dos 3 looks mais recentes** do usuário (lidos de `looks_history`), para
a Miranda não repetir na terça a combinação que ditou na segunda.

O **manual de estilo** (`look_prompt.MIRANDA_SYSTEM_PROMPT`) é enviado como
system prompt em toda chamada e cobre:

- **Persona e tom** — editora de moda de altíssimo padrão, fria e decisiva; e a
  regra que governa o resto: *elegância de frase nunca justifica um erro de
  moda*.
- **Estrutura** — baixo + cima, ou vestido sozinho. Vestido nunca acompanha peça
  de baixo. No máximo uma sobreposição, no máximo um acessório.
- **Formalidade** — escala `esporte · casual · smart casual · social`; peças no
  mesmo degrau ou em degraus vizinhos; esporte com social é proibido.
- **Cor** — neutros como base, no máximo uma cor forte por look, ancorada em
  neutros; famílias tonais; disciplina de estampa.
- **Clima** — `serve_chuva` nas posições expostas, sobreposição no vento,
  peças leves no calor.
- **Variedade** — de 2 a 3 looks sem repetir o núcleo; *menos* looks é melhor
  que três quase iguais.
- **Justificativa** — uma frase editorial por look, no espírito de *"Para o sol,
  camisa oxford azul conduz. O resto obedece."*

### Saída estruturada e validação

A resposta é obrigada a ser JSON por **dois** mecanismos: a instrução explícita
no manual e `output_config.format` com um `json_schema` fixo
(`look_prompt.LOOK_RESPONSE_SCHEMA`). O schema fixa o formato, os papéis
possíveis e os rótulos.

> Nota de implementação: o schema **não aceita** `minItems`/`maxItems` — a API
> responde HTTP 400. A cardinalidade (2 a 3 looks) vem do manual, e a validação
> em código faz o resto.

O schema não protege contra tudo, então `_parse_reply` reconfere em código:

| Situação | Tratamento |
|---|---|
| JSON malformado, ou prosa fora do JSON | Falha da chamada → nova tentativa |
| `item_id` que não estava no subconjunto enviado | Falha da chamada → nova tentativa |
| Papel desconhecido, ou peça no papel errado | Falha da chamada → nova tentativa |
| Look que viola a estrutura (vestido + calça) | Aquele look é **descartado**; os válidos passam |
| Nenhum look válido sobrou | Falha da chamada → nova tentativa |
| Mais de 3 looks | Truncado em 3 |

Os rótulos (`I`, `II`, `III`) são **reatribuídos por posição**, não copiados da
resposta: dois `"I"` sairiam errados na tela.

### Tentativas e degradação graciosa

São **3 tentativas** por geração (`ANTHROPIC_MAX_ATTEMPTS`), com esperas de 0,6 s
e 1,5 s. O retry cobre rede, timeout, rate limit, 5xx **e** resposta ilegível —
uma resposta que não dá para interpretar é tão retentável quanto um 429.

Falhas **definitivas** (chave inválida, requisição recusada, modelo inexistente)
não são retentadas: não melhoram na segunda vez e cada tentativa custa.

Esgotadas as tentativas, a resposta é **HTTP 200** com `looks: []` e a nota *"A
Miranda não conseguiu compor o look agora. Tente novamente em instantes."* — o
frontend já renderiza esse estado. **A rota nunca devolve 500.**

O motor de regras antigo **não** sobreviveu como fallback: um motor mantido só
para emergências apodrece sem ninguém perceber, e a degradação honesta é melhor
produto que um look mediano assinado pela Miranda.

### Custo e acompanhamento

Cada chamada registra em log o modelo, os tokens de entrada e saída e o custo
estimado:

```
INFO miranda.ai.claude_client: composição de look — modelo=claude-opus-5
     input_tokens=1832 output_tokens=497 custo_estimado_usd=0.021585
```

Preços em `MODEL_PRICES_USD_PER_MTOK` (`claude_client.py`); para o Opus 5, US$
5,00 por milhão de tokens de entrada e US$ 25,00 de saída. **Não há controle de
quota nesta fase** — o log é o instrumento de acompanhamento.

### Persistência

Cada geração grava um registro em **`looks_history`** (data, temperaturas,
condições, **ocasião**, ids das peças por look em JSONB, e a justificativa). As
condições múltiplas ficam juntas em `condicao_climatica`, separadas por `", "` —
o histórico é lido para auditoria, nunca filtrado por condição isolada. A coluna
`ocasiao` (migration `0002_look_ocasiao`) é **texto e não ENUM** de propósito: a
lista de ocasiões é parâmetro de produto e deve poder crescer sem `ALTER TYPE`.

O histórico também **realimenta a próxima geração**: os núcleos dos 3 looks mais
recentes vão no contexto da chamada seguinte para evitar repetição.

### Calibrar o comportamento

Onde mexer, em ordem de impacto:

1. **`look_prompt.MIRANDA_SYSTEM_PROMPT`** — o gosto da Miranda. É aqui que se
   ajusta o que ela considera erro, o tom das justificativas e a disciplina de
   cor. Mudou aqui, mudou tudo.
2. **`ANTHROPIC_EFFORT`** — profundidade do raciocínio e custo por chamada.
3. **Constantes no topo de `look_generation.py`** — faixas de temperatura, pesos
   aceitáveis por faixa, número máximo de looks, esperas entre tentativas.
4. **`occasions.py`** — categorias proibidas por ocasião.

Para exercitar com um guarda-roupa de demonstração, `scripts/seed_and_test_looks.py`
continua funcionando — **lembrando que agora cada cenário custa uma chamada paga**:

```bash
cd ~/projects/my/miranda-folder/miranda-api
PYTHONPATH=. .venv/bin/python scripts/seed_and_test_looks.py
```
````

- [ ] **Step 6: Atualizar a árvore de arquivos (README, ~linha 774)**

Substitua o bloco `└── ai/` por:

```
│       └── ai/                 # camada de IA — DUAS naturezas, veja abaixo
│           ├── clothing_analysis.py   # [grátis] orquestrador: analyze_clothing_item(...)
│           ├── fashion_clip.py        # [grátis] FashionCLIP zero-shot (lazy singleton)
│           ├── color_extraction.py    # [grátis] cor por k-means + paleta em português
│           ├── rules.py               # [grátis] regras determinísticas (peso/chuva/estações)
│           ├── labels.py              # [grátis] rótulos EN → enums do domínio
│           ├── occasions.py           # [grátis] perfis de ocasião (categorias proibidas)
│           ├── look_generation.py     # [PAGO]  pré-filtro + orquestração da composição
│           ├── look_prompt.py         # [PAGO]  manual de estilo da Miranda + schema JSON
│           └── claude_client.py       # [PAGO]  transporte da API do Claude + log de custo
```

- [ ] **Step 7: Atualizar o parágrafo "Camada de IA" (README, ~linha 790)**

```markdown
**Camada de IA:** `app/services/ai/` reúne duas coisas de naturezas opostas.
`analyze_clothing_item` (inferir atributos de moda a partir da imagem) é **100%
self-hosted e gratuita** (FashionCLIP + k-means + regras — veja a seção 10), e a
rota `POST /api/wardrobe/items/analyze` a expõe. `generate_daily_look` (compor
looks a partir do guarda-roupa e do clima) faz um **pré-filtro determinístico
local** e depois chama a **API paga do Claude** (veja a seção 12); a rota
`POST /api/looks/generate` a expõe. Os campos de moda em `clothing_items` são
anuláveis justamente para serem preenchidos pela IA — ou à mão, quando ela não
determina com confiança.
```

- [ ] **Step 8: Atualizar a seção 15 (README, ~linha 810)**

Substitua a lista de bullets de "Composição de look" por:

```markdown
**Composição de look** (`tests/test_look_generation.py`, `tests/test_look_prompt.py`,
`tests/test_claude_client.py` — rápidos, guarda-roupa fake em memória e **SDK
mockado**: a suíte padrão nunca gasta dinheiro):

- **pré-filtro** (regressão): temperatura de referência, limites das faixas,
  corte por peso térmico incompatível, peça de peso nulo preservada, categorias
  proibidas pela ocasião, detecção de núcleo;
- **interpretação da resposta**: JSON válido, JSON malformado, prosa fora do
  JSON, `item_id` inexistente, papel desconhecido, look estruturalmente inválido
  descartado, rótulos reatribuídos por posição, truncamento em 3 looks;
- **degradação graciosa**: falha transitória retentada e depois degradada, falha
  definitiva não retentada, falha de parse persistente, erro inesperado contido;
- **guarda-roupa insuficiente não chega a chamar a API**;
- **classificação de erro do transporte**: 429/5xx/rede são transitórios,
  400/401/403/404 são definitivos; e a trava que impede alguém de reintroduzir
  `temperature`.

**Histórico** (`tests/test_look_service_history.py`, exige Postgres): os núcleos
recentes viram contexto, o corte por limite funciona, e o histórico de um usuário
nunca vaza para outro.

**Qualidade contra a API real** (`tests/test_look_generation_live.py`) — **custa
dinheiro**, desligado por padrão:

```bash
MIRANDA_LIVE_API_TESTS=1 .venv/bin/python -m pytest tests/test_look_generation_live.py -v -s
```
```

- [ ] **Step 9: Conferir que nenhuma afirmação de "sem custo" sobrou desatualizada**

Run:
```bash
grep -n -i "sem api paga\|sem custo\|sem llm\|determinístic" ../README.md
```
Expected: só ocorrências que se referem à **análise de peça** (seções 10 e 11) e
ao **pré-filtro** da seção 12. Qualquer linha que ainda diga que a *geração de
look* é gratuita ou determinística deve ser corrigida.

- [ ] **Step 10: Commit**

```bash
git add ../README.md app/services/ai/occasions.py
git commit -m "docs: documenta a API paga na geração de look"
```

> Se o README estiver fora do repositório `miranda-api`, comite-o no repositório
> onde ele vive; o resto do comando permanece igual.

---

### Task 8: Validação final com a API real

Não é um teste automatizado: é a prova de qualidade que o dono do projeto pediu
para avaliar na prática, contra o guarda-roupa de verdade que está no banco.

**Files:**
- Create: `scripts/validate_look_live.py`

**Interfaces:**
- Consumes: `generate_daily_look` (Task 4), `claude_client.ClaudeUsage` (Task 3).
- Produces: nada — script de operação.

- [ ] **Step 1: Escrever `scripts/validate_look_live.py`**

```python
"""
Validação manual da qualidade da composição, contra a API REAL e o banco real.

CUSTA DINHEIRO: uma chamada paga por cenário. Não é um teste automatizado e não
roda em suíte — é o instrumento para olhar os looks e julgar se a Miranda está
com bom gosto.

Uso:
    PYTHONPATH=. .venv/bin/python scripts/validate_look_live.py [email-do-usuario]

Sem argumento, escolhe o usuário com mais peças cadastradas.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.clothing_item import ClothingItem
from app.models.user import User
from app.services.ai import claude_client
from app.services.ai.look_generation import generate_daily_look
from app.services.look_service import _item_to_payload

# O log de custo do claude_client é o ponto do exercício — deixe-o visível.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CENARIOS = [
    (
        "Dia quente e ensolarado — dia a dia",
        {"temperatura_min": 23.0, "temperatura_max": 33.0, "condicoes": ["sol"]},
        "dia_a_dia",
    ),
    (
        "Dia frio, com chuva e vento — trabalho",
        {"temperatura_min": 7.0, "temperatura_max": 14.0,
         "condicoes": ["chuva", "frio", "vento"]},
        "trabalho",
    ),
]


def _pick_user(db, email: str | None) -> User:
    if email:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit(f"Usuário '{email}' não encontrado.")
        return user

    row = db.execute(
        select(User, func.count(ClothingItem.id).label("n"))
        .join(ClothingItem, ClothingItem.user_id == User.id)
        .group_by(User.id)
        .order_by(func.count(ClothingItem.id).desc())
        .limit(1)
    ).first()
    if row is None:
        sys.exit("Nenhum usuário com peças cadastradas.")
    return row[0]


def main() -> None:
    if not settings.ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY não configurada no .env.")

    email = sys.argv[1] if len(sys.argv) > 1 else None
    db = SessionLocal()
    try:
        user = _pick_user(db, email)
        items = db.scalars(
            select(ClothingItem).where(ClothingItem.user_id == user.id)
        ).all()
        payload = [_item_to_payload(i) for i in items]
        by_id = {p["id"]: p for p in payload}

        print(f"\nUsuário: {user.email}  ·  {len(payload)} peças")
        print(f"Modelo:  {settings.ANTHROPIC_MODEL}  ·  effort={settings.ANTHROPIC_EFFORT}")

        for titulo, weather, ocasiao in CENARIOS:
            print("\n" + "═" * 72)
            print(f"  {titulo}")
            print(f"  {weather['temperatura_min']}–{weather['temperatura_max']}°C, "
                  f"{', '.join(weather['condicoes'])}")
            print("═" * 72)

            result = generate_daily_look(payload, weather, ocasiao=ocasiao)

            if result["note"]:
                print(f"\n  [nota] {result['note']}")
            for look in result["looks"]:
                print(f"\n  Look {look['label']}")
                for entry in look["items"]:
                    peca = by_id[entry["item_id"]]
                    cor = peca.get("cor_primaria") or "—"
                    print(f"    · {entry['role']:<16} {peca['name']}  ({cor})")
                print(f"    “{look['commentary']}”")
    finally:
        db.close()

    print("\nOs tokens e o custo de cada chamada saíram nas linhas INFO acima.")
    print(f"Preços do modelo: {claude_client.MODEL_PRICES_USD_PER_MTOK.get(settings.ANTHROPIC_MODEL)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rodar contra o guarda-roupa real**

Run:
```bash
PYTHONPATH=. .venv/bin/python scripts/validate_look_live.py teste@gmail.com
```

Expected: dois cenários impressos, cada um com 2 a 3 looks, peças resolvidas por
nome, uma justificativa por look, e duas linhas `INFO miranda.ai.claude_client`
com `input_tokens`, `output_tokens` e `custo_estimado_usd`.

- [ ] **Step 3: Somar e relatar**

Reúna, para entregar ao dono do projeto:
- os looks completos e as justificativas dos dois cenários;
- `input_tokens` e `output_tokens` de cada chamada;
- o custo total das chamadas de validação.

Se a qualidade decepcionar, o ajuste é em `look_prompt.MIRANDA_SYSTEM_PROMPT` ou
em `ANTHROPIC_EFFORT` — **não** em código de composição, que não existe mais.

- [ ] **Step 4: Commit**

```bash
git add scripts/validate_look_live.py
git commit -m "chore(look): script de validação manual contra a API real"
```

---

## Verificação final

- [ ] `.venv/bin/python -m pytest -q` passa (exceto `test_analysis_regression.py`, que exige os pesos do FashionCLIP baixados).
- [ ] `grep -rn "temperature" app/services/ai/` não retorna nenhum uso como parâmetro de API.
- [ ] `grep -rn "sk-ant" app/ scripts/ docs/ tests/ ../README.md` não retorna nada — a chave só existe no `.env` ignorado.
- [ ] `git status` não lista `.env` como arquivo a comitar.
- [ ] Subindo a API sem `ANTHROPIC_API_KEY`, o boot funciona e `POST /api/looks/generate` responde 200 com nota.
- [ ] O frontend em `/look` renderiza os looks vindos da API e a mensagem de indisponibilidade.
