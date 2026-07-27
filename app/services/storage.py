"""
Camada de armazenamento de imagens.

Toda leitura/escrita/remoção de arquivos de imagem passa por aqui. O restante
da aplicação lida apenas com `image_path` (uma string opaca) e com `image_url`
(a URL pública). Para migrar para um storage em nuvem (S3, GCS, etc.) no
futuro, basta reimplementar `LocalImageStorage` — ou criar outra classe com a
mesma interface `ImageStorage` — sem tocar nas rotas, serviços ou modelos.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from fastapi import UploadFile

from app.core.config import settings

# Extensões de imagem aceitas → extensão canônica salva em disco.
_ALLOWED_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}

# Tamanho máximo do arquivo (8 MB). Evita que uploads gigantes estourem o disco.
_MAX_FILE_BYTES = 8 * 1024 * 1024


class StorageError(Exception):
    """Erro genérico da camada de storage (arquivo inválido, grande demais...)."""


class ImageStorage(Protocol):
    """Interface que qualquer backend de storage deve implementar."""

    async def save(self, file: UploadFile) -> str:
        """Salva o arquivo e retorna um `image_path` opaco."""
        ...

    def delete(self, image_path: str) -> None:
        """Remove o arquivo associado a `image_path` (idempotente)."""
        ...

    def url_for(self, image_path: str) -> str:
        """Retorna a URL pública absoluta para `image_path`."""
        ...


class LocalImageStorage:
    """Implementação que grava os arquivos no disco local do servidor."""

    def __init__(self, base_dir: str, url_prefix: str, public_base_url: str) -> None:
        self._base_dir = Path(base_dir)
        self._url_prefix = url_prefix.rstrip("/")
        self._public_base_url = public_base_url.rstrip("/")
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, file: UploadFile) -> str:
        ext = _ALLOWED_CONTENT_TYPES.get((file.content_type or "").lower())
        if ext is None:
            raise StorageError(
                "Formato de imagem não suportado. Envie PNG, JPEG ou WebP."
            )

        contents = await file.read()
        if len(contents) == 0:
            raise StorageError("Arquivo de imagem vazio.")
        if len(contents) > _MAX_FILE_BYTES:
            raise StorageError("Imagem muito grande (máximo 8 MB).")

        filename = f"{uuid.uuid4().hex}{ext}"
        destination = self._base_dir / filename
        destination.write_bytes(contents)
        # `image_path` é apenas o nome do arquivo, relativo à pasta de storage.
        return filename

    def delete(self, image_path: str) -> None:
        if not image_path:
            return
        target = self._base_dir / Path(image_path).name  # evita path traversal
        try:
            target.unlink(missing_ok=True)
        except OSError:
            # Falha ao remover o arquivo não deve impedir a exclusão do registro.
            pass

    def url_for(self, image_path: str) -> str:
        return f"{self._public_base_url}{self._url_prefix}/{image_path}"


# Instância única usada pela aplicação. Trocar esta linha (e nada mais) é o
# suficiente para migrar de storage local para nuvem no futuro.
image_storage: ImageStorage = LocalImageStorage(
    base_dir=settings.STORAGE_DIR,
    url_prefix=settings.STORAGE_URL_PREFIX,
    public_base_url=settings.PUBLIC_BASE_URL,
)
