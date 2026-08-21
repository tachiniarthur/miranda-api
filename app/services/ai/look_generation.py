"""
Composição do "look do dia" — 100% determinística, sem LLM nem API paga.

A geração combina três etapas, todas baseadas nos atributos já salvos em
`clothing_items`, no clima informado e na ocasião escolhida no formulário:

  1. Pré-filtros: descarta o que a OCASIÃO não admite (inviolável), mapeia a
     temperatura de referência do dia para os pesos térmicos aceitáveis, mantém
     o que está no REGISTRO da ocasião e prioriza peças à prova de chuva nas
     posições mais expostas.
  2. Composição: monta de 2 a 3 looks completos e coerentes (formalidade, cor e
     ocasião), variando as peças principais entre os looks quando o
     guarda-roupa permite.
  3. Justificativa: gera uma frase editorial curta por look, a partir de
     templates preenchidos com os atributos reais das peças, do clima e da
     ocasião.

── Entradas do usuário ──────────────────────────────────────────────────────
· CONDIÇÕES CLIMÁTICAS são MÚLTIPLAS (`weather["condicoes"]` é uma lista): sol
  com vento, chuva com frio, etc. As flags derivadas são independentes entre si
  e se acumulam — chuva+vento pede sobreposição pelos dois motivos.
· OCASIÃO é ÚNICA e vira um `OccasionProfile` (ver `occasions.py`), que informa
  alvo de formalidade, viés de conforto, viés de sobreposição, disciplina de cor
  e categorias favorecidas/proibidas.

Filosofia (igual à da análise de peça): degradar graciosamente. Nunca lança
erro por falta de peças — devolve o que for possível, com uma nota quando o
guarda-roupa está limitado para o clima ou para a ocasião.

Determinismo com variedade: as escolhas pseudo-aleatórias (desempate de peças e
seleção de templates) usam uma semente derivada do clima + da ocasião + do
conjunto de peças. Assim a mesma requisição sempre produz o mesmo resultado
(testável), mas dias/ocasiões/guarda-roupas diferentes produzem looks e frases
diferentes.

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

from app.services.ai.occasions import (
    COLOR_NEUTRAL,
    COLOR_STATEMENT,
    FORMALITY_SCALE_SPAN,
    OccasionProfile,
    get_profile,
)

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
    # Nota opcional exibida quando o guarda-roupa está limitado para o clima ou
    # para a ocasião (poucas peças, filtro relaxado, etc.). None quando a
    # composição foi plena.
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
# citado no spec como incoerente. É também a escala em que a ocasião posiciona
# seu alvo (ver `occasions.py`).
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

# ── Pesos da ocasião na prioridade das peças ─────────────────────────────────
# Calibração: o encaixe de formalidade (até +1.2) tem peso comparável ao do peso
# térmico compatível (+1.5), de modo que a ocasião influencia de verdade sem
# atropelar o conforto térmico. A penalidade por estar fora do registro (−1.0)
# existe para o caso da relaxação, quando peças fora de registro voltam a
# concorrer: elas entram, mas por último.
OCCASION_FIT_WEIGHT = 1.2
OCCASION_OFF_REGISTER_PENALTY = 1.0
OCCASION_NULL_FORMALITY_PENALTY = 0.3
# Peça pesada incomoda quando a ocasião envolve andar E o clima não exige.
COMFORT_HEAVY_PENALTY = 0.6
# Prêmio por ponto de cor nas ocasiões de "destaque".
STATEMENT_COLOR_BONUS = 0.4
# A partir deste viés, a ocasião pede sobreposição mesmo com clima ameno.
LAYERING_THRESHOLD = 0.6

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


def _condition_flags(condicoes: list[str]) -> dict[str, bool]:
    """
    Deriva flags independentes a partir das condições marcadas.

    As condições são combináveis, então as flags se ACUMULAM: ["chuva", "vento"]
    liga `rainy` e `windy` ao mesmo tempo, e cada uma pesa por si na composição
    (chuva prioriza peça impermeável; vento pede sobreposição).
    """
    joined = " ".join((c or "").strip().lower() for c in condicoes)
    return {
        "rainy": "chuva" in joined,
        "windy": "vento" in joined,
        "cold_signal": "frio" in joined,
        "sunny": "sol" in joined,
        "cloudy": "nublado" in joined,
    }


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


def _max_strong_families(profile: OccasionProfile) -> int:
    """
    Quantas famílias de cor forte a ocasião tolera num mesmo look.

    Ocasiões de disciplina "neutro" (reunião, entrevista, viagem) não admitem
    nenhuma: o look não deve competir com quem o veste, nem exigir combinação
    fina numa mala. As demais mantêm o teto histórico de uma.
    """
    return 0 if profile.color_discipline == COLOR_NEUTRAL else 1


def _colors_ok(pieces: list[dict[str, Any]], profile: OccasionProfile) -> bool:
    """Coerente se as famílias de cor forte couberem no teto da ocasião."""
    strong = {
        fam
        for p in pieces
        if (fam := _color_family(p.get("cor_primaria"))) != "neutro"
    }
    return len(strong) <= _max_strong_families(profile)


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
# Encaixe na ocasião
# ─────────────────────────────────────────────────────────────────────────────
def _formality_distance(
    piece: dict[str, Any], profile: OccasionProfile
) -> Optional[float]:
    """Distância da peça ao alvo da ocasião. None quando a peça não tem registro."""
    rank = FORMALITY_RANK.get(piece.get("formalidade"))
    if rank is None:
        return None
    return abs(rank - profile.formality_target)


def _in_register(piece: dict[str, Any], profile: OccasionProfile) -> bool:
    """
    Peça dentro do registro da ocasião.

    Formalidade nula é CURINGA e entra (mesma política permissiva do peso
    térmico): a IA de análise deixa esse campo nulo com frequência, e excluir
    essas peças esvaziaria o guarda-roupa de quem não preencheu tudo à mão.
    """
    dist = _formality_distance(piece, profile)
    return dist is None or dist <= profile.formality_tolerance


def _occasion_score(
    piece: dict[str, Any], profile: OccasionProfile, band: str
) -> float:
    """Contribuição da ocasião para a prioridade de uma peça."""
    score = 0.0

    dist = _formality_distance(piece, profile)
    if dist is None:
        # Curinga: entra, mas é uma aposta cega — vale menos que uma peça que
        # comprovadamente pertence ao registro.
        score -= OCCASION_NULL_FORMALITY_PENALTY
    else:
        score += OCCASION_FIT_WEIGHT * (1.0 - dist / FORMALITY_SCALE_SPAN)
        if dist > profile.formality_tolerance:
            score -= OCCASION_OFF_REGISTER_PENALTY

    score += profile.category_bonus.get(piece.get("category"), 0.0)

    # Conforto: só penaliza peça pesada quando o frio NÃO a justifica.
    if band != BAND_COLD and piece.get("peso_termico") == "pesado":
        score -= COMFORT_HEAVY_PENALTY * profile.comfort_bias

    # Nas ocasiões de destaque, um ponto de cor é ativo — não só tolerado.
    if profile.color_discipline == COLOR_STATEMENT:
        if _color_family(piece.get("cor_primaria")) != "neutro":
            score += STATEMENT_COLOR_BONUS

    return score


# ─────────────────────────────────────────────────────────────────────────────
# Prioridade das peças (quanto maior, melhor)
# ─────────────────────────────────────────────────────────────────────────────
def _piece_priority(
    piece: dict[str, Any],
    band: str,
    flags: dict[str, bool],
    exposed: bool,
    profile: OccasionProfile,
) -> float:
    """
    Pontua uma peça para o dia e para a ocasião. Peças com peso térmico definido
    e compatível são preferidas; peças com atributos nulos entram (permissivo)
    mas com leve penalização. Em dia de chuva, peças expostas à prova de chuva
    ganham pontos. A ocasião entra por `_occasion_score`.
    """
    score = 0.0
    peso = piece.get("peso_termico")
    if peso is None:
        score -= 0.5  # permissivo, mas menos confiável
    else:
        score += 1.0
        if peso in ACCEPTABLE_PESO[band]:
            score += 0.5

    if flags["rainy"] and exposed:
        if piece.get("serve_chuva") is True:
            score += 1.0
        elif piece.get("serve_chuva") is False:
            score -= 0.5

    score += _occasion_score(piece, profile, band)
    return score


# ─────────────────────────────────────────────────────────────────────────────
# Pré-filtros
# ─────────────────────────────────────────────────────────────────────────────
def _drop_forbidden(
    items: list[dict[str, Any]], profile: OccasionProfile
) -> list[dict[str, Any]]:
    """
    Remove as categorias que a ocasião NÃO ADMITE.

    Este filtro é inviolável: nunca participa da cascata de relaxação. É
    preferível não montar um look de academia a sugerir um blazer para ela.
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
    kept = []
    for it in items:
        peso = it.get("peso_termico")
        if peso is None or peso in acceptable:
            kept.append(it)
    return kept


def _register_filter(
    items: list[dict[str, Any]], profile: OccasionProfile
) -> list[dict[str, Any]]:
    """Mantém apenas peças no registro da ocasião (formalidade nula é curinga)."""
    return [i for i in items if _in_register(i, profile)]


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


def _have_core(slots: dict[str, list[dict[str, Any]]]) -> bool:
    """Há núcleo possível: um vestido, ou um par baixo+cima."""
    return bool(slots["dresses"]) or (bool(slots["bottoms"]) and bool(slots["tops"]))


class _Base:
    """Núcleo de um look (peça única, ou baixo+cima), antes de complementos."""

    def __init__(self, mains: list[tuple[dict[str, Any], str]], score: float) -> None:
        self.mains = mains  # lista de (peça, papel)
        self.score = score  # soma bruta das prioridades das peças principais

    @property
    def main_ids(self) -> set[str]:
        return {p["id"] for p, _ in self.mains}

    @property
    def quality(self) -> float:
        """
        Prioridade MÉDIA por peça principal — a métrica com que núcleos de
        formatos diferentes se comparam.

        A soma bruta não serve para isso: um par baixo+cima soma duas
        prioridades e um vestido só uma, então o par vencia por ser composto de
        duas peças, não por ser a melhor escolha. Na média, um vestido excelente
        supera um par medíocre, que é o comportamento esperado — e é o que
        permite às ocasiões que favorecem vestido (jantar romântico, evento
        formal) efetivamente propô-lo.
        """
        return self.score / len(self.mains)


def _build_bases(
    slots: dict[str, list[dict[str, Any]]],
    band: str,
    flags: dict[str, bool],
    profile: OccasionProfile,
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
        pr = _piece_priority(dress, band, flags, False, profile)
        bases.append(_Base([(dress, ROLE_DRESS)], pr))

    # Pares baixo+cima coerentes (formalidade adjacente + cor coordenada).
    coherent_pairs: list[_Base] = []
    for bottom in slots["bottoms"]:
        for top in slots["tops"]:
            if not _formality_ok(bottom.get("formalidade"), top.get("formalidade")):
                continue
            if not _colors_ok([bottom, top], profile):
                continue
            score = (
                _piece_priority(bottom, band, flags, False, profile)
                + _piece_priority(top, band, flags, False, profile)
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
                    _piece_priority(bottom, band, flags, False, profile)
                    + _piece_priority(top, band, flags, False, profile)
                )
                bases.append(_Base([(bottom, ROLE_BOTTOM), (top, ROLE_TOP)], score))

    # Ordena por qualidade média (desc) com desempate pseudo-aleatório estável.
    # Média, e não soma, para que vestido e par disputem em pé de igualdade.
    rng.shuffle(bases)
    bases.sort(key=lambda b: b.quality, reverse=True)
    return bases, relaxed


def _select_varied(bases: list[_Base]) -> list[_Base]:
    """
    Seleciona até MAX_LOOKS núcleos priorizando VARIEDADE das peças principais:
    a cada passo escolhe o núcleo de maior (novidade, score). Evita duplicatas.

    A novidade é uma FRAÇÃO (peças inéditas ÷ peças do núcleo), não uma contagem
    absoluta. A diferença importa: um vestido é um núcleo de uma peça e um par
    baixo+cima é de duas, então na contagem absoluta o par vencia sempre por
    2 > 1 — antes mesmo de o score ser consultado. Nenhum vestido chegava a ser
    o primeiro look enquanto existisse um par qualquer, e as preferências de
    ocasião por vestido (jantar romântico, evento formal) não tinham como se
    manifestar. Normalizada, "totalmente novo" vale 1.0 nos dois formatos e o
    desempate volta a ser o mérito da peça.
    """
    selected: list[_Base] = []
    used_ids: set[str] = set()
    used_signatures: set[frozenset[str]] = set()

    remaining = list(bases)
    while remaining and len(selected) < MAX_LOOKS:
        def key(b: _Base) -> tuple[float, float]:
            novelty = len(b.main_ids - used_ids) / len(b.main_ids)
            return (novelty, b.quality)

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
    profile: OccasionProfile,
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
        if not _colors_ok(pieces, profile):
            continue
        viable.append(c)
    if not viable:
        return None

    def key(c: dict[str, Any]) -> tuple[int, float]:
        novel = 0 if c["id"] in used_ids else 1
        pr = _piece_priority(c, band, flags, exposed, profile)
        return (novel, pr)

    rng.shuffle(viable)
    viable.sort(key=key, reverse=True)
    return viable[0]


# ─────────────────────────────────────────────────────────────────────────────
# Justificativa editorial (templates)
# ─────────────────────────────────────────────────────────────────────────────
# Duas formas de cada condição: uma com artigo (abre a frase) e uma nua (encadeia
# depois de "com"). Assim ["sol", "vento"] vira "o sol com vento", e não o
# canhestro "o sol com o vento".
_CONDITION_MAIN: dict[str, str] = {
    "chuva": "a chuva",
    "frio": "o frio",
    "vento": "o vento",
    "nublado": "o céu fechado",
    "sol": "o sol",
}
_CONDITION_BARE: dict[str, str] = {
    "chuva": "chuva",
    "frio": "frio",
    "vento": "vento",
    "nublado": "céu fechado",
    "sol": "sol",
}
# Como as condições se encadeiam na frase. O ESTADO DO CÉU abre ("o sol com
# vento", "o céu fechado com frio"), que é como se fala; os MODIFICADORES vêm
# depois, do mais determinante para o menos. Sem céu marcado, o modificador mais
# determinante assume a cabeça ("a chuva com vento").
_SKY_CONDITIONS = ("sol", "nublado")
_MODIFIER_PRIORITY = ("chuva", "frio", "vento")
_CONDITION_PRIORITY = _SKY_CONDITIONS + _MODIFIER_PRIORITY

_FORMALITY_WORD: dict[str, str] = {
    "esporte": "esportivo",
    "casual": "casual",
    "smart_casual": "smart casual",
    "social": "de rigor social",
}


def _condition_phrase(condicoes: list[str]) -> str:
    """Monta a expressão do clima combinando todas as condições marcadas."""
    joined = " ".join((c or "").strip().lower() for c in condicoes)
    present = [c for c in _CONDITION_PRIORITY if c in joined]
    if not present:
        return "o dia"

    head_key = next((c for c in _SKY_CONDITIONS if c in present), None)
    if head_key is None:
        head_key = next(c for c in _MODIFIER_PRIORITY if c in present)

    # Modificadores primeiro; um segundo estado de céu (caso raro de marcar sol
    # e nublado juntos) fecha a frase.
    tail = [c for c in _MODIFIER_PRIORITY + _SKY_CONDITIONS if c in present and c != head_key]

    head = _CONDITION_MAIN[head_key]
    rest = [_CONDITION_BARE[c] for c in tail]
    if not rest:
        return head
    if len(rest) == 1:
        return f"{head} com {rest[0]}"
    return f"{head} com {', '.join(rest[:-1])} e {rest[-1]}"


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
    profile: OccasionProfile,
    rng: random.Random,
) -> str:
    """
    Monta uma frase curta e editorial escolhendo, entre templates elegíveis, um
    template cujas variáveis correspondem ao que o look realmente tem.
    """
    clima = _condition_phrase(weather["condicoes"])
    formal = _predominant_formality(pieces)
    formal_word = _FORMALITY_WORD.get(formal or "", "")
    anchor = _anchor_piece(pieces)
    anchor_name = (anchor.get("name") or anchor.get("category") or "a peça").strip()
    strong = _strong_color_piece(pieces)
    rain_piece = next(
        (p for p in pieces if p.get("serve_chuva") is True), None
    )
    has_outer = any(p.get("_role") == ROLE_OUTER for p in pieces)
    faixa = f"{int(round(weather['temperatura_min']))}°–{int(round(weather['temperatura_max']))}°"

    templates: list[str] = []

    # Sempre elegíveis (usam só clima/âncora/faixa).
    templates.append(f"Para {clima}, {anchor_name.lower()} conduz. O resto obedece.")
    templates.append(f"{faixa}. A escolha é {anchor_name.lower()} — e nada que a contrarie.")
    templates.append(f"{clima.capitalize()} pede clareza. Este conjunto não hesita.")

    # ── Ocasião: sempre elegíveis, é a informação mais específica que temos ──
    templates.append(f"Para {profile.phrase}, {anchor_name.lower()} resolve sem alarde.")
    templates.append(f"{profile.label} sob {clima}: o registro não se negocia.")

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
            if profile.color_discipline == COLOR_STATEMENT:
                templates.append(f"O {cor} é o convidado de honra. O resto sustenta.")
            templates.append(f"O {cor} é o único ponto de cor permitido — e é o suficiente.")
            templates.append(f"Neutros ancoram, o {cor} decide. Contraste sob controle.")
    elif profile.color_discipline == COLOR_NEUTRAL:
        templates.append("Neutros do começo ao fim — nada aqui disputa atenção com você.")

    if profile.comfort_bias >= 0.7:
        templates.append("Horas de pé pedem leveza. Este conjunto não cobra pedágio.")

    if has_outer and profile.layering_bias >= LAYERING_THRESHOLD:
        templates.append("A camada extra não é enfeite: é o que salva a mudança de ambiente.")

    if flags["rainy"] and rain_piece is not None:
        nome = (rain_piece.get("name") or rain_piece.get("category") or "a peça").strip()
        templates.append(f"A chuva não intimida: {nome.lower()} responde por ela.")
        templates.append("Chuva prevista, elegância mantida — a proteção está onde importa.")

    if flags["windy"] and has_outer:
        templates.append("Vento é detalhe quando a sobreposição está resolvida.")

    return rng.choice(templates)


# ─────────────────────────────────────────────────────────────────────────────
# Função pública
# ─────────────────────────────────────────────────────────────────────────────
def _roman(n: int) -> str:
    numerals = ["I", "II", "III", "IV", "V", "VI"]
    return numerals[n] if 0 <= n < len(numerals) else str(n + 1)


def _seed_from(
    items: list[dict[str, Any]], weather: WeatherInfo, profile: OccasionProfile
) -> int:
    ids = ",".join(sorted(str(i.get("id")) for i in items))
    # As condições entram ORDENADAS: marcar "sol, vento" ou "vento, sol" é a
    # mesma informação e deve produzir exatamente o mesmo look.
    condicoes = ",".join(sorted((c or "").strip().lower() for c in weather["condicoes"]))
    key = (
        f"{ids}|{weather['temperatura_min']}|{weather['temperatura_max']}"
        f"|{condicoes}|{profile.key}"
    )
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _assemble_look(
    base: _Base,
    slots: dict[str, list[dict[str, Any]]],
    used_complement_ids: set[str],
    band: str,
    flags: dict[str, bool],
    profile: OccasionProfile,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Expande um núcleo com sobreposição/calçado/acessório compatíveis."""
    # Peças do núcleo, anotadas com o papel (para a justificativa e a saída).
    pieces: list[dict[str, Any]] = []
    for p, role in base.mains:
        pc = dict(p)
        pc["_role"] = role
        pieces.append(pc)

    # A sobreposição pode ser pedida pelo CLIMA (frio/chuva/vento) ou pela
    # OCASIÃO (blazer de reunião, casaco de avião, camada de sala com ar), de
    # forma independente — daí o `or` com o viés de camada.
    wants_outer = (
        band == BAND_COLD
        or flags["cold_signal"]
        or flags["rainy"]
        or flags["windy"]
        or profile.layering_bias >= LAYERING_THRESHOLD
    )
    wants_scarf = band == BAND_COLD or flags["cold_signal"]

    # Sobreposição (quando o clima ou a ocasião pede e há peça compatível).
    if wants_outer and slots["outers"]:
        outer = _pick_complement(
            slots["outers"], used_complement_ids, pieces, band, flags,
            True, profile, rng,
        )
        if outer is not None:
            oc = dict(outer)
            oc["_role"] = ROLE_OUTER
            pieces.append(oc)

    # Calçado (parte de um look completo; opcional se o guarda-roupa não tiver).
    if slots["footwear"]:
        shoe = _pick_complement(
            slots["footwear"], used_complement_ids, pieces, band, flags,
            True, profile, rng,
        )
        if shoe is not None:
            sc = dict(shoe)
            sc["_role"] = ROLE_FOOTWEAR
            pieces.append(sc)

    # Cachecol (frio) e/ou um acessório (opcional), no máximo um de cada.
    if wants_scarf and slots["scarves"]:
        scarf = _pick_complement(
            slots["scarves"], used_complement_ids, pieces, band, flags,
            False, profile, rng,
        )
        if scarf is not None:
            fc = dict(scarf)
            fc["_role"] = ROLE_SCARF
            pieces.append(fc)

    # Acessório extra: algumas ocasiões dispensam (mãos ocupadas no shopping,
    # ruído visual na entrevista, bagagem na viagem).
    if profile.wants_accessory and slots["accessories"]:
        acc = _pick_complement(
            slots["accessories"], used_complement_ids, pieces, band, flags,
            False, profile, rng,
        )
        if acc is not None:
            ac = dict(acc)
            ac["_role"] = ROLE_ACCESSORY
            pieces.append(ac)

    return pieces


def _look_structure_is_valid(
    pieces: list[dict[str, Any]], profile: Optional[OccasionProfile] = None
) -> bool:
    """
    Rede de segurança estrutural (defesa em profundidade): valida um look montado
    contra as regras invioláveis, olhando SOMENTE a `category` de cada peça.

    Regras:
      - Um vestido é peça única: não pode coexistir com peça de baixo (calça/saia)
        nem com peça de cima (camisa/malha), e nunca há dois vestidos.
      - Sem vestido, há no máximo UMA peça de baixo (calça e saia são ambas peça
        de baixo — nunca duas no mesmo look).
      - Quando há ocasião, nenhuma peça pode ser de categoria proibida por ela
        (nenhum blazer na academia) — a proibição é inviolável e não relaxa.

    Não reclassifica imagem nem conserta categoria errada: apenas garante que,
    dadas as categorias, a composição jamais viole a estrutura. Por construção os
    looks já saem válidos; este cheque protege contra regressões da montagem e
    torna a invariante testável de forma automatizada.
    """
    cats = [p.get("category") for p in pieces]

    if profile is not None and profile.forbidden_categories:
        if any(c in profile.forbidden_categories for c in cats):
            return False

    n_bottom = sum(1 for c in cats if c in BOTTOMS)
    n_top = sum(1 for c in cats if c in TOPS)
    n_dress = sum(1 for c in cats if c in DRESSES)

    if n_dress:
        return n_dress == 1 and n_bottom == 0 and n_top == 0
    return n_bottom <= 1


def generate_daily_look(
    items: list[dict[str, Any]],
    weather: WeatherInfo,
    ocasiao: Optional[str] = None,
) -> DailyLookResult:
    """
    Gera de 2 a 3 sugestões de look para o dia, de forma determinística.

    Args:
        items: peças do usuário (cada uma como dict com id e atributos de moda).
        weather: mínima, máxima e a LISTA de condições climáticas do dia.
        ocasiao: para o que a pessoa precisa do look (chave de `Ocasiao`).
            Ausente ou desconhecida cai em `dia_a_dia`, o registro mais elástico.

    Returns:
        DailyLookResult com a lista de looks (cada um com peças+papéis e uma
        justificativa) e uma nota opcional quando o guarda-roupa está limitado.
        Nunca lança por falta de peças — degrada graciosamente.
    """
    profile = get_profile(ocasiao)
    rng = random.Random(_seed_from(items, weather, profile))
    temp_ref = _reference_temp(weather)
    band = _band_for(temp_ref)
    flags = _condition_flags(weather["condicoes"])

    notes: list[str] = []

    # ── Etapa 1: pré-filtros ────────────────────────────────────────────────
    # A proibição da ocasião é aplicada primeiro e NUNCA é desfeita.
    allowed = _drop_forbidden(items, profile)
    n_forbidden = len(items) - len(allowed)

    thermal = _thermal_prefilter(allowed, band)

    # Cascata de relaxação, do mais restrito ao mais permissivo. Cada degrau
    # cede exatamente uma restrição e explica o que cedeu. A ordem — soltar o
    # REGISTRO antes do CLIMA — é deliberada: estar mal-vestida para a ocasião
    # é constrangedor, estar mal-vestida para o frio é insalubre.
    attempts: list[tuple[list[dict[str, Any]], Optional[str]]] = [
        (_register_filter(thermal, profile), None),
        (
            thermal,
            f"O guarda-roupa não tem peças no registro de {profile.label.lower()}; "
            "compus com o que havia, respeitando o clima.",
        ),
        (
            _register_filter(allowed, profile),
            "Poucas peças combinam com esta temperatura; ampliei a seleção para "
            f"manter o registro de {profile.label.lower()}.",
        ),
        (
            allowed,
            f"Nem o clima nem o registro de {profile.label.lower()} puderam ser "
            "plenamente respeitados — esta é a melhor composição possível com o "
            "guarda-roupa atual.",
        ),
    ]

    slots: Optional[dict[str, list[dict[str, Any]]]] = None
    for candidate, note in attempts:
        partitioned = _partition(candidate)
        if _have_core(partitioned):
            slots = partitioned
            if note:
                notes.append(note)
            break

    if slots is None:
        # Impossível montar um look completo. Degrada com uma nota clara,
        # distinguindo "faltam peças" de "a ocasião exclui o que você tem".
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
        return DailyLookResult(looks=[], note=note)

    # ── Etapa 2: composição ─────────────────────────────────────────────────
    bases, relaxed = _build_bases(slots, band, flags, profile, rng)
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
            base, slots, used_complement_ids, band, flags, profile, rng
        )

        # Rede de segurança: descarta um look que viole a estrutura ou a
        # proibição da ocasião (nunca deveria acontecer por construção — se
        # acontecer, sinaliza bug de montagem).
        if not _look_structure_is_valid(pieces, profile):
            logger.warning(
                "Look inválido descartado (categorias: %s, ocasião: %s) — "
                "verifique a lógica de montagem.",
                [p.get("category") for p in pieces],
                profile.key,
            )
            continue

        # Marca complementos como usados para dar variedade entre os looks.
        for p in pieces:
            if p.get("_role") in (ROLE_OUTER, ROLE_FOOTWEAR, ROLE_SCARF, ROLE_ACCESSORY):
                used_complement_ids.add(p["id"])

        commentary = _compose_commentary(pieces, weather, band, flags, profile, rng)
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
