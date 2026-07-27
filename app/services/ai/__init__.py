"""
Camada de IA do Miranda (services/ai).

Toda a camada é 100% self-hosted, sem nenhuma API paga: a análise de peça
(FashionCLIP + k-means + regras determinísticas) e a composição do look do dia
(`generate_daily_look`, determinística por regras — sem LLM).
"""

from app.services.ai.clothing_analysis import (
    AnalysisResult,
    ClothingAttributes,
    FieldResult,
    NotClothingError,
    analyze_clothing_item,
    analyze_clothing_item_detailed,
)
from app.services.ai.look_generation import generate_daily_look

__all__ = [
    "AnalysisResult",
    "ClothingAttributes",
    "FieldResult",
    "NotClothingError",
    "analyze_clothing_item",
    "analyze_clothing_item_detailed",
    "generate_daily_look",
]
