"""
Reexporta todos os modelos para que o Alembic (via app.models) e o restante
da aplicação consigam descobri-los a partir de um único ponto.
"""

from app.models.clothing_item import ClothingItem
from app.models.look_history import LookHistory
from app.models.user import User

__all__ = ["User", "ClothingItem", "LookHistory"]
