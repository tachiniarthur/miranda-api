"""
Lógica de negócio do guarda-roupa (peças de roupa), separada das rotas.
"""

from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clothing_item import ClothingItem
from app.models.enums import ClothingCategory
from app.schemas.clothing_item import ClothingItemCreate, ClothingItemUpdate
from app.services.storage import ImageStorage


class WardrobeError(Exception):
    """Erro de negócio do guarda-roupa com status HTTP associado."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def list_items(
    db: Session,
    *,
    user_id: uuid.UUID,
    category: ClothingCategory | None = None,
) -> list[ClothingItem]:
    stmt = select(ClothingItem).where(ClothingItem.user_id == user_id)
    if category is not None:
        stmt = stmt.where(ClothingItem.category == category)
    stmt = stmt.order_by(ClothingItem.created_at.desc())
    return list(db.scalars(stmt).all())


def get_item(db: Session, *, user_id: uuid.UUID, item_id: uuid.UUID) -> ClothingItem:
    item = db.get(ClothingItem, item_id)
    if item is None or item.user_id != user_id:
        raise WardrobeError(404, "Peça não encontrada.")
    return item


async def create_item(
    db: Session,
    *,
    user_id: uuid.UUID,
    data: ClothingItemCreate,
    image: UploadFile,
    storage: ImageStorage,
) -> ClothingItem:
    image_path = await storage.save(image)

    item = ClothingItem(
        user_id=user_id,
        image_path=image_path,
        name=data.name.strip(),
        category=data.category,
        cor_primaria=data.cor_primaria,
        cor_secundaria=data.cor_secundaria,
        estampa=data.estampa,
        formalidade=data.formalidade,
        peso_termico=data.peso_termico,
        serve_chuva=data.serve_chuva,
        estacoes=[e.value for e in data.estacoes] if data.estacoes else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


async def update_item(
    db: Session,
    *,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    data: ClothingItemUpdate,
    image: UploadFile | None,
    storage: ImageStorage,
) -> ClothingItem:
    item = get_item(db, user_id=user_id, item_id=item_id)

    # O formulário de edição do frontend reenvia o formulário completo, então
    # tratamos a atualização como uma substituição dos atributos:
    #  - name/category só são sobrescritos se vierem preenchidos (obrigatórios);
    #  - os atributos de moda são sempre substituídos pelo valor enviado
    #    (inclusive None, permitindo "limpar" um atributo).
    if data.name is not None:
        item.name = data.name.strip()
    if data.category is not None:
        item.category = data.category
    item.cor_primaria = data.cor_primaria
    item.cor_secundaria = data.cor_secundaria
    item.estampa = data.estampa
    item.formalidade = data.formalidade
    item.peso_termico = data.peso_termico
    item.serve_chuva = data.serve_chuva
    item.estacoes = [e.value for e in data.estacoes] if data.estacoes else None

    # Troca de imagem (opcional): salva a nova e remove a antiga.
    if image is not None:
        old_path = item.image_path
        item.image_path = await storage.save(image)
        storage.delete(old_path)

    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete_item(
    db: Session,
    *,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    storage: ImageStorage,
) -> None:
    item = get_item(db, user_id=user_id, item_id=item_id)
    image_path = item.image_path
    db.delete(item)
    db.commit()
    # Remove o arquivo do disco só depois de o registro sair do banco.
    storage.delete(image_path)
