"""
Orquestrador da análise de peça de roupa — 100% self-hosted, sem API paga.

Junta três técnicas, todas rodando localmente:
  1. FashionCLIP (zero-shot) → categoria, formalidade, estampa + portão de
     validação "isto é uma peça de roupa?".
  2. K-means de cor (processamento clássico) → cor primária e secundária.
  3. Regras determinísticas (por categoria) → peso térmico, serve-chuva, estações.

Cada campo que não puder ser determinado com confiança fica NULO no resultado
(para o usuário preencher no formulário) — nunca causa erro. O ÚNICO caso que
interrompe o fluxo de propósito é o portão de validação: imagem que claramente
não é roupa levanta `NotClothingError`.

Extensão futura: se um dia for adicionada uma camada extra de resolução para os
campos incertos (um VLM pago ou outra técnica), basta plugá-la em
`_resolve_uncertain_fields` — sem reescrever o resto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypedDict

from app.core.config import settings
from app.services.ai import color_extraction, fashion_clip, labels, rules
from app.services.ai.fashion_clip import ModelUnavailableError

logger = logging.getLogger("miranda.ai.analysis")

# Probabilidade mínima do grupo "roupa" no portão de validação. É separado do
# limiar de confiança dos campos (que é configurável) porque tem outro papel:
# barrar imagens que claramente não são roupa, não escolher entre roupas.
CLOTHING_GATE_THRESHOLD = 0.60


class ClothingAttributes(TypedDict, total=False):
    """Formato do dicionário de atributos inferidos (campos incertos = None)."""

    category: str | None
    cor_primaria: str | None
    cor_secundaria: str | None
    estampa: str | None
    formalidade: str | None
    peso_termico: str | None
    serve_chuva: bool | None
    estacoes: list[str] | None


class NotClothingError(Exception):
    """A imagem enviada não foi reconhecida como uma peça de roupa."""

    def __init__(self, probability: float) -> None:
        super().__init__("A imagem enviada não parece ser uma peça de roupa.")
        self.probability = probability


@dataclass
class FieldResult:
    """Valor de um campo + proveniência (origem) + motivo quando fica nulo."""

    value: Any = None
    source: str | None = None          # "fashion_clip" | "kmeans" | "rules"
    confidence: float | None = None    # score do FashionCLIP, quando aplicável
    reason: str | None = None          # por que ficou nulo, quando value é None


@dataclass
class AnalysisResult:
    """Resultado completo da análise: um FieldResult por campo."""

    fields: dict[str, FieldResult] = field(default_factory=dict)

    def attributes(self) -> ClothingAttributes:
        """Extrai só o dicionário de atributos (contrato público, campos = valor/None)."""
        return {name: fr.value for name, fr in self.fields.items()}  # type: ignore[return-value]

    def determined(self) -> list[str]:
        return [name for name, fr in self.fields.items() if fr.value is not None]

    def undetermined(self) -> list[str]:
        return [name for name, fr in self.fields.items() if fr.value is None]


def _classify_field(image, candidates, threshold: float) -> FieldResult:
    """Classifica um campo pelo FashionCLIP; nulo se abaixo do limiar de confiança."""
    scored = fashion_clip.classify(image, candidates)
    top_value, top_score = scored[0]
    if top_score >= threshold:
        return FieldResult(value=top_value, source="fashion_clip", confidence=top_score)
    return FieldResult(
        value=None,
        source="fashion_clip",
        confidence=top_score,
        reason="confianca_abaixo_do_limiar",
    )


def _resolve_uncertain_fields(result: AnalysisResult, image) -> None:
    """
    Ponto de extensão (no-op nesta fase).

    Aqui, no futuro, uma camada adicional (ex.: um VLM) poderia tentar resolver
    os campos que ficaram nulos. Por ora não fazemos nada: campos incertos
    permanecem nulos para o usuário preencher.
    """
    return None


def analyze_clothing_item_detailed(image: str | bytes) -> AnalysisResult:
    """
    Analisa a imagem e devolve o resultado detalhado (valor + proveniência).

    Args:
        image: caminho do arquivo (str) OU os bytes brutos da imagem (com fundo
            já removido; PNG com transparência).

    Returns:
        `AnalysisResult` com um `FieldResult` por campo.

    Raises:
        NotClothingError: se o portão de validação reprovar a imagem.
    """
    result = AnalysisResult()

    # ── Portão de validação (só se o modelo estiver disponível) ──────────
    clip_available = True
    try:
        prob = fashion_clip.clothing_probability(image)
        if prob < CLOTHING_GATE_THRESHOLD:
            logger.info("Portão de validação reprovou a imagem (p_roupa=%.3f).", prob)
            raise NotClothingError(prob)
        logger.debug("Portão de validação aprovou a imagem (p_roupa=%.3f).", prob)
    except ModelUnavailableError:
        # Modelo indisponível: NÃO barra o fluxo. Degrada — segue só com cor.
        clip_available = False
        logger.warning("FashionCLIP indisponível; seguindo com análise degradada (só cor).")

    # ── Campos do FashionCLIP: categoria, formalidade, estampa ───────────
    if clip_available:
        # Cada campo usa seu próprio limiar de confiança (ver config.py): categoria
        # e estampa são decisivas (limiar alto), formalidade é difusa (limiar baixo).
        try:
            result.fields["category"] = _classify_field(
                image, labels.CATEGORY_CANDIDATES, settings.FASHION_CLIP_THRESHOLD_CATEGORIA
            )
        except Exception as exc:  # noqa: BLE001 — degrada campo a campo
            logger.warning("Falha ao classificar categoria: %s", exc)
            result.fields["category"] = FieldResult(reason="erro_na_classificacao")

        try:
            result.fields["formalidade"] = _classify_field(
                image, labels.FORMALIDADE_CANDIDATES, settings.FASHION_CLIP_THRESHOLD_FORMALIDADE
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao classificar formalidade: %s", exc)
            result.fields["formalidade"] = FieldResult(reason="erro_na_classificacao")

        try:
            result.fields["estampa"] = _classify_field(
                image, labels.ESTAMPA_CANDIDATES, settings.FASHION_CLIP_THRESHOLD_ESTAMPA
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao classificar estampa: %s", exc)
            result.fields["estampa"] = FieldResult(reason="erro_na_classificacao")
    else:
        for name in ("category", "formalidade", "estampa"):
            result.fields[name] = FieldResult(reason="modelo_indisponivel")

    # ── Cores por k-means (independente do FashionCLIP) ──────────────────
    try:
        colors = color_extraction.extract_colors(image)
        result.fields["cor_primaria"] = FieldResult(
            value=colors.primary,
            source="kmeans",
            reason=None if colors.primary else colors.primary_reason,
        )
        result.fields["cor_secundaria"] = FieldResult(
            value=colors.secondary,
            source="kmeans",
            reason=None if colors.secondary else colors.secondary_reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha na extração de cor: %s", exc)
        result.fields["cor_primaria"] = FieldResult(reason="erro_na_extracao_de_cor")
        result.fields["cor_secundaria"] = FieldResult(reason="erro_na_extracao_de_cor")

    # ── Regras determinísticas (dependem da categoria) ───────────────────
    category_value = result.fields.get("category", FieldResult()).value
    rule_res = rules.apply_rules(category_value)
    result.fields["peso_termico"] = FieldResult(
        value=rule_res.peso_termico,
        source="rules",
        reason=None if rule_res.peso_termico else rule_res.peso_termico_reason,
    )
    result.fields["serve_chuva"] = FieldResult(
        value=rule_res.serve_chuva,
        source="rules",
        reason=None if rule_res.serve_chuva is not None else rule_res.serve_chuva_reason,
    )
    result.fields["estacoes"] = FieldResult(
        value=rule_res.estacoes,
        source="rules",
        reason=None if rule_res.estacoes else rule_res.estacoes_reason,
    )

    # Ponto de extensão futuro para resolver campos incertos.
    _resolve_uncertain_fields(result, image)

    # ── Log de acompanhamento (taxa de acerto ao longo do tempo) ─────────
    logger.info(
        "Análise concluída | determinados=%s | nulos=%s",
        result.determined(),
        result.undetermined(),
    )

    return result


def analyze_clothing_item(image: str | bytes) -> ClothingAttributes:
    """
    Analisa a imagem de uma peça de roupa e retorna seus atributos de moda.

    Mantém o contrato público do stub original: recebe caminho ou bytes e devolve
    um dicionário `ClothingAttributes`, com os campos que não puderam ser
    determinados vindo como `None`.

    Args:
        image: caminho do arquivo no disco (str) OU os bytes brutos da imagem.

    Returns:
        Um dicionário `ClothingAttributes` com os atributos inferidos.

    Raises:
        NotClothingError: se a imagem não for reconhecida como peça de roupa.
    """
    return analyze_clothing_item_detailed(image).attributes()
