"""Schemas Pydantic do usuário (nunca expõem o hash da senha)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: EmailStr
    created_at: datetime
    updated_at: datetime
