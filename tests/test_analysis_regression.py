"""
Teste de regressão da análise de peça (FashionCLIP).

Roda o classificador real contra imagens de referência versionadas em
`test-images/` e verifica que a categoria prevista bate com a categoria
esperada conhecida. Serve de rede de segurança: se uma mudança futura nos
rótulos (labels.py) ou no modelo quebrar uma classificação que hoje funciona,
este teste falha.

O carregamento do FashionCLIP baixa ~600 MB na primeira vez (depois usa o cache
do HuggingFace). Se o modelo não estiver disponível (sem internet e sem cache),
o teste é PULADO em vez de falhar — para não travar a suíte em ambientes offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai import fashion_clip
from app.services.ai.clothing_analysis import analyze_clothing_item

TEST_IMAGES = Path(__file__).resolve().parent.parent / "test-images"

# (arquivo, categoria esperada) — imagens inequívocas escolhidas manualmente.
# 12.jpg: vestido slip terracota (o caso que originou a investigação vestido/saia)
# 4.jpg:  saia midi off-white
# 2.jpg:  calça
REFERENCE_CASES = [
    ("12.jpg", "vestido"),
    ("4.jpg", "saia"),
    ("2.jpg", "calca"),
]


@pytest.fixture(scope="module")
def model_available() -> bool:
    if not fashion_clip.is_available():
        pytest.skip("FashionCLIP indisponível (sem cache/internet) — teste pulado.")
    return True


@pytest.mark.parametrize("filename,expected", REFERENCE_CASES)
def test_category_regression(filename: str, expected: str, model_available: bool):
    path = TEST_IMAGES / filename
    assert path.exists(), f"imagem de referência ausente: {path}"
    attrs = analyze_clothing_item(str(path))
    assert attrs["category"] == expected, (
        f"{filename}: esperava categoria '{expected}', obtido '{attrs.get('category')}'"
    )


def test_dress_not_confused_with_skirt(model_available: bool):
    """O vestido terracota (12.jpg) não pode ser classificado como saia."""
    from app.services.ai.fashion_clip import classify
    from app.services.ai.labels import CATEGORY_CANDIDATES

    scored = dict(classify(str(TEST_IMAGES / "12.jpg"), CATEGORY_CANDIDATES))
    assert scored["vestido"] > scored["saia"]
    assert scored["vestido"] > 0.5


def test_o_vestido_ganha_da_saia_com_margem_folgada(model_available: bool):
    """
    Não basta vencer: a margem precisa ser folgada. Uma vitória por 0.01 é
    ruído, e a próxima mudança de prompt a inverteria sem ninguém notar.

    O limiar é 0.5, e não a margem medida (0.997 em 2026-08-25, ver
    docs/superpowers/relatorio-vestido-saia.md): o teste existe para pegar um
    colapso da distinção, não para travar o modelo num número exato.
    """
    from app.services.ai.fashion_clip import classify
    from app.services.ai.labels import CATEGORY_CANDIDATES

    scored = dict(classify(str(TEST_IMAGES / "12.jpg"), CATEGORY_CANDIDATES))
    margem = scored["vestido"] - scored["saia"]
    assert margem > 0.5, f"margem estreita demais: {margem:.3f}"
