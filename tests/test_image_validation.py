"""
Testes da validação compartilhada de imagens (app/services/image_validation.py).

Cobre o que a rota /analyze não tinha antes: teto de bytes, teto de dimensões e
verificação de que o conteúdo é mesmo uma imagem de formato aceito — em vez de
confiar no Content-Type, que quem escolhe é o cliente.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from app.services.image_validation import (
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
