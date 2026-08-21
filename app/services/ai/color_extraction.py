"""
Extração de cor primária e secundária por processamento clássico de imagem.

Nenhuma IA aqui: rodamos k-means sobre os pixels da peça (que chega com o fundo
já removido, então descartamos os pixels transparentes) e convertemos os
clusters dominantes para nomes de cor em português por proximidade perceptual
(distância no espaço CIELAB até uma paleta de cores nomeadas).

Regras de confiança:
  - Se o cluster dominante representar uma fração pequena demais da peça, a cor
    primária vem nula (palpite pouco conclusivo).
  - A cor secundária só é retornada se o segundo cluster for grande o bastante E
    perceptualmente distinto da cor primária.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

import numpy as np
from sklearn.exceptions import ConvergenceWarning

# Fração mínima de pixels do cluster dominante para aceitarmos a cor primária.
PRIMARY_MIN_FRACTION = 0.15
# Fração mínima do segundo cluster para considerarmos uma cor secundária.
SECONDARY_MIN_FRACTION = 0.15
# Distância CIELAB mínima entre primária e secundária para elas contarem como
# cores realmente diferentes (evita retornar "preto" e "preto").
MIN_LAB_DISTANCE = 18.0
# Alpha abaixo do qual o pixel é considerado transparente (fundo removido).
_ALPHA_THRESHOLD = 128
# Número de clusters do k-means.
_N_CLUSTERS = 5
# Amostragem máxima de pixels (k-means em CPU sobre a imagem inteira é lento).
_MAX_SAMPLES = 8000


# Paleta de cores nomeadas (nome em português → RGB de referência). Usada para
# mapear a cor média de um cluster ao nome mais próximo.
_PALETTE: dict[str, tuple[int, int, int]] = {
    "preto": (20, 20, 20),
    "cinza-chumbo": (75, 75, 78),
    "cinza": (140, 140, 143),
    "cinza-claro": (200, 200, 202),
    "branco": (245, 245, 245),
    "off-white": (238, 232, 220),
    "bege": (214, 197, 168),
    "caramelo": (176, 128, 74),
    "marrom": (110, 74, 48),
    "marrom-escuro": (66, 44, 32),
    "vermelho": (190, 45, 45),
    "vinho": (110, 30, 45),
    "rosa": (222, 130, 160),
    "rosa-claro": (240, 190, 205),
    "coral": (233, 116, 96),
    "laranja": (222, 122, 40),
    "mostarda": (200, 160, 40),
    "amarelo": (230, 210, 70),
    "verde": (70, 140, 70),
    "verde-escuro": (40, 80, 55),
    "verde-oliva": (120, 125, 70),
    "verde-menta": (150, 205, 175),
    "azul": (55, 95, 175),
    "azul-marinho": (35, 45, 80),
    "azul-claro": (140, 180, 220),
    "turquesa": (60, 170, 175),
    "roxo": (110, 70, 150),
    "lilás": (185, 160, 210),
    "nude": (222, 190, 170),
}


@dataclass
class ColorResult:
    """Resultado da extração de cor, com o motivo quando um campo fica nulo."""

    primary: str | None = None
    secondary: str | None = None
    primary_reason: str | None = None
    secondary_reason: str | None = None


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """
    Converte cores sRGB (0-255) para CIELAB (D65), sem dependências externas.

    Aceita um array (N, 3) e devolve (N, 3). CIELAB aproxima a percepção humana
    de diferença de cor, então a distância euclidiana em Lab é razoável para
    "qual nome de cor está mais perto".
    """
    arr = np.asarray(rgb, dtype=np.float64) / 255.0

    # sRGB → linear
    mask = arr > 0.04045
    arr = np.where(mask, ((arr + 0.055) / 1.055) ** 2.4, arr / 12.92)

    # linear RGB → XYZ (matriz sRGB D65)
    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = arr @ m.T

    # Normaliza pelo branco de referência D65
    white = np.array([0.95047, 1.0, 1.08883])
    xyz = xyz / white

    # XYZ → Lab
    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)
    return lab


# Pré-computa a paleta em Lab uma vez (nomes e vetores alinhados por índice).
_PALETTE_NAMES: list[str] = list(_PALETTE.keys())
_PALETTE_LAB: np.ndarray = _srgb_to_lab(np.array([_PALETTE[n] for n in _PALETTE_NAMES]))


def _nearest_color_name(rgb: tuple[float, float, float]) -> str:
    lab = _srgb_to_lab(np.array([rgb]))[0]
    dists = np.linalg.norm(_PALETTE_LAB - lab, axis=1)
    return _PALETTE_NAMES[int(np.argmin(dists))]


def _load_rgba(image: "str | bytes") -> np.ndarray:
    from PIL import Image

    from app.services.image_validation import apply_pillow_limits

    apply_pillow_limits()

    if isinstance(image, (bytes, bytearray)):
        pil = Image.open(io.BytesIO(bytes(image)))
    else:
        pil = Image.open(image)
    return np.array(pil.convert("RGBA"))


def extract_colors(image: "str | bytes") -> ColorResult:
    """
    Extrai cor primária e (quando possível) secundária da peça recortada.

    Degrada com segurança: qualquer falha inesperada resulta em cores nulas,
    nunca numa exceção que derrube o fluxo de análise.
    """
    from sklearn.cluster import KMeans

    try:
        rgba = _load_rgba(image)
    except Exception:  # noqa: BLE001
        return ColorResult(primary_reason="imagem_ilegivel", secondary_reason="imagem_ilegivel")

    pixels = rgba.reshape(-1, 4)
    # Descarta pixels transparentes (o fundo removido).
    opaque = pixels[pixels[:, 3] >= _ALPHA_THRESHOLD][:, :3].astype(np.float64)

    if opaque.shape[0] < 50:
        return ColorResult(
            primary_reason="poucos_pixels_opacos",
            secondary_reason="poucos_pixels_opacos",
        )

    # Amostragem determinística para acelerar o k-means em CPU.
    if opaque.shape[0] > _MAX_SAMPLES:
        rng = np.random.default_rng(42)
        idx = rng.choice(opaque.shape[0], size=_MAX_SAMPLES, replace=False)
        sample = opaque[idx]
    else:
        sample = opaque

    # Limita k ao número de cores distintas para evitar clusters vazios (e o
    # ConvergenceWarning que eles geram em recortes de poucas cores).
    n_distinct = np.unique(sample, axis=0).shape[0]
    n_clusters = max(1, min(_N_CLUSTERS, n_distinct))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=ConvergenceWarning)
        kmeans = KMeans(n_clusters=n_clusters, n_init=4, random_state=42)
        labels = kmeans.fit_predict(sample)
    centers = kmeans.cluster_centers_

    counts = np.bincount(labels, minlength=n_clusters)
    total = counts.sum()
    order = np.argsort(counts)[::-1]  # clusters do maior ao menor

    result = ColorResult()

    # ── Cor primária: maior cluster, se representativo o bastante ─────────
    top = order[0]
    top_fraction = counts[top] / total
    if top_fraction < PRIMARY_MIN_FRACTION:
        result.primary_reason = "cluster_dominante_pequeno"
        result.secondary_reason = "sem_cor_primaria"
        return result

    primary_rgb = tuple(centers[top])
    result.primary = _nearest_color_name(primary_rgb)
    primary_lab = _srgb_to_lab(np.array([primary_rgb]))[0]

    # ── Cor secundária: próximo cluster grande e perceptualmente distinto ─
    result.secondary_reason = "sem_segunda_cor_distinta"
    for cluster in order[1:]:
        fraction = counts[cluster] / total
        if fraction < SECONDARY_MIN_FRACTION:
            break  # os próximos são ainda menores
        cand_rgb = tuple(centers[cluster])
        cand_lab = _srgb_to_lab(np.array([cand_rgb]))[0]
        if np.linalg.norm(cand_lab - primary_lab) >= MIN_LAB_DISTANCE:
            name = _nearest_color_name(cand_rgb)
            if name != result.primary:
                result.secondary = name
                result.secondary_reason = None
                break

    return result
