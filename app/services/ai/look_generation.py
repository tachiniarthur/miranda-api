"""
Composição do "look do dia" — 100% determinística, sem LLM nem API paga.

A geração combina três etapas, todas baseadas nos atributos já salvos em
`clothing_items` e no clima informado no formulário:

  1. Pré-filtro térmico/chuva: mapeia a temperatura de referência do dia para os
     pesos térmicos aceitáveis e prioriza peças à prova de chuva nas posições
     mais expostas.
  2. Composição: monta de 2 a 3 looks completos e coerentes (formalidade e cor),
     variando as peças principais entre os looks quando o guarda-roupa permite.
  3. Justificativa: gera uma frase editorial curta por look, a partir de
     templates preenchidos com os atributos reais das peças e do clima.

Filosofia (igual à da análise de peça): degradar graciosamente. Nunca lança
erro por falta de peças — devolve o que for possível, com uma nota quando o
guarda-roupa está limitado para o clima.

Determinismo com variedade: as escolhas pseudo-aleatórias (desempate de peças e
seleção de templates) usam uma semente derivada do clima + do conjunto de peças.
Assim a mesma requisição sempre produz o mesmo resultado (testável), mas dias/
guarda-roupas diferentes produzem looks e frases diferentes.

⚠️ LIMITAÇÃO CONHECIDA — a composição CONFIA NA CATEGORIA armazenada em cada peça
(`category`), que vem da análise de imagem (FashionCLIP) ou do preenchimento
manual. A estrutura de um look (nunca vestido com peça de baixo; nunca duas peças
de baixo) é decidida por essa categoria, não por reconhecimento visual em tempo
de composição. Portanto, **a qualidade da composição depende da qualidade da
categorização**: um vestido rotulado por engano como `saia` será tratado como
peça de baixo e combinado com uma peça de cima — o que parece um erro de
composição, mas tem origem na categoria errada. Como nenhum classificador é
perfeito, `_look_structure_is_valid` age como uma rede de segurança barata que
rejeita, na saída, qualquer look estruturalmente inválido segundo a própria
categoria (defesa em profundidade contra regressões futuras da lógica de montagem
— não corrige categoria errada, mas garante que a REGRA nunca seja violada dado o
que as categorias dizem).
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections import Counter
from typing import Any, Optional, TypedDict

logger = logging.getLogger("miranda.ai.look_generation")


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de entrada/saída
# ─────────────────────────────────────────────────────────────────────────────
class WeatherInfo(TypedDict):
    temperatura_min: float
    temperatura_max: float
    condicao_climatica: str


class SuggestedLookItem(TypedDict):
    item_id: str
    role: str


class SuggestedLook(TypedDict):
    label: str
    items: list[SuggestedLookItem]
    commentary: str


class DailyLookResult(TypedDict):
    looks: list[SuggestedLook]
    # Nota opcional exibida quando o guarda-roupa está limitado para o clima
    # (poucas peças, filtro relaxado, etc.). None quando a composição foi plena.
    note: Optional[str]


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

# Rótulos de papel exibidos ao usuário (PT).
ROLE_BOTTOM = "peça de baixo"
ROLE_TOP = "peça de cima"
ROLE_OUTER = "sobreposição"
ROLE_DRESS = "peça única"
ROLE_FOOTWEAR = "calçado"
ROLE_SCARF = "acessório"
ROLE_ACCESSORY = "acessório"

# ── Faixas de temperatura (°C) → pesos térmicos aceitáveis ───────────────────
# A temperatura de referência dá MAIS peso à mínima ("vestir por segurança":
# é pior passar frio do que calor), por isso 0.6*min + 0.4*max.
TEMP_MIN_WEIGHT = 0.6
TEMP_MAX_WEIGHT = 0.4

# Cortes das faixas (ajustáveis). Abaixo de COLD_MAX = frio; entre COLD_MAX e
# MILD_MAX = ameno; acima de MILD_MAX = quente.
COLD_MAX = 15.0   # temp_ref < 15  → frio
MILD_MAX = 25.0   # 15 <= temp_ref <= 25 → ameno ; > 25 → quente

# Pesos térmicos aceitos em cada faixa. Peças fora do conjunto (com peso
# conhecido e incompatível) são cortadas no pré-filtro; peças com peso NULO
# passam sempre (política permissiva) com uma penalização de prioridade.
BAND_COLD = "frio"
BAND_MILD = "ameno"
BAND_HOT = "quente"
ACCEPTABLE_PESO: dict[str, set[str]] = {
    BAND_COLD: {"pesado", "medio"},
    BAND_MILD: {"medio", "leve"},
    BAND_HOT: {"leve"},
}

# ── Escala de formalidade ────────────────────────────────────────────────────
# Ordinal onde a distância <= 1 = "adjacente" (combinável). "esporte" fica ao
# lado de "casual" e longe de "social" — é o que evita o par esporte×social,
# citado no spec como incoerente.
FORMALITY_RANK: dict[str, int] = {
    "esporte": 0,
    "casual": 1,
    "smart_casual": 2,
    "social": 3,
}

# ── Coordenação de cor ───────────────────────────────────────────────────────
# Neutros combinam com quase tudo; cores fortes não devem competir entre si
# (dois tons fortes DIFERENTES no mesmo look é evitado). Cor nula/desconhecida
# é tratada como neutra (permissivo). Casamento por prefixo do nome de cor
# (os nomes vêm de color_extraction, ex.: "azul-marinho", "cinza-claro").
NEUTRAL_COLOR_PREFIXES = (
    "preto", "cinza", "branco", "off-white", "bege", "caramelo",
    "marrom", "nude", "areia", "creme", "grafite", "chumbo",
)
# Nomes que começam com um prefixo "forte" mas são, na prática, neutros de base
# (avaliados antes de STRONG_COLOR_FAMILIES).
NEUTRAL_STRONG_LOOKALIKES = ("azul-marinho",)
# Prefixo do nome → família de cor forte (para detectar tons que competem).
STRONG_COLOR_FAMILIES: dict[str, str] = {
    "vermelho": "vermelho", "vinho": "vermelho", "coral": "vermelho",
    "rosa": "rosa",
    "laranja": "laranja",
    "mostarda": "amarelo", "amarelo": "amarelo",
    "verde": "verde",
    "azul": "azul", "turquesa": "azul",
    "roxo": "roxo", "lilás": "roxo", "lilas": "roxo",
}

MAX_LOOKS = 3
MIN_DESIRED_LOOKS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Utilitários de clima e coerência
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


def _normalize_condition(condicao: str) -> dict[str, bool]:
    """Deriva flags do clima a partir da condição livre informada."""
    c = (condicao or "").strip().lower()
    rainy = "chuva" in c
    windy = "vento" in c
    cold_signal = "frio" in c
    return {"rainy": rainy, "windy": windy, "cold_signal": cold_signal}


def _color_family(cor: Optional[str]) -> str:
    """Mapeia um nome de cor para 'neutro' ou uma família de cor forte."""
    if not cor:
        return "neutro"
    name = cor.strip().lower()
    # Neutros "de fato" que começam com um prefixo forte e precisam ser tratados
    # ANTES dele — o azul-marinho (navy) é um neutro clássico de alfaiataria,
    # não um azul que compete por atenção.
    for prefix in NEUTRAL_STRONG_LOOKALIKES:
        if name.startswith(prefix):
            return "neutro"
    for prefix, family in STRONG_COLOR_FAMILIES.items():
        if name.startswith(prefix):
            return family
    for prefix in NEUTRAL_COLOR_PREFIXES:
        if name.startswith(prefix):
            return "neutro"
    # Desconhecida: tratada como neutra (permissivo, não corta o look).
    return "neutro"


def _colors_ok(pieces: list[dict[str, Any]]) -> bool:
    """Coerente se houver no máximo UMA família de cor forte distinta."""
    strong = {
        fam
        for p in pieces
        if (fam := _color_family(p.get("cor_primaria"))) != "neutro"
    }
    return len(strong) <= 1


def _formality_ok(a: Optional[str], b: Optional[str]) -> bool:
    """Formalidades adjacentes (dist <= 1). Nulo é curinga (combina com tudo)."""
    if a is None or b is None:
        return True
    ra, rb = FORMALITY_RANK.get(a), FORMALITY_RANK.get(b)
    if ra is None or rb is None:
        return True
    return abs(ra - rb) <= 1


def _look_formality_ok(pieces: list[dict[str, Any]]) -> bool:
    ranks = [
        FORMALITY_RANK[p["formalidade"]]
        for p in pieces
        if p.get("formalidade") in FORMALITY_RANK
    ]
    if len(ranks) < 2:
        return True
    return max(ranks) - min(ranks) <= 1


# ─────────────────────────────────────────────────────────────────────────────
# Prioridade das peças (quanto maior, melhor)
# ─────────────────────────────────────────────────────────────────────────────
def _piece_priority(
    piece: dict[str, Any], band: str, flags: dict[str, bool], exposed: bool
) -> float:
    """
    Pontua uma peça para o dia. Peças com peso térmico definido e compatível são
    preferidas; peças com atributos nulos entram (permissivo) mas com leve
    penalização. Em dia de chuva, peças expostas à prova de chuva ganham pontos.
    """
    score = 0.0
    peso = piece.get("peso_termico")
    if peso is None:
        score -= 0.5  # permissivo, mas menos confiável
    else:
        score += 1.0
        if peso in ACCEPTABLE_PESO[band]:
            score += 0.5

    if piece.get("formalidade") is not None:
        score += 0.5  # peça com etiqueta clara é mais fácil de coordenar

    if flags["rainy"] and exposed:
        if piece.get("serve_chuva") is True:
            score += 1.0
        elif piece.get("serve_chuva") is False:
            score -= 0.5

    return score


# ─────────────────────────────────────────────────────────────────────────────
# Pré-filtro térmico
# ─────────────────────────────────────────────────────────────────────────────
def _thermal_prefilter(items: list[dict[str, Any]], band: str) -> list[dict[str, Any]]:
    """
    Mantém peças cujo peso térmico é compatível com a faixa OU nulo (permissivo).
    Peças com peso conhecido e incompatível são removidas.
    """
    acceptable = ACCEPTABLE_PESO[band]
    kept = []
    for it in items:
        peso = it.get("peso_termico")
        if peso is None or peso in acceptable:
            kept.append(it)
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# Montagem dos looks
# ─────────────────────────────────────────────────────────────────────────────
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


class _Base:
    """Núcleo de um look (peça única, ou baixo+cima), antes de complementos."""

    def __init__(self, mains: list[tuple[dict[str, Any], str]], score: float) -> None:
        self.mains = mains  # lista de (peça, papel)
        self.score = score

    @property
    def main_ids(self) -> set[str]:
        return {p["id"] for p, _ in self.mains}


def _build_bases(
    slots: dict[str, list[dict[str, Any]]],
    band: str,
    flags: dict[str, bool],
    rng: random.Random,
) -> tuple[list[_Base], bool]:
    """
    Constrói os núcleos candidatos (vestidos e pares baixo+cima coerentes).

    Retorna (bases, coerencia_relaxada). Se não houver nenhum par coerente mas
    existirem baixo e cima, relaxa a coerência (usa todos os pares) e sinaliza.
    """
    bases: list[_Base] = []

    # Vestidos: cada um é um núcleo por si só.
    for dress in slots["dresses"]:
        pr = _piece_priority(dress, band, flags, exposed=False)
        bases.append(_Base([(dress, ROLE_DRESS)], pr))

    # Pares baixo+cima coerentes (formalidade adjacente + cor coordenada).
    coherent_pairs: list[_Base] = []
    for bottom in slots["bottoms"]:
        for top in slots["tops"]:
            if not _formality_ok(bottom.get("formalidade"), top.get("formalidade")):
                continue
            if not _colors_ok([bottom, top]):
                continue
            score = (
                _piece_priority(bottom, band, flags, exposed=False)
                + _piece_priority(top, band, flags, exposed=False)
            )
            coherent_pairs.append(_Base([(bottom, ROLE_BOTTOM), (top, ROLE_TOP)], score))

    relaxed = False
    if coherent_pairs:
        bases.extend(coherent_pairs)
    elif slots["bottoms"] and slots["tops"]:
        # Sem par coerente: relaxa (melhor um look imperfeito do que nenhum).
        relaxed = True
        for bottom in slots["bottoms"]:
            for top in slots["tops"]:
                score = (
                    _piece_priority(bottom, band, flags, exposed=False)
                    + _piece_priority(top, band, flags, exposed=False)
                )
                bases.append(_Base([(bottom, ROLE_BOTTOM), (top, ROLE_TOP)], score))

    # Ordena por score (desc) com desempate pseudo-aleatório estável.
    rng.shuffle(bases)
    bases.sort(key=lambda b: b.score, reverse=True)
    return bases, relaxed


def _select_varied(bases: list[_Base]) -> list[_Base]:
    """
    Seleciona até MAX_LOOKS núcleos priorizando VARIEDADE das peças principais:
    a cada passo escolhe o núcleo de maior (novidade, score), onde novidade é o
    número de peças principais ainda não usadas. Evita duplicatas exatas.
    """
    selected: list[_Base] = []
    used_ids: set[str] = set()
    used_signatures: set[frozenset[str]] = set()

    remaining = list(bases)
    while remaining and len(selected) < MAX_LOOKS:
        def key(b: _Base) -> tuple[int, float]:
            novelty = len(b.main_ids - used_ids)
            return (novelty, b.score)

        best = max(remaining, key=key)
        remaining.remove(best)

        sig = frozenset(best.main_ids)
        if sig in used_signatures:
            continue  # duplicata exata de um look já escolhido
        # Após já termos o mínimo desejado, exige que o núcleo traga ao menos uma
        # peça principal nova (evita looks que só repetem peças).
        if selected and len(selected) >= MIN_DESIRED_LOOKS:
            if not (best.main_ids - used_ids):
                continue

        selected.append(best)
        used_signatures.add(sig)
        used_ids |= best.main_ids

    return selected


def _pick_complement(
    candidates: list[dict[str, Any]],
    used_ids: set[str],
    base_pieces: list[dict[str, Any]],
    band: str,
    flags: dict[str, bool],
    exposed: bool,
    rng: random.Random,
) -> Optional[dict[str, Any]]:
    """
    Escolhe UMA peça complementar (sobreposição/calçado/acessório) compatível com
    o núcleo (formalidade + cor), preferindo peças ainda não usadas em outro look
    e, em dia de chuva para posições expostas, as à prova de chuva.
    """
    viable = []
    for c in candidates:
        pieces = base_pieces + [c]
        if not _look_formality_ok(pieces):
            continue
        if not _colors_ok(pieces):
            continue
        viable.append(c)
    if not viable:
        return None

    def key(c: dict[str, Any]) -> tuple[int, float]:
        novel = 0 if c["id"] in used_ids else 1
        pr = _piece_priority(c, band, flags, exposed)
        return (novel, pr)

    rng.shuffle(viable)
    viable.sort(key=key, reverse=True)
    return viable[0]


# ─────────────────────────────────────────────────────────────────────────────
# Justificativa editorial (templates)
# ─────────────────────────────────────────────────────────────────────────────
_CONDITION_PHRASE: dict[str, str] = {
    "sol": "o sol",
    "nublado": "o céu fechado",
    "chuva": "a chuva",
    "vento": "o vento",
    "frio": "o frio",
}
_FORMALITY_WORD: dict[str, str] = {
    "esporte": "esportivo",
    "casual": "casual",
    "smart_casual": "smart casual",
    "social": "de rigor social",
}


def _condition_phrase(condicao: str) -> str:
    c = (condicao or "").strip().lower()
    for key, phrase in _CONDITION_PHRASE.items():
        if key in c:
            return phrase
    return "o dia"


def _predominant_formality(pieces: list[dict[str, Any]]) -> Optional[str]:
    vals = [p["formalidade"] for p in pieces if p.get("formalidade")]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def _anchor_piece(pieces: list[dict[str, Any]]) -> dict[str, Any]:
    """A peça mais 'protagonista': vestido > sobreposição > cima > primeira."""
    order = {ROLE_DRESS: 0, ROLE_OUTER: 1, ROLE_TOP: 2, ROLE_BOTTOM: 3}
    return min(pieces, key=lambda p: order.get(p.get("_role", ""), 9))


def _strong_color_piece(pieces: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for p in pieces:
        if _color_family(p.get("cor_primaria")) != "neutro":
            return p
    return None


def _compose_commentary(
    pieces: list[dict[str, Any]],
    weather: WeatherInfo,
    band: str,
    flags: dict[str, bool],
    rng: random.Random,
) -> str:
    """
    Monta uma frase curta e editorial escolhendo, entre templates elegíveis, um
    template cujas variáveis correspondem ao que o look realmente tem.
    """
    clima = _condition_phrase(weather["condicao_climatica"])
    formal = _predominant_formality(pieces)
    formal_word = _FORMALITY_WORD.get(formal or "", "")
    anchor = _anchor_piece(pieces)
    anchor_name = (anchor.get("name") or anchor.get("category") or "a peça").strip()
    strong = _strong_color_piece(pieces)
    rain_piece = next(
        (p for p in pieces if p.get("serve_chuva") is True), None
    )
    faixa = f"{int(round(weather['temperatura_min']))}°–{int(round(weather['temperatura_max']))}°"

    templates: list[str] = []

    # Sempre elegíveis (usam só clima/âncora/faixa).
    templates.append(f"Para {clima}, {anchor_name.lower()} conduz. O resto obedece.")
    templates.append(f"{faixa}. A escolha é {anchor_name.lower()} — e nada que a contrarie.")
    templates.append(f"{clima.capitalize()} pede clareza. Este conjunto não hesita.")

    if band == BAND_COLD:
        templates.append("Frio se enfrenta em camadas, não em excesso. Aqui, cada peça tem função.")
        templates.append(f"Contra {clima}, a lógica é agasalhar sem perder a linha.")
    elif band == BAND_HOT:
        templates.append("Calor não justifica descuido. Leveza com intenção.")
        templates.append(f"Sob {clima}, menos tecido, mais precisão.")
    else:
        templates.append("Meia-estação premia quem sabe dosar. Este look sabe.")

    if formal_word:
        templates.append(f"Um registro {formal_word}, sustentado do começo ao fim.")
        templates.append(f"{anchor_name} define o tom {formal_word}. O conjunto acompanha.")

    if strong is not None:
        cor = (strong.get("cor_primaria") or "").strip()
        if cor:
            templates.append(f"O {cor} é o único ponto de cor permitido — e é o suficiente.")
            templates.append(f"Neutros ancoram, o {cor} decide. Contraste sob controle.")

    if flags["rainy"] and rain_piece is not None:
        nome = (rain_piece.get("name") or rain_piece.get("category") or "a peça").strip()
        templates.append(f"A chuva não intimida: {nome.lower()} responde por ela.")
        templates.append("Chuva prevista, elegância mantida — a proteção está onde importa.")

    return rng.choice(templates)


# ─────────────────────────────────────────────────────────────────────────────
# Função pública
# ─────────────────────────────────────────────────────────────────────────────
def _roman(n: int) -> str:
    numerals = ["I", "II", "III", "IV", "V", "VI"]
    return numerals[n] if 0 <= n < len(numerals) else str(n + 1)


def _seed_from(items: list[dict[str, Any]], weather: WeatherInfo) -> int:
    ids = ",".join(sorted(str(i.get("id")) for i in items))
    key = (
        f"{ids}|{weather['temperatura_min']}|{weather['temperatura_max']}"
        f"|{weather['condicao_climatica']}"
    )
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _assemble_look(
    base: _Base,
    slots: dict[str, list[dict[str, Any]]],
    used_complement_ids: set[str],
    band: str,
    flags: dict[str, bool],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Expande um núcleo com sobreposição/calçado/acessório compatíveis."""
    # Peças do núcleo, anotadas com o papel (para a justificativa e a saída).
    pieces: list[dict[str, Any]] = []
    for p, role in base.mains:
        pc = dict(p)
        pc["_role"] = role
        pieces.append(pc)

    wants_outer = band == BAND_COLD or flags["cold_signal"] or flags["rainy"] or flags["windy"]
    wants_scarf = band == BAND_COLD or flags["cold_signal"]
    is_dress = any(r == ROLE_DRESS for _, r in base.mains)

    # Sobreposição (quando o clima pede e há peça compatível).
    if wants_outer and slots["outers"]:
        outer = _pick_complement(
            slots["outers"], used_complement_ids, pieces, band, flags,
            exposed=True, rng=rng,
        )
        if outer is not None:
            oc = dict(outer)
            oc["_role"] = ROLE_OUTER
            pieces.append(oc)

    # Calçado (parte de um look completo; opcional se o guarda-roupa não tiver).
    if slots["footwear"]:
        shoe = _pick_complement(
            slots["footwear"], used_complement_ids, pieces, band, flags,
            exposed=True, rng=rng,
        )
        if shoe is not None:
            sc = dict(shoe)
            sc["_role"] = ROLE_FOOTWEAR
            pieces.append(sc)

    # Cachecol (frio) e/ou um acessório (opcional), no máximo um de cada.
    if wants_scarf and slots["scarves"]:
        scarf = _pick_complement(
            slots["scarves"], used_complement_ids, pieces, band, flags,
            exposed=False, rng=rng,
        )
        if scarf is not None:
            fc = dict(scarf)
            fc["_role"] = ROLE_SCARF
            pieces.append(fc)

    if slots["accessories"]:
        acc = _pick_complement(
            slots["accessories"], used_complement_ids, pieces, band, flags,
            exposed=False, rng=rng,
        )
        if acc is not None:
            ac = dict(acc)
            ac["_role"] = ROLE_ACCESSORY
            pieces.append(ac)

    _ = is_dress  # (explicitação: vestido nunca ganha peça de baixo/cima)
    return pieces


def _look_structure_is_valid(pieces: list[dict[str, Any]]) -> bool:
    """
    Rede de segurança estrutural (defesa em profundidade): valida um look montado
    contra as regras invioláveis, olhando SOMENTE a `category` de cada peça.

    Regras:
      - Um vestido é peça única: não pode coexistir com peça de baixo (calça/saia)
        nem com peça de cima (camisa/malha), e nunca há dois vestidos.
      - Sem vestido, há no máximo UMA peça de baixo (calça e saia são ambas peça
        de baixo — nunca duas no mesmo look).

    Não reclassifica imagem nem conserta categoria errada: apenas garante que,
    dadas as categorias, a composição jamais viole a estrutura. Por construção os
    looks já saem válidos; este cheque protege contra regressões da montagem e
    torna a invariante testável de forma automatizada.
    """
    cats = [p.get("category") for p in pieces]
    n_bottom = sum(1 for c in cats if c in BOTTOMS)
    n_top = sum(1 for c in cats if c in TOPS)
    n_dress = sum(1 for c in cats if c in DRESSES)

    if n_dress:
        return n_dress == 1 and n_bottom == 0 and n_top == 0
    return n_bottom <= 1


def generate_daily_look(
    items: list[dict[str, Any]],
    weather: WeatherInfo,
) -> DailyLookResult:
    """
    Gera de 2 a 3 sugestões de look para o dia, de forma determinística.

    Args:
        items: peças do usuário (cada uma como dict com id e atributos de moda).
        weather: mínima, máxima e condição climática do dia.

    Returns:
        DailyLookResult com a lista de looks (cada um com peças+papéis e uma
        justificativa) e uma nota opcional quando o guarda-roupa está limitado.
        Nunca lança por falta de peças — degrada graciosamente.
    """
    rng = random.Random(_seed_from(items, weather))
    temp_ref = _reference_temp(weather)
    band = _band_for(temp_ref)
    flags = _normalize_condition(weather["condicao_climatica"])

    notes: list[str] = []

    # ── Etapa 1: pré-filtro térmico ─────────────────────────────────────────
    filtered = _thermal_prefilter(items, band)
    slots = _partition(filtered)

    have_core = bool(slots["dresses"]) or (bool(slots["bottoms"]) and bool(slots["tops"]))
    if not have_core:
        # Filtro térmico deixou o guarda-roupa sem núcleo possível: relaxa para
        # todas as peças (ignora peso) antes de desistir.
        slots = _partition(items)
        have_core = bool(slots["dresses"]) or (
            bool(slots["bottoms"]) and bool(slots["tops"])
        )
        if have_core:
            notes.append(
                "Poucas peças combinam com esta temperatura; ampliei a seleção "
                "para montar algo apresentável."
            )

    if not have_core:
        # Impossível montar um look completo. Degrada com uma nota clara.
        return DailyLookResult(
            looks=[],
            note=(
                "Ainda não há peças suficientes para compor um look completo. "
                "Cadastre ao menos uma parte de baixo e uma de cima — ou um "
                "vestido — para a Miranda trabalhar."
            ),
        )

    # ── Etapa 2: composição ─────────────────────────────────────────────────
    bases, relaxed = _build_bases(slots, band, flags, rng)
    if relaxed:
        notes.append(
            "As combinações possíveis misturam registros diferentes; priorizei "
            "o que o guarda-roupa permite."
        )

    selected = _select_varied(bases)

    looks: list[SuggestedLook] = []
    used_complement_ids: set[str] = set()
    for base in selected:
        pieces = _assemble_look(
            base, slots, used_complement_ids, band, flags, rng
        )

        # Rede de segurança: descarta um look que viole a estrutura (nunca deveria
        # acontecer por construção — se acontecer, sinaliza bug de montagem).
        if not _look_structure_is_valid(pieces):
            logger.warning(
                "Look estruturalmente inválido descartado (categorias: %s) — "
                "verifique a lógica de montagem.",
                [p.get("category") for p in pieces],
            )
            continue

        # Marca complementos como usados para dar variedade entre os looks.
        for p in pieces:
            if p.get("_role") in (ROLE_OUTER, ROLE_FOOTWEAR, ROLE_SCARF, ROLE_ACCESSORY):
                used_complement_ids.add(p["id"])

        commentary = _compose_commentary(pieces, weather, band, flags, rng)
        looks.append(
            SuggestedLook(
                label=_roman(len(looks)),  # numeração romana só dos looks válidos
                items=[
                    SuggestedLookItem(item_id=str(p["id"]), role=p["_role"])
                    for p in pieces
                ],
                commentary=commentary,
            )
        )

    if len(looks) < MIN_DESIRED_LOOKS:
        notes.append(
            "O guarda-roupa ainda é enxuto para este clima — cadastre mais peças "
            "para receber opções variadas."
        )

    note = " ".join(notes) if notes else None
    return DailyLookResult(looks=looks, note=note)
