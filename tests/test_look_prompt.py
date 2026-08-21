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
