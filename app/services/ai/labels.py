"""
Rótulos candidatos do FashionCLIP e mapeamento inglês → enums do domínio.

O FashionCLIP foi treinado com dados de moda em inglês, então os *prompts* de
texto que passamos a ele precisam ser em inglês. Cada campo tem uma lista de
candidatos; para cada candidato há:

  - um ou mais prompts em inglês (a similaridade do rótulo é o máximo entre eles);
  - o valor final no domínio (o valor exato do enum em português usado no banco
    e no frontend), OU, no caso de `estampa`, a string livre em português.

Centralizar tudo aqui garante que o mapeamento inglês → enum esteja correto e em
um único lugar. Os valores de enum do Postgres são minúsculos e sem acento.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import ClothingCategory, Formalidade


@dataclass(frozen=True)
class Candidate:
    """Um rótulo candidato: prompts em inglês → valor final no domínio."""

    value: str
    prompts: list[str] = field(default_factory=list)


# ── Portão de validação: é uma peça de roupa? ─────────────────────────────
# Comparamos a massa de probabilidade entre "é roupa" e "não é roupa".
CLOTHING_PROMPTS: list[str] = [
    "a photo of a piece of clothing",
    "a photo of a garment",
    "a fashion product photo of an apparel item",
    "a photo of an outfit",
    "a photo of shoes",
    "a photo of a fashion accessory",
]

NOT_CLOTHING_PROMPTS: list[str] = [
    "a photo of furniture",
    "a photo of food",
    "a screenshot of a screen",
    "a photo of a landscape",
    "a photo of an animal",
    "a close-up photo of a human face",
    "a photo of a car",
    "a photo of a building",
    "a photo of a random object",
    "a photo of a document or text",
]


# ── Categoria (→ ClothingCategory) ────────────────────────────────────────
# `outros` não entra como candidato: não há como descrevê-lo por imagem. Se
# nenhuma categoria conhecida vencer com confiança, o campo fica nulo e o
# usuário escolhe manualmente (que pode ser justamente "outros").
CATEGORY_CANDIDATES: list[Candidate] = [
    Candidate(ClothingCategory.BLAZER.value, ["a photo of a blazer", "a photo of a tailored suit jacket"]),
    # `vestido` (dress) e `saia` (skirt) são visualmente confundíveis quando a
    # peça é longa: a queda de tecido abaixo da cintura de um vestido maxi lembra
    # uma saia. Para separá-las, os prompts de VESTIDO enfatizam que é uma peça
    # ÚNICA que cobre o TRONCO e desce até as pernas (ombros→barra), e os de SAIA
    # enfatizam que cobre APENAS da cintura para baixo, sem parte de cima.
    Candidate(
        ClothingCategory.VESTIDO.value,
        [
            "a photo of a dress",
            "a photo of a one-piece dress covering the torso and the legs",
            "a photo of a full-length dress from the shoulders to the hem",
            "a photo of a maxi dress",
            "a photo of a sleeveless slip dress",
        ],
    ),
    Candidate(ClothingCategory.CALCA.value, ["a photo of pants", "a photo of trousers", "a photo of jeans"]),
    Candidate(ClothingCategory.CAMISA.value, ["a photo of a shirt", "a photo of a blouse", "a photo of a t-shirt"]),
    Candidate(ClothingCategory.CASACO.value, ["a photo of a coat", "a photo of an overcoat", "a photo of a heavy jacket"]),
    Candidate(ClothingCategory.MALHA.value, ["a photo of a knit sweater", "a photo of a sweater", "a photo of a knitwear top"]),
    Candidate(
        ClothingCategory.SAIA.value,
        [
            "a photo of a skirt",
            "a photo of a skirt covering only the lower body from the waist down",
            "a photo of a midi skirt with a waistband and no top",
            "a photo of an a-line skirt",
        ],
    ),
    Candidate(ClothingCategory.CALCADO.value, ["a photo of shoes", "a photo of footwear", "a photo of boots"]),
    Candidate(ClothingCategory.CACHECOL.value, ["a photo of a scarf"]),
    Candidate(ClothingCategory.ACESSORIO.value, ["a photo of a fashion accessory", "a photo of a handbag", "a photo of a belt", "a photo of a hat"]),
]


# ── Formalidade (→ Formalidade) ───────────────────────────────────────────
FORMALIDADE_CANDIDATES: list[Candidate] = [
    Candidate(Formalidade.CASUAL.value, ["a photo of casual everyday clothing"]),
    Candidate(Formalidade.SMART_CASUAL.value, ["a photo of smart casual clothing"]),
    Candidate(Formalidade.SOCIAL.value, ["a photo of formal business attire", "a photo of formal evening wear"]),
    Candidate(Formalidade.ESPORTE.value, ["a photo of sportswear", "a photo of athletic clothing"]),
]


# ── Estampa (campo de texto livre em português) ───────────────────────────
# O campo `estampa` no banco é uma string livre (não é enum). Mapeamos o
# rótulo vencedor para a palavra em português usada como placeholder no
# formulário ("Liso, listrado, floral...").
ESTAMPA_CANDIDATES: list[Candidate] = [
    Candidate("liso", ["a photo of solid color clothing with no pattern", "a photo of plain clothing"]),
    Candidate("listrado", ["a photo of striped patterned clothing"]),
    Candidate("floral", ["a photo of floral patterned clothing"]),
    Candidate("xadrez", ["a photo of plaid checkered clothing", "a photo of tartan clothing"]),
    Candidate("poás", ["a photo of polka dot patterned clothing"]),
    Candidate("animal print", ["a photo of animal print clothing", "a photo of leopard print clothing"]),
    Candidate("estampado", ["a photo of clothing with a graphic print", "a photo of patterned printed clothing"]),
]
