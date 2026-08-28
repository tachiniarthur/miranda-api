"""
Lógica de negócio do guarda-roupa (peças de roupa), separada das rotas.
"""

from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.clothing_item import ClothingItem
from app.models.enums import ClothingCategory
from app.models.user import User
from app.schemas.clothing_item import ClothingItemCreate, ClothingItemUpdate
from app.services.image_validation import (
    ImageValidationError,
    perceptual_hash,
    read_and_validate_upload,
)
from app.services.storage import ImageStorage, StorageError


class WardrobeError(Exception):
    """Erro de negócio do guarda-roupa com status HTTP associado."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class DuplicateImageError(Exception):
    """Esta imagem já está cadastrada nesta conta."""

    def __init__(self) -> None:
        super().__init__(
            "Esta imagem já está cadastrada no seu guarda-roupa. "
            "Use outra foto ou edite a peça existente."
        )


class QuotaExceededError(Exception):
    """O usuário atingiu o teto de peças cadastradas."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Limite de {limit} peças por conta atingido.")
        self.limit = limit


def _assert_within_quota(db: Session, *, user_id: uuid.UUID) -> None:
    """
    Confere a quota ANTES de gravar a imagem no disco.

    A ordem importa: validar depois do upload deixaria um arquivo órfão no
    storage a cada tentativa recusada — que é exatamente o recurso que a quota
    existe para proteger.
    """
    # `with_for_update()` trava a linha do usuário até o fim desta transação.
    #
    # Sem a trava, contar e gravar são dois passos com uma janela no meio: dois
    # uploads simultâneos leem a MESMA contagem antes de qualquer um inserir, os
    # dois passam pela checagem, e com N requisições em paralelo o teto vira
    # teto + N. É a mesma corrida que `auth_service.reset_password` já fecha do
    # mesmo jeito — e aqui ela é barata, porque a trava é por usuário e só
    # serializa os uploads simultâneos da própria conta.
    db.execute(select(User.id).where(User.id == user_id).with_for_update())

    atuais = db.scalar(
        select(func.count(ClothingItem.id)).where(ClothingItem.user_id == user_id)
    )
    if (atuais or 0) >= settings.MAX_ITEMS_PER_USER:
        raise QuotaExceededError(settings.MAX_ITEMS_PER_USER)


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
    _assert_within_quota(db, user_id=user_id)

    # Lê e valida ANTES de gravar: a checagem de duplicata precisa dos bytes, e
    # recusar depois deixaria o arquivo órfão no disco — justamente o recurso
    # que estas duas defesas existem para proteger.
    try:
        contents, ext = await read_and_validate_upload(image)
    except ImageValidationError as exc:
        # Mesma tradução que `LocalImageStorage.save` faz: quem chama `create_item`
        # trata StorageError.
        raise StorageError(exc.message) from exc

    image_hash = perceptual_hash(contents)
    if image_hash is not None:
        ja_existe = db.scalar(
            select(ClothingItem.id).where(
                ClothingItem.user_id == user_id,
                ClothingItem.image_hash == image_hash,
            )
        )
        if ja_existe is not None:
            raise DuplicateImageError()

    # Os bytes já foram lidos e validados acima: passar o resultado adiante
    # evita que o storage releia o arquivo inteiro, o que dobrava o pico de
    # memória por requisição aprovada e exigia um `seek(0)` no meio.
    image_path = await storage.save(image, validated=(contents, ext))

    item = ClothingItem(
        user_id=user_id,
        image_path=image_path,
        image_hash=image_hash,
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
