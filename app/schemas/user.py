"""Schemas Pydantic do usuário (nunca expõem o hash da senha)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: EmailStr
    created_at: datetime
    updated_at: datetime

    # Exposto para o frontend poder sinalizar o estado. Não bloqueia nada por
    # padrão — ver settings.REQUIRE_VERIFIED_EMAIL.
    #
    # O valor vem da propriedade `is_email_verified` do ORM (que lê
    # `email_verified_at`) via validation_alias. É um caminho só: nada monta
    # este schema por palavra-chave, e assim a rota continua devolvendo o
    # objeto do banco sem tradução manual.
    email_verified: bool = Field(default=False, validation_alias="is_email_verified")
