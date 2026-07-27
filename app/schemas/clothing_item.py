"""Schemas Pydantic do domínio de peças de roupa (guarda-roupa)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ClothingCategory, Estacao, Formalidade, PesoTermico


class ClothingItemBase(BaseModel):
    """Campos editáveis de uma peça (sem a imagem, que é tratada à parte)."""

    name: str = Field(min_length=1, max_length=160)
    category: ClothingCategory
    cor_primaria: str | None = Field(default=None, max_length=60)
    cor_secundaria: str | None = Field(default=None, max_length=60)
    estampa: str | None = Field(default=None, max_length=80)
    formalidade: Formalidade | None = None
    peso_termico: PesoTermico | None = None
    serve_chuva: bool | None = None
    estacoes: list[Estacao] | None = None

    @field_validator("estacoes")
    @classmethod
    def _dedupe_estacoes(cls, v: list[Estacao] | None) -> list[Estacao] | None:
        if v is None:
            return None
        # Remove duplicatas preservando a ordem.
        seen: set[Estacao] = set()
        result: list[Estacao] = []
        for e in v:
            if e not in seen:
                seen.add(e)
                result.append(e)
        return result


class ClothingItemCreate(ClothingItemBase):
    """Payload de criação (a imagem chega como arquivo multipart separado)."""


class ClothingItemUpdate(BaseModel):
    """Payload de atualização — todos os campos são opcionais (patch parcial)."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: ClothingCategory | None = None
    cor_primaria: str | None = Field(default=None, max_length=60)
    cor_secundaria: str | None = Field(default=None, max_length=60)
    estampa: str | None = Field(default=None, max_length=80)
    formalidade: Formalidade | None = None
    peso_termico: PesoTermico | None = None
    serve_chuva: bool | None = None
    estacoes: list[Estacao] | None = None


class ClothingItemPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    category: ClothingCategory
    image_path: str
    # URL absoluta para o frontend carregar a imagem. Não existe como coluna no
    # banco: é preenchida pela camada de rotas via storage.url_for(). O default
    # "" permite `model_validate(orm_item)` sem o atributo presente no ORM.
    image_url: str = ""
    cor_primaria: str | None
    cor_secundaria: str | None
    estampa: str | None
    formalidade: Formalidade | None
    peso_termico: PesoTermico | None
    serve_chuva: bool | None
    estacoes: list[str] | None
    created_at: datetime
    updated_at: datetime
