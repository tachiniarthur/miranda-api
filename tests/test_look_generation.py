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
