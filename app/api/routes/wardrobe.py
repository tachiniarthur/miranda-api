"""
Rotas REST de peças de roupa (guarda-roupa), todas protegidas por autenticação.

Criação e atualização recebem os dados via `multipart/form-data`, pois incluem
o upload do arquivo de imagem já processado (fundo removido) pelo frontend.
"""

# NOTA: este módulo deliberadamente NÃO usa `from __future__ import annotations`.
# O decorator @limiter.limit do slowapi embrulha o endpoint com functools.wraps,
# que não copia `__globals__`. Com as anotações adiadas (em string), o FastAPI
# tentaria resolvê-las contra os globals do slowapi — onde os schemas deste
# módulo não existem — e passaria a tratar o corpo da requisição como query
# param. É a mesma armadilha já documentada em app/api/routes/auth.py.

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter, user_or_ip_key
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
from app.services.image_validation import (
    ImageValidationError,
    read_and_validate_upload,
)
from app.services.storage import (
    StorageError,
    authenticated_image_url,
    image_storage,
)
from app.services.wardrobe_service import (
    DuplicateImageError,
    QuotaExceededError,
    WardrobeError,
)

router = APIRouter(prefix="/wardrobe/items", tags=["wardrobe"])


def _none_if_blank(value: str | None) -> str | None:
    """Converte strings vazias vindas do formulário em None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


# Extensão do arquivo → media type devolvido pela rota de imagem. A extensão
# vem do formato REAL detectado no upload (ver image_validation.py), então não
# há como o cliente induzir um media type que não corresponda ao conteúdo.
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _to_public(item) -> ClothingItemPublic:
    """Serializa uma peça incluindo a URL absoluta da imagem."""
    # `image_url` não é uma coluna do ORM; anexamos como atributo transiente
    # na instância para que o model_validate (from_attributes) o encontre.
    item.image_url = authenticated_image_url(item.id)
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


@router.get("/{item_id}/image")
def get_item_image(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """
    Devolve o arquivo de imagem da peça, conferindo a posse antes.

    Substitui o mount estático `/static/clothing_images`, que servia QUALQUER
    imagem a QUALQUER pessoa que tivesse a URL, sem autenticação. O nome do
    arquivo é um uuid4 e portanto não é adivinhável, mas isso é segurança por
    obscuridade: bastava a URL vazar uma vez — no histórico do navegador, num
    header Referer, num print — para o acesso virar permanente e irrevogável.

    Aqui a peça é resolvida por `get_item`, a mesma função usada pelas rotas de
    leitura/edição/exclusão, que responde 404 quando a peça é de outro usuário.
    """
    try:
        item = wardrobe_service.get_item(
            db, user_id=current_user.id, item_id=item_id
        )
    except WardrobeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    path = image_storage.path_for(item.image_path)
    if not path.is_file():
        # Registro no banco sem arquivo em disco: trata como peça sem imagem em
        # vez de vazar detalhe do sistema de arquivos.
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")

    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        headers={
            # `private`: pode ficar no cache do navegador do dono, nunca em um
            # cache compartilhado (proxy/CDN), já que o conteúdo é por usuário.
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline",
        },
    )


@router.post("", response_model=ClothingItemPublic, status_code=201)
# O teto vai num lambda, e não na string direta, para ser lido a cada
# requisição: como string, o decorator congelaria o valor no import e mudar a
# configuração exigiria reiniciar o processo.
@limiter.limit(lambda: settings.WARDROBE_UPLOAD_RATE_LIMIT, key_func=user_or_ip_key)
async def create_item(
    request: Request,
    response: Response,
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
    except DuplicateImageError as exc:
        # 409 pelo mesmo motivo da quota: conflito com o estado da conta, não
        # falta de permissão.
        raise HTTPException(status_code=409, detail=str(exc))
    except QuotaExceededError as exc:
        # 409 e não 403: não é falta de permissão, é conflito com o estado atual
        # da conta — e o caminho para resolver é apagar uma peça, não pedir
        # acesso.
        raise HTTPException(status_code=409, detail=str(exc))
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_public(item)


@router.post("/analyze", response_model=AnalyzeResponse)
# Teto menor que o do upload: esta rota roda o FashionCLIP, o gasto de CPU mais
# caro do projeto.
@limiter.limit(lambda: settings.ANALYZE_RATE_LIMIT, key_func=user_or_ip_key)
async def analyze_item(
    request: Request,
    response: Response,
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

    O arquivo passa pela MESMA validação de tipo, tamanho e dimensões da rota de
    criação (app/services/image_validation.py) antes de qualquer processamento:
    esta rota entrega os bytes ao Pillow e ao FashionCLIP, então deixá-la sem
    teto significaria decodificar em memória o que o cliente quisesse mandar.
    """
    try:
        contents, _ext = await read_and_validate_upload(image)
    except ImageValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

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
