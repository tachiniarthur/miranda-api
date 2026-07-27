"""Schemas Pydantic do domínio de "look do dia"."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ClothingCategory


class GenerateLookRequest(BaseModel):
    """Entrada do usuário na tela de look do dia."""

    temperatura_min: float = Field(ge=-30, le=60)
    temperatura_max: float = Field(ge=-30, le=60)
    # Condição climática livre (ex.: "sol", "nublado", "chuva", "vento", "frio").
    condicao_climatica: str = Field(min_length=1, max_length=60)


class LookItemPublic(BaseModel):
    """
    Uma peça dentro de um look sugerido, com o papel que ela cumpre e os dados
    necessários para o frontend renderizá-la (mesmo espírito dos ClothingItem).
    """

    item_id: uuid.UUID
    role: str
    name: str
    category: ClothingCategory
    image_url: str = ""
    cor_primaria: str | None = None


class LookSuggestionPublic(BaseModel):
    """Um look sugerido: conjunto de peças + justificativa editorial."""

    id: str
    label: str
    items: list[LookItemPublic]
    commentary: str


class GenerateLookResponse(BaseModel):
    """Resposta da geração de look (composição determinística por regras)."""

    condicao_climatica: str
    temperatura_min: float
    temperatura_max: float
    looks: list[LookSuggestionPublic]
    # Nota opcional quando o guarda-roupa está limitado para o clima informado.
    note: str | None = None


class LookHistoryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    data_gerado: datetime
    temperatura_min: float | None
    temperatura_max: float | None
    condicao_climatica: str | None
    itens_sugeridos: dict | list | None
    justificativa: str | None
    created_at: datetime
