"""
Testes automatizados da composição de look (`generate_daily_look`).

Montam guarda-roupas fake em memória (sem banco, sem IA, sem rede) e verificam
as invariantes que a validação manual vinha checando visualmente:

  - nenhum look combina vestido com peça de baixo (calça/saia);
  - nenhum look tem duas peças de baixo;
  - peças com peso térmico incompatível não entram num dia extremo;
  - em dia de chuva, peças à prova de chuva são priorizadas nas posições expostas;
  - condições climáticas COMBINADAS acumulam efeitos (chuva+vento);
  - a OCASIÃO muda o look de verdade: alvo de formalidade, camada, cor e
    categorias proibidas;
  - degradação graciosa: guarda-roupa pobre nunca lança erro.
"""

from __future__ import annotations

import itertools

import pytest

from app.services.ai.look_generation import (
    BOTTOMS,
    DRESSES,
    TOPS,
    _condition_flags,
    _condition_phrase,
    _look_structure_is_valid,
    generate_daily_look,
)
from app.services.ai.occasions import OCCASION_PROFILES, get_profile


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


def weather(tmin: float, tmax: float, *conds: str) -> dict:
    """Clima com uma ou mais condições combinadas."""
    return {
        "temperatura_min": tmin,
        "temperatura_max": tmax,
        "condicoes": list(conds),
    }


def _categories_by_id(items: list[dict]) -> dict[str, str]:
    return {str(i["id"]): i["category"] for i in items}


def _look_categories(look: dict, cat_by_id: dict[str, str]) -> list[str]:
    return [cat_by_id[str(i["item_id"])] for i in look["items"]]


def _all_ids(result: dict) -> set[str]:
    return {str(i["item_id"]) for look in result["looks"] for i in look["items"]}


def _roles(look: dict) -> list[str]:
    return [i["role"] for i in look["items"]]


# ── Invariante estrutural (função de defesa) ─────────────────────────────────
def test_structure_validator_rules():
    assert _look_structure_is_valid([{"category": "vestido"}]) is True
    assert _look_structure_is_valid([{"category": "vestido"}, {"category": "calca"}]) is False
    assert _look_structure_is_valid([{"category": "vestido"}, {"category": "camisa"}]) is False
    assert _look_structure_is_valid([{"category": "calca"}, {"category": "saia"}]) is False
    assert _look_structure_is_valid([{"category": "calca"}, {"category": "camisa"}]) is True
    assert _look_structure_is_valid([{"category": "saia"}, {"category": "malha"}]) is True


def test_structure_validator_enforces_forbidden_categories():
    """A proibição da ocasião entra na mesma rede de segurança da estrutura."""
    esporte = get_profile("esporte")
    trabalho = get_profile("trabalho")
    look = [{"category": "calca"}, {"category": "camisa"}, {"category": "blazer"}]

    assert _look_structure_is_valid(look, trabalho) is True
    assert _look_structure_is_valid(look, esporte) is False   # blazer na academia
    assert _look_structure_is_valid(look) is True             # sem ocasião, só estrutura


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
        weather(6, 11, "chuva", "vento", "frio"),
    ):
        for ocasiao in OCCASION_PROFILES:
            result = generate_daily_look(items, w, ocasiao)
            for look in result["looks"]:
                cats = _look_categories(look, cat_by_id)
                has_dress = any(c in DRESSES for c in cats)
                n_bottom = sum(1 for c in cats if c in BOTTOMS)
                # regra 1: vestido nunca com peça de baixo
                assert not (has_dress and n_bottom > 0), f"vestido+baixo em {cats} ({w}, {ocasiao})"
                # regra 2: no máximo uma peça de baixo
                assert n_bottom <= 1, f"duas peças de baixo em {cats} ({w}, {ocasiao})"
                # a rede de segurança concorda, inclusive quanto à ocasião
                pieces = [{"category": c} for c in cats]
                assert _look_structure_is_valid(pieces, get_profile(ocasiao))


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
    result = generate_daily_look(items, weather(24, 30, "sol"), "evento_formal")
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
    peso_by_id = {str(i["id"]): i["peso_termico"] for i in items}

    result = generate_daily_look(items, weather(33, 41, "sol"), "dia_a_dia")
    assert result["looks"], "deveria montar ao menos um look só com peças leves"
    used_ids = _all_ids(result)
    assert "malha-pesada" not in used_ids
    assert all(peso_by_id[i] != "pesado" for i in used_ids)


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
    result = generate_daily_look(items, weather(4, 9, "chuva"), "dia_a_dia")
    assert result["looks"]
    first = result["looks"][0]
    ids = {str(i["item_id"]) for i in first["items"]}
    # a peça exposta escolhida deve ser a à prova de chuva, não a "seca"
    assert "casaco-chuva" in ids and "casaco-seco" not in ids
    assert "sapato-chuva" in ids and "sapato-seco" not in ids


# ── Condições climáticas combinadas ──────────────────────────────────────────
def test_condition_flags_accumulate():
    """Marcar várias condições liga várias flags — elas não se sobrescrevem."""
    flags = _condition_flags(["sol", "vento"])
    assert flags["sunny"] and flags["windy"]
    assert not flags["rainy"] and not flags["cold_signal"]

    flags = _condition_flags(["chuva", "vento", "frio"])
    assert flags["rainy"] and flags["windy"] and flags["cold_signal"]

    assert not any(_condition_flags([]).values())


def test_condition_phrase_combines_naturally():
    assert _condition_phrase(["sol"]) == "o sol"
    # o estado do céu abre a frase, como se fala
    assert _condition_phrase(["sol", "vento"]) == "o sol com vento"
    assert _condition_phrase(["sol", "chuva"]) == "o sol com chuva"
    assert _condition_phrase(["frio", "nublado"]) == "o céu fechado com frio"
    # sem céu marcado, o modificador mais determinante assume a cabeça
    assert _condition_phrase(["vento", "chuva"]) == "a chuva com vento"
    # a ordem do encadeamento não depende da ordem de clique
    assert _condition_phrase(["vento", "sol", "chuva"]) == "o sol com chuva e vento"
    assert _condition_phrase([]) == "o dia"


def test_order_of_conditions_does_not_change_the_result():
    """Marcar "sol, vento" ou "vento, sol" é a mesma informação."""
    items = [
        piece("calca", formalidade="casual", peso_termico="medio"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
        piece("malha", formalidade="casual", peso_termico="medio"),
        piece("casaco", formalidade="casual", peso_termico="medio"),
    ]
    a = generate_daily_look(items, weather(16, 22, "sol", "vento"), "trabalho")
    b = generate_daily_look(items, weather(16, 22, "vento", "sol"), "trabalho")
    assert a == b


def test_wind_alone_asks_for_an_outer_layer():
    """Vento é motivo suficiente para sobreposição, mesmo em dia ameno."""
    items = [
        piece("calca", formalidade="casual", peso_termico="medio"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
        piece("casaco", id="corta-vento", formalidade="casual", peso_termico="medio"),
    ]
    calmo = generate_daily_look(items, weather(20, 24, "sol"), "dia_a_dia")
    ventoso = generate_daily_look(items, weather(20, 24, "sol", "vento"), "dia_a_dia")

    assert "corta-vento" not in _all_ids(calmo)
    assert "corta-vento" in _all_ids(ventoso)


# ── Ocasião ──────────────────────────────────────────────────────────────────
def _formal_wardrobe() -> list[dict]:
    return [
        piece("calca", id="calca-social", formalidade="social", peso_termico="medio", cor_primaria="preto"),
        piece("camisa", id="camisa-social", formalidade="social", peso_termico="leve", cor_primaria="branco"),
        piece("calca", id="calca-moletom", formalidade="esporte", peso_termico="medio", cor_primaria="cinza"),
        piece("malha", id="malha-esporte", formalidade="esporte", peso_termico="leve", cor_primaria="cinza"),
        piece("blazer", id="blazer-social", formalidade="social", peso_termico="medio", cor_primaria="preto"),
        piece("calcado", id="sapato-social", formalidade="social", cor_primaria="preto"),
        piece("calcado", id="tenis", formalidade="esporte", cor_primaria="branco"),
    ]


def test_occasion_targets_the_right_formality_register():
    """O mesmo guarda-roupa responde a registros opostos com peças opostas."""
    items = _formal_wardrobe()
    w = weather(18, 24, "sol")

    entrevista = _all_ids(generate_daily_look(items, w, "entrevista"))
    assert "calca-social" in entrevista and "camisa-social" in entrevista
    assert "calca-moletom" not in entrevista and "malha-esporte" not in entrevista

    esporte = _all_ids(generate_daily_look(items, w, "esporte"))
    assert "calca-moletom" in esporte and "malha-esporte" in esporte
    assert "calca-social" not in esporte and "camisa-social" not in esporte


def test_occasion_forbidden_categories_are_never_used():
    """Proibição é inviolável — nem em guarda-roupa pobre o blazer vai à academia."""
    items = [
        piece("calca", formalidade="esporte", peso_termico="leve"),
        piece("malha", formalidade="esporte", peso_termico="leve"),
        piece("blazer", formalidade="social", peso_termico="medio"),
        piece("vestido", formalidade="social", peso_termico="leve"),
        piece("saia", formalidade="casual", peso_termico="leve"),
    ]
    cat_by_id = _categories_by_id(items)
    result = generate_daily_look(items, weather(18, 24, "sol"), "esporte")
    assert result["looks"]

    forbidden = get_profile("esporte").forbidden_categories
    for look in result["looks"]:
        cats = _look_categories(look, cat_by_id)
        assert not (set(cats) & forbidden), f"categoria proibida em {cats}"


def test_forbidden_categories_can_make_a_look_impossible():
    """
    Guarda-roupa só de vestidos + ocasião esportiva: a resposta honesta é não
    montar nada e explicar, em vez de sugerir um vestido para a academia.
    """
    items = [
        piece("vestido", formalidade="social", peso_termico="leve"),
        piece("saia", formalidade="social", peso_termico="leve"),
        piece("calcado", formalidade="social"),
    ]
    result = generate_daily_look(items, weather(20, 26, "sol"), "esporte")
    assert result["looks"] == []
    assert result["note"] and "ocasião" in result["note"].lower()


def test_occasion_relaxes_register_with_an_explanatory_note():
    """Registro é preferência forte: sem peça social, monta o possível e avisa."""
    items = [
        piece("calca", formalidade="esporte", peso_termico="medio"),
        piece("malha", formalidade="esporte", peso_termico="medio"),
    ]
    result = generate_daily_look(items, weather(18, 24, "sol"), "evento_formal")
    assert result["looks"], "deveria degradar graciosamente, não devolver vazio"
    assert result["note"] and "registro" in result["note"].lower()


def test_layering_occasions_add_an_outer_without_cold_weather():
    """Reunião pede blazer mesmo em dia ameno; dia a dia, não."""
    items = [
        piece("calca", formalidade="smart_casual", peso_termico="medio", cor_primaria="preto"),
        piece("camisa", formalidade="smart_casual", peso_termico="leve", cor_primaria="branco"),
        piece("blazer", id="blazer", formalidade="social", peso_termico="medio", cor_primaria="preto"),
    ]
    w = weather(20, 25, "sol")  # ameno: o clima não pede camada nenhuma

    assert "blazer" in _all_ids(generate_daily_look(items, w, "reuniao"))
    assert "blazer" not in _all_ids(generate_daily_look(items, w, "dia_a_dia"))


def test_neutral_discipline_rejects_strong_colors():
    """Entrevista não admite cor forte; jantar romântico a premia."""
    items = [
        piece("calca", id="calca-preta", formalidade="social", peso_termico="medio", cor_primaria="preto"),
        piece("camisa", id="camisa-branca", formalidade="social", peso_termico="leve", cor_primaria="off-white"),
        piece("camisa", id="camisa-vermelha", formalidade="social", peso_termico="leve", cor_primaria="vermelho"),
    ]
    w = weather(20, 25, "sol")

    entrevista = _all_ids(generate_daily_look(items, w, "entrevista"))
    assert "camisa-vermelha" not in entrevista
    assert "camisa-branca" in entrevista

    jantar = _all_ids(generate_daily_look(items, w, "jantar_romantico"))
    assert "camisa-vermelha" in jantar


def test_dress_bonus_can_win_against_a_pair():
    """
    Regressão: a novidade em `_select_varied` era uma contagem absoluta, então um
    par baixo+cima (2 peças novas) sempre batia um vestido (1 peça nova) antes de
    o score ser olhado — e o bônus de vestido das ocasiões nunca se manifestava.
    Com a novidade normalizada, o vestido volta a poder liderar quando merece.
    """
    items = [
        piece("vestido", id="vestido-social", formalidade="social", peso_termico="leve", cor_primaria="vinho"),
        piece("calca", id="calca", formalidade="smart_casual", peso_termico="leve", cor_primaria="preto"),
        piece("camisa", id="camisa", formalidade="smart_casual", peso_termico="leve", cor_primaria="branco"),
    ]
    w = weather(19, 24, "nublado")

    jantar = generate_daily_look(items, w, "jantar_romantico")
    assert "vestido-social" in _all_ids(jantar), "o vestido deveria concorrer"
    # e, com o bônus da ocasião, ele encabeça a lista
    assert any(
        str(i["item_id"]) == "vestido-social" for i in jantar["looks"][0]["items"]
    ), "o vestido deveria liderar num jantar romântico"


def test_accessory_is_skipped_when_the_occasion_does_not_want_one():
    """Shopping e viagem dispensam acessório extra (mãos ocupadas, bagagem)."""
    items = [
        piece("calca", formalidade="casual", peso_termico="leve"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
        piece("acessorio", id="bolsa", formalidade="casual"),
    ]
    w = weather(20, 25, "sol")

    assert "bolsa" in _all_ids(generate_daily_look(items, w, "dia_a_dia"))
    assert "bolsa" not in _all_ids(generate_daily_look(items, w, "shopping"))
    assert "bolsa" not in _all_ids(generate_daily_look(items, w, "viagem"))


def test_comfort_bias_avoids_heavy_pieces_when_weather_allows():
    """
    Viagem (conforto máximo) evita a peça pesada num dia ameno; reunião, que não
    tem viés de conforto, aceita a mesma peça no mesmo clima.
    """
    items = [
        piece("calca", formalidade="smart_casual", peso_termico="medio", cor_primaria="preto"),
        piece("camisa", formalidade="smart_casual", peso_termico="leve", cor_primaria="branco"),
        piece("casaco", id="casaco-leve", formalidade="smart_casual", peso_termico="medio", cor_primaria="cinza"),
        piece("casaco", id="casaco-pesado", formalidade="smart_casual", peso_termico="pesado", cor_primaria="cinza"),
    ]
    w = weather(19, 24, "nublado")  # ameno: nada aqui justifica peso

    viagem = generate_daily_look(items, w, "viagem")
    outer_ids = {
        str(i["item_id"])
        for look in viagem["looks"]
        for i in look["items"]
        if i["role"] == "sobreposição"
    }
    assert outer_ids, "viagem tem layering_bias alto, deveria escolher sobreposição"
    assert "casaco-pesado" not in outer_ids


def test_unknown_or_missing_occasion_falls_back_to_dia_a_dia():
    items = [
        piece("calca", formalidade="casual", peso_termico="medio"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
    ]
    w = weather(20, 25, "sol")
    baseline = generate_daily_look(items, w, "dia_a_dia")
    assert generate_daily_look(items, w) == baseline
    assert generate_daily_look(items, w, "ocasiao-que-nao-existe") == baseline


def test_different_occasions_produce_different_looks():
    """
    Guarda o que motivou a granularidade: 12 ocasiões só se justificam se
    produzirem resultados de fato distintos. Aqui, num guarda-roupa rico, as
    ocasiões não podem colapsar todas na mesma resposta.
    """
    items = [
        piece("calca", id="calca-social", formalidade="social", peso_termico="medio", cor_primaria="preto"),
        piece("calca", id="jeans", formalidade="casual", peso_termico="medio", cor_primaria="azul-marinho"),
        piece("calca", id="moletom", formalidade="esporte", peso_termico="medio", cor_primaria="cinza"),
        piece("saia", id="saia-linho", formalidade="smart_casual", peso_termico="leve", cor_primaria="bege"),
        piece("camisa", id="camisa-seda", formalidade="social", peso_termico="leve", cor_primaria="off-white"),
        piece("camisa", id="oxford", formalidade="smart_casual", peso_termico="leve", cor_primaria="azul"),
        piece("malha", id="malha-casual", formalidade="casual", peso_termico="medio", cor_primaria="cinza"),
        piece("malha", id="malha-esporte", formalidade="esporte", peso_termico="leve", cor_primaria="preto"),
        piece("vestido", id="vestido-social", formalidade="social", peso_termico="leve", cor_primaria="vinho"),
        piece("blazer", id="blazer", formalidade="social", peso_termico="medio", cor_primaria="preto"),
        piece("casaco", id="casaco", formalidade="casual", peso_termico="pesado", cor_primaria="grafite"),
        piece("calcado", id="sapato", formalidade="social", cor_primaria="preto"),
        piece("calcado", id="tenis", formalidade="esporte", cor_primaria="branco"),
        piece("acessorio", id="bolsa", formalidade="casual", cor_primaria="marrom"),
    ]
    w = weather(18, 24, "sol")

    signatures = {
        ocasiao: frozenset(_all_ids(generate_daily_look(items, w, ocasiao)))
        for ocasiao in OCCASION_PROFILES
    }
    # Não exigimos 12 respostas únicas (registros vizinhos podem coincidir com
    # um guarda-roupa finito), mas um colapso geral significaria que a ocasião
    # não está fazendo nada.
    assert len(set(signatures.values())) >= 6, signatures


# ── Determinismo ─────────────────────────────────────────────────────────────
def test_same_request_is_deterministic():
    items = [
        piece("calca", formalidade="casual", peso_termico="medio"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
        piece("malha", formalidade="casual", peso_termico="medio"),
    ]
    w = weather(16, 22, "sol", "vento")
    assert generate_daily_look(items, w, "trabalho") == generate_daily_look(items, w, "trabalho")


# ── Degradação graciosa ──────────────────────────────────────────────────────
def test_graceful_degradation_empty_wardrobe():
    result = generate_daily_look([], weather(20, 26, "sol"), "dia_a_dia")
    assert result["looks"] == []
    assert result["note"]  # nota explicativa, sem exceção


def test_graceful_degradation_only_tops():
    items = [piece("camisa", peso_termico="leve"), piece("malha", peso_termico="leve")]
    result = generate_daily_look(items, weather(20, 26, "sol"), "dia_a_dia")
    assert result["looks"] == []  # sem peça de baixo nem vestido → não há núcleo
    assert result["note"]


def test_graceful_degradation_single_core_still_returns():
    items = [
        piece("calca", formalidade="casual", peso_termico="leve"),
        piece("camisa", formalidade="casual", peso_termico="leve"),
    ]
    result = generate_daily_look(items, weather(24, 30, "sol"), "dia_a_dia")
    # consegue montar 1 look; não lança e sinaliza guarda-roupa enxuto
    assert len(result["looks"]) >= 1
    assert result["note"]


@pytest.mark.parametrize(
    "w",
    [
        weather(-5, 2, "chuva"),
        weather(38, 45, "sol"),
        weather(15, 15, "nublado"),
        weather(10, 14, "chuva", "vento", "frio"),
        weather(30, 36, "sol", "vento"),
        weather(20, 25),  # nenhuma condição marcada
    ],
)
@pytest.mark.parametrize("ocasiao", list(OCCASION_PROFILES))
def test_never_raises_on_varied_weather_and_occasion(w, ocasiao):
    items = [
        piece("vestido", peso_termico="leve"),
        piece("calca", peso_termico="pesado"),
        piece("malha", peso_termico="medio"),
    ]
    # não deve lançar em nenhuma combinação de clima × ocasião
    result = generate_daily_look(items, w, ocasiao)
    assert isinstance(result["looks"], list)
