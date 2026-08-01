"""prod-13 · task_prod13_04 — leer una subida SIN materializarla entera (api-2).

`POST /knowledge-bases/{kb_id}/documents` hacía `payload = await file.read()` y
comprobaba el tamaño DESPUÉS. Con un fichero de 2 GB eso son 2 GB en el heap del
api-server ANTES de saber que hay que rechazarlo: un OOM del proceso que atiende
TODAS las requests y todos los WebSockets, provocable por cualquiera que pueda
subir a una KB.

Lo que se fija aquí:

  * el lector NUNCA acumula más de `max_bytes + 1` — un byte de más basta para
    saber que se pasa, y ese byte extra es lo que distingue «exactamente el
    límite» (aceptar) de «el límite más uno» (413);
  * el límite es INCLUSIVO: un fichero de exactamente `max_bytes` se acepta;
  * el `Content-Length` declarado permite rechazar antes de leer nada, pero
    **no se cree a ciegas**: un header mentiroso o ausente no cuela un cuerpo
    grande, porque la lectura por trozos vuelve a comprobarlo;
  * el rechazo es 413.
"""

from __future__ import annotations

import io

import pytest
from api_server.routers._uploads import read_capped_upload
from fastapi import HTTPException
from starlette.datastructures import UploadFile

pytestmark = pytest.mark.unit

_MAX = 1024

# Un `Content-Length` que supera el tope MÁS el margen de multipart, o sea
# indiscutiblemente enorme: es el único caso en que el rechazo temprano actúa.
_HUGE_DECLARED = 500 * 1024 * 1024


class _CountingBytesIO(io.BytesIO):
    """Un BytesIO que recuerda cuántos bytes ha servido en total."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.served = 0

    def read(self, size: int = -1) -> bytes:
        chunk = super().read(size)
        self.served += len(chunk)
        return chunk


def _upload(data: bytes) -> tuple[UploadFile, _CountingBytesIO]:
    buffer = _CountingBytesIO(data)
    return UploadFile(file=buffer, filename="doc.pdf", size=len(data)), buffer


@pytest.mark.asyncio
async def test_a_file_exactly_at_the_limit_is_accepted() -> None:
    upload, _ = _upload(b"x" * _MAX)

    payload = await read_capped_upload(upload, max_bytes=_MAX)

    assert len(payload) == _MAX, "el límite es inclusivo: exactamente max_bytes se acepta"


@pytest.mark.asyncio
async def test_one_byte_over_the_limit_is_rejected_with_413() -> None:
    upload, _ = _upload(b"x" * (_MAX + 1))

    with pytest.raises(HTTPException) as exc:
        await read_capped_upload(upload, max_bytes=_MAX)

    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_an_oversized_body_is_never_fully_materialised() -> None:
    """La aserción que importa: se deja de leer en cuanto se sabe que sobra."""
    huge = _MAX * 500
    upload, buffer = _upload(b"x" * huge)

    with pytest.raises(HTTPException):
        await read_capped_upload(upload, max_bytes=_MAX, chunk_size=64)

    assert buffer.served <= _MAX + 64, (
        "el lector se tragó el cuerpo entero antes de rechazarlo: sirvió"
        f" {buffer.served} de {huge} bytes, que es justo el OOM que este cambio"
        " venía a evitar"
    )


@pytest.mark.asyncio
async def test_a_declared_content_length_over_the_limit_rejects_before_reading() -> None:
    upload, buffer = _upload(b"x" * 10)

    with pytest.raises(HTTPException) as exc:
        await read_capped_upload(upload, max_bytes=_MAX, declared_content_length=_HUGE_DECLARED)

    assert exc.value.status_code == 413
    assert buffer.served == 0, "se rechazó por el header, no debería haber leído ni un byte"


@pytest.mark.asyncio
async def test_a_lying_content_length_does_not_smuggle_an_oversized_body() -> None:
    """El header lo escribe el cliente: creerle sería la guarda entera."""
    upload, _ = _upload(b"x" * (_MAX + 1))

    with pytest.raises(HTTPException) as exc:
        await read_capped_upload(upload, max_bytes=_MAX, declared_content_length=1)

    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_a_missing_or_unparsable_content_length_still_reads_and_checks() -> None:
    for declared in (None, -1):
        upload, _ = _upload(b"x" * (_MAX + 1))
        with pytest.raises(HTTPException) as exc:
            await read_capped_upload(upload, max_bytes=_MAX, declared_content_length=declared)
        assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_multipart_envelope_overhead_does_not_reject_a_legal_file() -> None:
    """`Content-Length` es el del REQUEST, no el del fichero.

    En multipart lleva encima los boundaries y los demás campos del formulario,
    así que un fichero de exactamente el límite declara algo más que el límite.
    Rechazar por `declared > max_bytes` a secas convertiría el tope en un tope
    silenciosamente MENOR y daría 413 a subidas legales.
    """
    upload, _ = _upload(b"x" * _MAX)

    payload = await read_capped_upload(upload, max_bytes=_MAX, declared_content_length=_MAX + 300)

    assert len(payload) == _MAX
