"""
Testes da validação compartilhada de imagens (app/services/image_validation.py).

Cobre o que a rota /analyze não tinha antes: teto de bytes, teto de dimensões e
verificação de que o conteúdo é mesmo uma imagem de formato aceito — em vez de
confiar no Content-Type, que quem escolhe é o cliente.
"""

from __future__ import annotations

import asyncio
import io
import struct
import zlib

import pytest
from PIL import Image

from app.services.image_validation import (
    read_and_validate_upload,
    MAX_FILE_BYTES,
    MAX_IMAGE_PIXELS,
    ImageValidationError,
    validate_image_bytes,
)


def _png(width: int = 10, height: int = 10) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 60, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _png_bomb(width: int, height: int) -> bytes:
    """
    PNG minúsculo cujo cabeçalho DECLARA dimensões enormes.

    É a forma clássica da "decompression bomb": poucos KB no fio que viram
    dezenas de GB se alguém decodificar os pixels. Reescrevemos o IHDR de um PNG
    real e recalculamos o CRC do chunk para que continue bem formado.
    """
    data = _png()
    # 8 bytes de assinatura, 4 de tamanho do chunk, 4 do tipo ("IHDR"),
    # depois 13 bytes de dados do IHDR e 4 de CRC.
    ihdr_start = 8 + 4 + 4
    ihdr = bytearray(data[ihdr_start : ihdr_start + 13])
    ihdr[0:4] = struct.pack(">I", width)
    ihdr[4:8] = struct.pack(">I", height)
    crc = zlib.crc32(b"IHDR" + bytes(ihdr)) & 0xFFFFFFFF
    return (
        data[:ihdr_start]
        + bytes(ihdr)
        + struct.pack(">I", crc)
        + data[ihdr_start + 13 + 4 :]
    )


# ── Casos aceitos ─────────────────────────────────────────────────────

def test_png_valido_e_aceito():
    assert validate_image_bytes(content_type="image/png", contents=_png()) == ".png"


def test_extensao_vem_do_formato_real_e_nao_do_content_type():
    """Cliente diz PNG mas manda JPEG: vale o cabeçalho do arquivo."""
    ext = validate_image_bytes(content_type="image/png", contents=_jpeg())
    assert ext == ".jpg"


# ── Casos recusados ───────────────────────────────────────────────────

def test_content_type_fora_da_allowlist_e_recusado():
    with pytest.raises(ImageValidationError) as exc:
        validate_image_bytes(content_type="application/pdf", contents=_png())
    assert exc.value.status_code == 415


def test_arquivo_vazio_e_recusado():
    with pytest.raises(ImageValidationError) as exc:
        validate_image_bytes(content_type="image/png", contents=b"")
    assert exc.value.status_code == 400


def test_arquivo_acima_do_teto_de_bytes_e_recusado():
    grande = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_FILE_BYTES + 1)
    with pytest.raises(ImageValidationError) as exc:
        validate_image_bytes(content_type="image/png", contents=grande)
    assert exc.value.status_code == 413
    assert "8 MB" in exc.value.message


def test_bytes_que_nao_sao_imagem_sao_recusados():
    """Content-Type de imagem não basta: o conteúdo é verificado de fato."""
    disfarçado = b"<html><script>alert(1)</script></html>"
    with pytest.raises(ImageValidationError) as exc:
        validate_image_bytes(content_type="image/png", contents=disfarçado)
    assert exc.value.status_code == 400


def test_decompression_bomb_e_recusada_sem_decodificar():
    """
    PNG de poucos KB declarando ~3,6 bilhões de pixels.

    O teste passa em milissegundos justamente porque a recusa acontece na
    leitura do cabeçalho — nenhum pixel chega a ser decodificado.
    """
    bomba = _png_bomb(60_000, 60_000)
    assert len(bomba) < 10_000, "a bomba precisa ser pequena no fio"

    with pytest.raises(ImageValidationError) as exc:
        validate_image_bytes(content_type="image/png", contents=bomba)
    assert exc.value.status_code == 413


def test_imagem_pouco_acima_do_teto_de_pixels_e_recusada():
    """Limite exato: acima de MAX_IMAGE_PIXELS recusa, mesmo sem virar 'bomba'."""
    lado = int(MAX_IMAGE_PIXELS**0.5) + 100
    with pytest.raises(ImageValidationError) as exc:
        validate_image_bytes(content_type="image/png", contents=_png_bomb(lado, lado))
    assert exc.value.status_code == 413


def test_teto_global_do_pillow_fica_configurado():
    """Rede de segurança para quem decodifica fora do caminho HTTP."""
    from app.services.image_validation import apply_pillow_limits

    apply_pillow_limits()
    assert Image.MAX_IMAGE_PIXELS == MAX_IMAGE_PIXELS


# ── Achado A3: o teto tem que valer ANTES da leitura ─────────────────────────
# O teto de 8 MB era conferido só depois de `await file.read()`, então um POST
# de 5 GB era parseado, gravado em arquivo temporário e trazido inteiro para a
# memória do processo antes de ser recusado. E o rate limit não protege esse
# caminho: o multipart é parseado antes de o decorator do slowapi rodar.
#
# Confirmado no Starlette 0.41.3 instalado que não há teto de tamanho para
# partes de arquivo — `formparsers.py:125` é só o limiar de spool para disco.
#
# Os testes usam `asyncio.run` em vez de pytest-asyncio: o projeto não tem esse
# plugin, e acrescentar dependência estava fora do escopo desta rodada.
class _UploadFalso:
    """Dublê mínimo de UploadFile que registra se o corpo chegou a ser lido."""

    def __init__(self, *, size: int | None, payload: bytes = b"") -> None:
        self.size = size
        self.content_type = "image/png"
        self.foi_lido = False
        self._payload = payload

    async def read(self) -> bytes:
        self.foi_lido = True
        return self._payload

    async def seek(self, offset: int) -> None:
        return None


def test_tamanho_acima_do_teto_recusa_sem_ler_o_corpo():
    upload = _UploadFalso(
        size=MAX_FILE_BYTES + 1,
        payload=b"\x89PNG\r\n\x1a\n" + b"x" * MAX_FILE_BYTES,
    )

    with pytest.raises(ImageValidationError) as exc:
        asyncio.run(read_and_validate_upload(upload))

    assert exc.value.status_code == 413
    assert upload.foi_lido is False, "o corpo foi lido antes de o teto ser conferido"


def test_arquivo_dentro_do_teto_continua_sendo_lido_e_validado():
    # O gate novo não pode recusar o que era aceito: um PNG legítimo passa.
    dados = _png(10, 10)
    upload = _UploadFalso(size=len(dados), payload=dados)

    contents, ext = asyncio.run(read_and_validate_upload(upload))

    assert upload.foi_lido is True
    assert contents == dados
    assert ext == ".png"


def test_sem_size_informado_a_validacao_por_bytes_ainda_segura():
    # Se o parser não informar `size`, o gate novo não tem o que conferir — e a
    # checagem por bytes de `validate_image_bytes` continua sendo a rede.
    grande = _UploadFalso(
        size=None, payload=b"\x89PNG\r\n\x1a\n" + b"x" * MAX_FILE_BYTES
    )

    with pytest.raises(ImageValidationError) as exc:
        asyncio.run(read_and_validate_upload(grande))

    assert exc.value.status_code == 413
    assert grande.foi_lido is True
