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
