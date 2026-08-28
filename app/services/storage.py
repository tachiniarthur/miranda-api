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
from app.services.image_validation import (
    ImageValidationError,
    read_and_validate_upload,
)

# As regras de tipo/tamanho/dimensão vivem em app/services/image_validation.py,
# compartilhadas com a rota /analyze — que não passa por aqui e antes ficava sem
# nenhuma delas.


class StorageError(Exception):
    """Erro genérico da camada de storage (arquivo inválido, grande demais...)."""


class ImageStorage(Protocol):
    """Interface que qualquer backend de storage deve implementar."""

    async def save(
        self, file: UploadFile, *, validated: tuple[bytes, str] | None = None
    ) -> str:
        """
        Salva o arquivo e retorna um `image_path` opaco.

        `validated` permite a quem já leu e validou o arquivo passar o
        resultado adiante, em vez de fazer o storage reler tudo — ver o
        comentário em `LocalImageStorage.save`.
        """
        ...

    def delete(self, image_path: str) -> None:
        """Remove o arquivo associado a `image_path` (idempotente)."""
        ...

    def path_for(self, image_path: str) -> Path:
        """Retorna o caminho físico do arquivo de `image_path`."""
        ...


class LocalImageStorage:
    """Implementação que grava os arquivos no disco local do servidor."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def save(
        self, file: UploadFile, *, validated: tuple[bytes, str] | None = None
    ) -> str:
        # Quem já leu e validou passa o resultado em `validated`. Sem isso, o
        # `create_item` lia o arquivo duas vezes — uma para validar e checar
        # duplicata, outra aqui — dobrando o pico de memória por requisição
        # aprovada, com um `seek(0)` no meio para o ponteiro voltar.
        if validated is not None:
            contents, ext = validated
        else:
            try:
                contents, ext = await read_and_validate_upload(file)
            except ImageValidationError as exc:
                # Preserva o contrato desta camada: quem chama `save` trata
                # StorageError. A extensão vem do formato REAL detectado no
                # cabeçalho, não do Content-Type informado pelo cliente.
                raise StorageError(exc.message) from exc

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

    def path_for(self, image_path: str) -> Path:
        # `Path(...).name` descarta qualquer diretório embutido em image_path:
        # mesmo que um valor malformado chegue ao banco, ele não escapa da pasta
        # de storage.
        return self._base_dir / Path(image_path).name


# Instância única usada pela aplicação. Trocar esta linha (e nada mais) é o
# suficiente para migrar de storage local para nuvem no futuro.
image_storage: ImageStorage = LocalImageStorage(base_dir=settings.STORAGE_DIR)


def authenticated_image_url(item_id) -> str:
    """
    URL pública da imagem de uma peça.

    Aponta para a rota AUTENTICADA `/api/wardrobe/items/{id}/image`, que confere
    a posse antes de devolver o arquivo. Antes, as imagens eram servidas por um
    mount estático sem autenticação: o nome do arquivo era um uuid4 (não
    adivinhável), mas a URL, uma vez vazada — histórico do navegador, header
    Referer, um print compartilhado —, dava acesso permanente e a qualquer um.
    """
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/wardrobe/items/{item_id}/image"
