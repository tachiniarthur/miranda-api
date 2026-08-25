"""
Validação compartilhada de imagens enviadas pelo cliente.

Antes, estas regras viviam dentro de `LocalImageStorage.save`, então só valiam
para o caminho que persistia a peça. A rota `/api/wardrobe/items/analyze` não
passa pelo storage — ela lê os bytes e entrega direto ao Pillow e ao FashionCLIP
— e por isso ficava sem teto de tamanho e sem checagem de tipo. Este módulo
existe para que os dois caminhos compartilhem exatamente a mesma porta de
entrada.

O que é verificado, em ordem de custo crescente:

  1. Content-Type declarado está na allowlist  (gate barato, mensagem clara);
  2. arquivo não vazio e dentro do teto de bytes;
  3. o conteúdo REALMENTE é uma imagem de um formato aceito — lido do cabeçalho
     pelo Pillow, não do header HTTP, que é escolhido pelo cliente;
  4. as dimensões declaradas no cabeçalho cabem no teto de pixels.

O passo 4 é o que neutraliza a "decompression bomb": um PNG de poucos KB pode
declarar 50.000 x 50.000 px e consumir dezenas de GB ao ser decodificado.
`Image.open()` só lê o cabeçalho (é preguiçoso), então dá para conferir
`size` ANTES de qualquer decodificação de pixel.
"""

from __future__ import annotations

import io

from fastapi import UploadFile

# Content-Types aceitos → extensão canônica.
ALLOWED_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}

# Formatos que o Pillow pode reportar → extensão canônica. É esta tabela, e não
# o Content-Type do cliente, que decide a extensão gravada em disco.
ALLOWED_PILLOW_FORMATS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
}

# Tamanho máximo do arquivo (8 MB). Evita que uploads gigantes estourem o disco.
MAX_FILE_BYTES = 8 * 1024 * 1024

# Teto de pixels decodificados (~40 MP, bem acima de qualquer foto de celular).
# Serve tanto para a checagem explícita aqui quanto como limite global do Pillow.
MAX_IMAGE_PIXELS = 40_000_000


class ImageValidationError(Exception):
    """Imagem recusada na validação, com o status HTTP adequado ao motivo."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def apply_pillow_limits() -> None:
    """
    Define o teto global de pixels do Pillow.

    Rede de segurança para os pontos que decodificam imagem sem passar por
    `validate_image_bytes` (scripts, testes, chamadas diretas à camada de IA).
    Acima do teto o Pillow avisa; acima do dobro ele levanta
    `DecompressionBombError`. É idempotente e barato, então pode ser chamada
    junto de cada import local do Pillow.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def validate_image_bytes(*, content_type: str | None, contents: bytes) -> str:
    """
    Valida os bytes de uma imagem e devolve a extensão canônica do formato real.

    Levanta `ImageValidationError` no primeiro problema encontrado.
    """
    from PIL import Image, UnidentifiedImageError

    apply_pillow_limits()

    if ALLOWED_CONTENT_TYPES.get((content_type or "").lower()) is None:
        raise ImageValidationError(
            "Formato de imagem não suportado. Envie PNG, JPEG ou WebP.",
            status_code=415,
        )

    if len(contents) == 0:
        raise ImageValidationError("Arquivo de imagem vazio.", status_code=400)

    if len(contents) > MAX_FILE_BYTES:
        raise ImageValidationError(
            "Imagem muito grande (máximo 8 MB).", status_code=413
        )

    # `Image.open` lê apenas o cabeçalho: valida o formato de verdade e expõe as
    # dimensões sem decodificar os pixels.
    try:
        with Image.open(io.BytesIO(contents)) as probe:
            image_format = probe.format
            width, height = probe.size
    except UnidentifiedImageError:
        raise ImageValidationError(
            "O arquivo enviado não é uma imagem válida.", status_code=400
        )
    except Image.DecompressionBombError:
        raise ImageValidationError(
            "Imagem com dimensões grandes demais.", status_code=413
        )
    except Exception:
        # Cabeçalho corrompido / truncado — qualquer plugin do Pillow pode
        # estourar aqui com uma exceção própria.
        raise ImageValidationError(
            "Não foi possível ler a imagem enviada.", status_code=400
        )

    ext = ALLOWED_PILLOW_FORMATS.get(image_format or "")
    if ext is None:
        raise ImageValidationError(
            "Formato de imagem não suportado. Envie PNG, JPEG ou WebP.",
            status_code=415,
        )

    if width * height > MAX_IMAGE_PIXELS:
        raise ImageValidationError(
            "Imagem com dimensões grandes demais.", status_code=413
        )

    return ext


async def read_and_validate_upload(file: UploadFile) -> tuple[bytes, str]:
    """
    Lê um `UploadFile` por inteiro e o valida.

    Devolve `(bytes, extensão canônica)`. A leitura acontece depois do gate de
    Content-Type justamente para descartar cedo o que já se sabe inválido.
    """
    if ALLOWED_CONTENT_TYPES.get((file.content_type or "").lower()) is None:
        raise ImageValidationError(
            "Formato de imagem não suportado. Envie PNG, JPEG ou WebP.",
            status_code=415,
        )

    contents = await file.read()
    ext = validate_image_bytes(content_type=file.content_type, contents=contents)
    return contents, ext


def perceptual_hash(contents: bytes) -> str | None:
    """
    dHash de 64 bits da imagem, em hexadecimal (16 caracteres).

    ── Por que perceptual e não SHA-256 ────────────────────────────────────
    Um hash criptográfico muda inteiro se um único byte mudar, então
    recomprimir o JPEG, redimensionar ou só salvar de novo já burlaria a
    checagem de reenvio — e essas são exatamente as transformações que um
    script usaria para encher a quota de graça.

    ── O que ele NÃO promete ───────────────────────────────────────────────
    Recompressão AGRESSIVA (JPEG abaixo de ~90 de qualidade) chega a virar um
    ou dois bits em regiões chapadas, onde vizinhos quase idênticos fazem a
    comparação depender de ruído. Aí o reenvio passa. A comparação continua
    sendo por igualdade exata mesmo assim, de propósito: aceitar "quase igual"
    pegaria peças legítimas parecidas, e recusar uma peça de verdade é pior do
    que deixar passar um reenvio. Erra-se para o lado seguro.

    ── Como funciona ───────────────────────────────────────────────────────
    Reduz para 9x8 em tons de cinza e compara cada pixel com o vizinho da
    direita: 8 comparações por linha, 8 linhas, 64 bits. O resultado depende da
    ESTRUTURA da imagem (onde ela fica mais clara e mais escura), não dos bytes
    — por isso sobrevive à recompressão.

    Implementado à mão de propósito: a biblioteca `imagehash` traria `scipy`
    junto, o que é desproporcional para quinze linhas.

    Returns:
        O hash, ou `None` se os bytes não abrirem como imagem OU se a imagem for
        estruturalmente degenerada (ver abaixo). Não lança: a validação de
        imagem já roda antes, e um erro aqui não pode derrubar um upload que ela
        aprovou.
    """
    # Import tardio pelo mesmo motivo do resto do módulo: o Pillow é pesado e
    # não precisa entrar no import da aplicação.
    from PIL import Image

    try:
        with Image.open(io.BytesIO(contents)) as img:
            reduzida = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            # `tobytes()` em vez de `getdata()`: em modo "L" são exatamente os
            # 72 valores de 0 a 255, e o outro está a caminho da remoção.
            pixels = reduzida.tobytes()
    except Exception:  # noqa: BLE001
        return None

    bits = 0
    for linha in range(8):
        base = linha * 9
        for coluna in range(8):
            bits <<= 1
            if pixels[base + coluna] > pixels[base + coluna + 1]:
                bits |= 1

    # Imagem sem estrutura (fundo chapado, silhueta muito uniforme) produz 64
    # bits todos iguais — e DUAS peças diferentes assim colidiriam, fazendo a
    # checagem recusar um cadastro legítimo. Um hash degenerado vale menos que
    # nenhum: devolvemos None e a peça simplesmente não participa da checagem.
    if bits in (0, (1 << 64) - 1):
        return None

    return f"{bits:016x}"
