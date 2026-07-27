"""
Rotas REST de peças de roupa (guarda-roupa), todas protegidas por autenticação.

Criação e atualização recebem os dados via `multipart/form-data`, pois incluem
o upload do arquivo de imagem já processado (fundo removido) pelo frontend.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.enums import ClothingCategory
from app.models.user import User
from app.schemas.analyze import AnalyzeResponse, FieldInfo
from app.schemas.clothing_item import (
    ClothingItemCreate,
    ClothingItemPublic,
    ClothingItemUpdate,
)
from app.services import wardrobe_service
from app.services.ai import NotClothingError, analyze_clothing_item_detailed
from app.services.storage import StorageError, image_storage
from app.services.wardrobe_service import WardrobeError

router = APIRouter(prefix="/wardrobe/items", tags=["wardrobe"])


def _none_if_blank(value: str | None) -> str | None:
    """Converte strings vazias vindas do formulário em None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _to_public(item) -> ClothingItemPublic:
    """Serializa uma peça incluindo a URL absoluta da imagem."""
    # `image_url` não é uma coluna do ORM; anexamos como atributo transiente
    # na instância para que o model_validate (from_attributes) o encontre.
    item.image_url = image_storage.url_for(item.image_path)
    return ClothingItemPublic.model_validate(item)


@router.get("", response_model=list[ClothingItemPublic])
def list_items(
    category: ClothingCategory | None = Query(
        default=None, description="Filtra as peças por categoria."
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClothingItemPublic]:
    items = wardrobe_service.list_items(
        db, user_id=current_user.id, category=category
    )
    return [_to_public(i) for i in items]


@router.get("/{item_id}", response_model=ClothingItemPublic)
def get_item(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClothingItemPublic:
    try:
        item = wardrobe_service.get_item(
            db, user_id=current_user.id, item_id=item_id
        )
    except WardrobeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return _to_public(item)


@router.post("", response_model=ClothingItemPublic, status_code=201)
async def create_item(
    name: str = Form(...),
    category: str = Form(...),
    cor_primaria: str | None = Form(None),
    cor_secundaria: str | None = Form(None),
    estampa: str | None = Form(None),
    formalidade: str | None = Form(None),
    peso_termico: str | None = Form(None),
    serve_chuva: bool | None = Form(None),
    estacoes: list[str] = Form(default=[]),
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClothingItemPublic:
    try:
        data = ClothingItemCreate(
            name=name,
            category=category,  # type: ignore[arg-type]
            cor_primaria=_none_if_blank(cor_primaria),
            cor_secundaria=_none_if_blank(cor_secundaria),
            estampa=_none_if_blank(estampa),
            formalidade=_none_if_blank(formalidade),  # type: ignore[arg-type]
            peso_termico=_none_if_blank(peso_termico),  # type: ignore[arg-type]
            serve_chuva=serve_chuva,
            estacoes=estacoes or None,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    try:
        item = await wardrobe_service.create_item(
            db,
            user_id=current_user.id,
            data=data,
            image=image,
            storage=image_storage,
        )
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_public(item)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_item(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    """
    Analisa a imagem de uma peça (fundo já removido) e sugere seus atributos.

    NÃO persiste nada: apenas roda a análise self-hosted (FashionCLIP + k-means +
    regras) e devolve os atributos sugeridos, com a proveniência e o motivo de
    cada campo nulo. A criação da peça continua pela rota POST "" existente.

    Reprova a imagem (422, code "not_clothing") apenas quando o portão de
    validação conclui que ela claramente não é uma peça de roupa. Qualquer outra
    incerteza vira campos nulos, nunca um erro.
    """
    contents = await image.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Arquivo de imagem vazio.")

    try:
        result = analyze_clothing_item_detailed(contents)
    except NotClothingError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "not_clothing",
                "message": "A imagem enviada não parece ser uma peça de roupa. Escolha outra foto.",
            },
        )

    return AnalyzeResponse(
        attributes=result.attributes(),
        fields={
            name: FieldInfo(
                value=fr.value,
                source=fr.source,
                confidence=fr.confidence,
                reason=fr.reason,
            )
            for name, fr in result.fields.items()
        },
    )


@router.put("/{item_id}", response_model=ClothingItemPublic)
async def update_item(
    item_id: uuid.UUID,
    name: str | None = Form(None),
    category: str | None = Form(None),
    cor_primaria: str | None = Form(None),
    cor_secundaria: str | None = Form(None),
    estampa: str | None = Form(None),
    formalidade: str | None = Form(None),
    peso_termico: str | None = Form(None),
    serve_chuva: bool | None = Form(None),
    estacoes: list[str] = Form(default=[]),
    image: UploadFile | None = File(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClothingItemPublic:
    try:
        data = ClothingItemUpdate(
            name=name,
            category=category,  # type: ignore[arg-type]
            cor_primaria=_none_if_blank(cor_primaria),
            cor_secundaria=_none_if_blank(cor_secundaria),
            estampa=_none_if_blank(estampa),
            formalidade=_none_if_blank(formalidade),  # type: ignore[arg-type]
            peso_termico=_none_if_blank(peso_termico),  # type: ignore[arg-type]
            serve_chuva=serve_chuva,
            # O formulário de edição sempre reenvia as estações selecionadas;
            # lista vazia significa "nenhuma estação".
            estacoes=estacoes or None,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    # `image` pode chegar como um UploadFile vazio quando nenhum arquivo é
    # enviado no multipart; normalizamos isso para None.
    new_image = image if (image is not None and image.filename) else None

    try:
        item = await wardrobe_service.update_item(
            db,
            user_id=current_user.id,
            item_id=item_id,
            data=data,
            image=new_image,
            storage=image_storage,
        )
    except WardrobeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_public(item)


@router.delete("/{item_id}", status_code=204, response_class=Response)
def delete_item(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        wardrobe_service.delete_item(
            db, user_id=current_user.id, item_id=item_id, storage=image_storage
        )
    except WardrobeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return Response(status_code=204)
