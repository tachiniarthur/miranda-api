"""
Camada de IA do Miranda (services/ai).

Duas metades, com custos bem diferentes:

  · ANÁLISE DE PEÇA (`clothing_analysis`, `fashion_clip`, `color_extraction`,
    `rules`, `labels`) é 100% self-hosted e gratuita: FashionCLIP local +
    k-means + regras determinísticas, sem chamada de rede paga.
  · COMPOSIÇÃO DO LOOK (`look_generation`, `look_prompt`, `claude_client`) chama
    a API do Claude a cada geração — `generate_daily_look` NÃO é mais
    determinística nem gratuita: cada composição custa dinheiro (ver
    `claude_client.MODEL_PRICES_USD_PER_MTOK` e o log de custo estimado).
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
