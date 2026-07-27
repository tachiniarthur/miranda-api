"""
Schemas da resposta da rota de análise de peça (`POST /api/wardrobe/analyze`).

A resposta traz, além dos atributos sugeridos, a proveniência de cada campo
(de onde veio o valor) e o motivo quando um campo fica nulo — útil para o
frontend e para calibrar os limiares de confiança com uso real.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FieldInfo(BaseModel):
    """Metadados de um campo analisado."""

    value: Any = None
    # Origem do valor: "fashion_clip", "kmeans", "rules" ou None.
    source: str | None = None
    # Score de confiança do FashionCLIP (0-1), quando aplicável.
    confidence: float | None = None
    # Motivo de o campo ter ficado nulo (ex.: "confianca_abaixo_do_limiar").
    reason: str | None = None


class AnalyzeResponse(BaseModel):
    """Resposta da análise: atributos sugeridos + detalhes por campo."""

    # Dicionário simples campo → valor (ou None), pronto para preencher o form.
    attributes: dict[str, Any]
    # Detalhes por campo (origem, confiança, motivo do nulo).
    fields: dict[str, FieldInfo]
