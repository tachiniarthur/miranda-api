"""Schemas Pydantic do domínio de "look do dia"."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ClothingCategory, CondicaoClimatica, Ocasiao


class GenerateLookRequest(BaseModel):
    """Entrada do usuário na tela de look do dia."""

    temperatura_min: float = Field(ge=-30, le=60)
    temperatura_max: float = Field(ge=-30, le=60)

    # Condições COMBINÁVEIS: o usuário marca quantas quiser (sol com vento,
    # chuva com frio...). Ao menos uma é exigida — sem nenhuma, a composição
    # perderia o único sinal qualitativo do dia.
    condicoes_climaticas: list[CondicaoClimatica] = Field(min_length=1)

    # Para o que a pessoa precisa do look. Única por geração: um look não serve
    # a dois registros ao mesmo tempo.
    ocasiao: Ocasiao

    @field_validator("condicoes_climaticas")
    @classmethod
    def _dedupe(cls, v: list[CondicaoClimatica]) -> list[CondicaoClimatica]:
        """Remove repetições preservando a ordem em que foram marcadas."""
        seen: set[str] = set()
        unique: list[CondicaoClimatica] = []
        for c in v:
            if c.value not in seen:
                seen.add(c.value)
                unique.append(c)
        return unique


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
    """Resposta da geração de look (pré-filtro determinístico + API do Claude)."""

    condicoes_climaticas: list[CondicaoClimatica]
    ocasiao: Ocasiao
    temperatura_min: float
    temperatura_max: float
    looks: list[LookSuggestionPublic]
    # Nota opcional: guarda-roupa limitado para o clima ou para a ocasião, ou a
    # explicação de que não foi possível gerar agora.
    note: str | None = None


class LookHistoryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    data_gerado: datetime
    temperatura_min: float | None
    temperatura_max: float | None
    condicao_climatica: str | None
    ocasiao: str | None
    itens_sugeridos: dict | list | None
    justificativa: str | None
    created_at: datetime
