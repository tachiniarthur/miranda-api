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
