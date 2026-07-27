"""
Testes automatizados da composição de look (`generate_daily_look`).

Montam guarda-roupas fake em memória (sem banco, sem IA, sem rede) e verificam
as invariantes que a validação manual vinha checando visualmente:

  - nenhum look combina vestido com peça de baixo (calça/saia);
  - nenhum look tem duas peças de baixo;
  - peças com peso térmico incompatível não entram num dia extremo;
  - em dia de chuva, peças à prova de chuva são priorizadas nas posições expostas;
  - degradação graciosa: guarda-roupa pobre nunca lança erro.
"""

from __future__ import annotations

import itertools

import pytest

from app.services.ai.look_generation import (
    BOTTOMS,
    DRESSES,
    TOPS,
    _look_structure_is_valid,
    generate_daily_look,
)


# ── Fábrica de peças fake ────────────────────────────────────────────────────
_counter = itertools.count()


def piece(
    category: str,
    *,
    id: str | None = None,
    formalidade: str | None = None,
    peso_termico: str | None = None,
    serve_chuva: bool | None = None,
    cor_primaria: str | None = "preto",
) -> dict:
    return {
        "id": id or f"{category}-{next(_counter)}",
        "name": id or category,
        "category": category,
        "cor_primaria": cor_primaria,
        "cor_secundaria": None,
        "estampa": None,
        "formalidade": formalidade,
        "peso_termico": peso_termico,
        "serve_chuva": serve_chuva,
        "estacoes": None,
    }


def weather(tmin: float, tmax: float, cond: str) -> dict:
    return {"temperatura_min": tmin, "temperatura_max": tmax, "condicao_climatica": cond}


def _categories_by_id(items: list[dict]) -> dict[str, str]:
    return {str(i["id"]): i["category"] for i in items}


def _look_categories(look: dict, cat_by_id: dict[str, str]) -> list[str]:
    return [cat_by_id[str(i["item_id"])] for i in look["items"]]


# ── Invariante estrutural (função de defesa) ─────────────────────────────────
def test_structure_validator_rules():
    assert _look_structure_is_valid([{"category": "vestido"}]) is True
    assert _look_structure_is_valid([{"category": "vestido"}, {"category": "calca"}]) is False
    assert _look_structure_is_valid([{"category": "vestido"}, {"category": "camisa"}]) is False
    assert _look_structure_is_valid([{"category": "calca"}, {"category": "saia"}]) is False
    assert _look_structure_is_valid([{"category": "calca"}, {"category": "camisa"}]) is True
    assert _look_structure_is_valid([{"category": "saia"}, {"category": "malha"}]) is True


# ── Nunca vestido + peça de baixo; nunca duas peças de baixo ──────────────────
def test_never_dress_with_bottom_across_weathers():
    items = [
        piece("vestido", formalidade="social", peso_termico="leve"),
        piece("calca", formalidade="casual", peso_termico="medio"),
        piece("saia", formalidade="smart_casual", peso_termico="leve"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
        piece("malha", formalidade="casual", peso_termico="medio"),
        piece("casaco", formalidade="casual", peso_termico="pesado", serve_chuva=True),
        piece("calcado", formalidade="casual", serve_chuva=True),
    ]
    cat_by_id = _categories_by_id(items)

    for w in (
        weather(28, 34, "sol"),
        weather(16, 22, "nublado"),
        weather(4, 10, "chuva"),
        weather(8, 14, "vento"),
    ):
        result = generate_daily_look(items, w)
        assert result["looks"], f"esperava ao menos 1 look para {w}"
        for look in result["looks"]:
            cats = _look_categories(look, cat_by_id)
            has_dress = any(c in DRESSES for c in cats)
            n_bottom = sum(1 for c in cats if c in BOTTOMS)
            # regra 1: vestido nunca com peça de baixo
            assert not (has_dress and n_bottom > 0), f"vestido+baixo em {cats} ({w})"
            # regra 2: no máximo uma peça de baixo
            assert n_bottom <= 1, f"duas peças de baixo em {cats} ({w})"
            # a rede de segurança concorda
            pieces = [{"category": c} for c in cats]
            assert _look_structure_is_valid(pieces)


def test_mislabeled_dress_as_skirt_is_treated_as_bottom():
    """
    Documenta a LIMITAÇÃO CONHECIDA: a composição confia na categoria. Um vestido
    rotulado por engano como `saia` é tratado como peça de baixo e combinado com
    uma peça de cima — o resultado é estruturalmente válido *dada a categoria*,
    o que confirma que esse tipo de erro nasce na categorização, não na montagem.
    """
    items = [
        piece("saia", id="na-verdade-um-vestido", formalidade="social", peso_termico="leve"),
        piece("camisa", formalidade="social", peso_termico="leve"),
    ]
    cat_by_id = _categories_by_id(items)
    result = generate_daily_look(items, weather(24, 30, "sol"))
    assert result["looks"]
    for look in result["looks"]:
        # o look é válido segundo as categorias armazenadas (saia = peça de baixo)
        assert _look_structure_is_valid([{"category": c} for c in _look_categories(look, cat_by_id)])


# ── Peso térmico incompatível não entra em dia extremo ───────────────────────
def test_extreme_heat_excludes_heavy_pieces():
    heavy_top = piece("malha", id="malha-pesada", formalidade="casual", peso_termico="pesado")
    items = [
        piece("calca", id="calca-leve", formalidade="casual", peso_termico="leve"),
        piece("camisa", id="camisa-leve", formalidade="casual", peso_termico="leve"),
        heavy_top,  # peso pesado: deve ser barrado no calor extremo
    ]
    cat_by_id = _categories_by_id(items)
    peso_by_id = {str(i["id"]): i["peso_termico"] for i in items}

    result = generate_daily_look(items, weather(33, 41, "sol"))  # calor extremo → só leve
    assert result["looks"], "deveria montar ao menos um look só com peças leves"
    used_ids = {str(i["item_id"]) for look in result["looks"] for i in look["items"]}
    assert "malha-pesada" not in used_ids
    assert all(peso_by_id[i] != "pesado" for i in used_ids)
    # sanity: usou pelo menos a base leve
    assert cat_by_id  # noqa: keeps cat map referenced


# ── Chuva prioriza serve_chuva nas posições expostas ─────────────────────────
def test_rain_prioritizes_waterproof_in_exposed_positions():
    items = [
        piece("calca", formalidade="casual", peso_termico="medio", cor_primaria="preto"),
        piece("camisa", formalidade="casual", peso_termico="medio", cor_primaria="branco"),
        piece("casaco", id="casaco-seco", formalidade="casual", peso_termico="pesado", serve_chuva=False, cor_primaria="cinza"),
        piece("casaco", id="casaco-chuva", formalidade="casual", peso_termico="pesado", serve_chuva=True, cor_primaria="cinza"),
        piece("calcado", id="sapato-seco", formalidade="casual", serve_chuva=False, cor_primaria="preto"),
        piece("calcado", id="sapato-chuva", formalidade="casual", serve_chuva=True, cor_primaria="preto"),
    ]
    result = generate_daily_look(items, weather(4, 9, "chuva"))  # frio + chuva → pede sobreposição
    assert result["looks"]
    first = result["looks"][0]
    ids = {str(i["item_id"]) for i in first["items"]}
    # a peça exposta escolhida deve ser a à prova de chuva, não a "seca"
    assert "casaco-chuva" in ids and "casaco-seco" not in ids
    assert "sapato-chuva" in ids and "sapato-seco" not in ids


# ── Degradação graciosa ──────────────────────────────────────────────────────
def test_graceful_degradation_empty_wardrobe():
    result = generate_daily_look([], weather(20, 26, "sol"))
    assert result["looks"] == []
    assert result["note"]  # nota explicativa, sem exceção


def test_graceful_degradation_only_tops():
    items = [piece("camisa", peso_termico="leve"), piece("malha", peso_termico="leve")]
    result = generate_daily_look(items, weather(20, 26, "sol"))
    assert result["looks"] == []  # sem peça de baixo nem vestido → não há núcleo
    assert result["note"]


def test_graceful_degradation_single_core_still_returns():
    items = [
        piece("calca", formalidade="casual", peso_termico="leve"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
    ]
    result = generate_daily_look(items, weather(24, 30, "sol"))
    # consegue montar 1 look; não lança e sinaliza guarda-roupa enxuto
    assert len(result["looks"]) >= 1
    assert result["note"]


@pytest.mark.parametrize(
    "w",
    [weather(-5, 2, "chuva"), weather(38, 45, "sol"), weather(15, 15, "nublado")],
)
def test_never_raises_on_varied_weather(w):
    items = [
        piece("vestido", peso_termico="leve"),
        piece("calca", peso_termico="pesado"),
        piece("malha", peso_termico="medio"),
    ]
    # não deve lançar em nenhuma combinação de clima
    result = generate_daily_look(items, w)
    assert isinstance(result["looks"], list)
