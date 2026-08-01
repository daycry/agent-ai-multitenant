"""Lectura ACOTADA de subidas multipart (plan prod-13, task_prod13_04 / api-2).

`await file.read()` devuelve el fichero entero en memoria del proceso, y solo
DESPUÉS se puede mirar cuánto ocupaba. En el api-server eso es un OOM del
proceso que atiende todas las requests REST y todos los WebSockets, disparable
por cualquiera con permiso de subir a una KB. Este módulo lee por trozos y para
en cuanto sabe que se pasa del tope.

Dos comprobaciones, y las dos hacen falta:

  * el `Content-Length` declarado permite rechazar **sin leer un byte**, que es
    lo barato;
  * la lectura por trozos vuelve a comprobar el tamaño REAL, porque el header lo
    escribe el cliente y puede mentir o faltar. Creerle sería toda la guarda.

Límite residual, dicho en voz alta: cuando el endpoint declara un parámetro
`UploadFile`, Starlette ya ha parseado el multipart antes de que el handler
corra, así que un cuerpo enorme ya ha pasado por el `SpooledTemporaryFile` (a
DISCO más allá del umbral de spooling, no al heap). Lo que este módulo evita es
el paso siguiente —traerse ese fichero entero a memoria— y el trabajo posterior
(MinIO, fila en `documents`). Cortar antes de parsear exige un middleware ASGI
por `Content-Length`, que es otra tarea.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from starlette.datastructures import UploadFile

__all__ = ["DEFAULT_CHUNK_SIZE", "MULTIPART_OVERHEAD_ALLOWANCE", "read_capped_upload"]

#: Trozo de lectura. 1 MiB: bastante grande para no hacer miles de `read()` en un
#: fichero de 50 MiB, bastante pequeño para que el pico de memoria de un rechazo
#: sea despreciable.
DEFAULT_CHUNK_SIZE = 1024 * 1024

#: Margen que se le concede al `Content-Length` DECLARADO antes de rechazar sin
#: leer. Ese header mide el REQUEST entero —boundaries del multipart, nombres de
#: campo, el `title` del formulario—, no el fichero. Sin margen, un fichero de
#: exactamente el tope declararía unos cientos de bytes de más y se llevaría un
#: 413: el tope real sería silenciosamente menor que el anunciado. Con margen, el
#: rechazo temprano solo actúa sobre lo que es indiscutiblemente enorme, y el
#: tope exacto lo sigue decidiendo la lectura real.
MULTIPART_OVERHEAD_ALLOWANCE = 1024 * 1024


async def read_capped_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    declared_content_length: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    detail: str | None = None,
) -> bytes:
    """Devuelve los bytes de ``file``, o levanta 413 si pasan de ``max_bytes``.

    ``max_bytes`` es INCLUSIVO: un fichero de exactamente ese tamaño se acepta.
    Nunca se acumulan más de ``max_bytes + chunk_size`` bytes en memoria.

    ``declared_content_length`` es el `Content-Length` del request (``None`` si
    falta o no es un entero). Solo se usa para el rechazo temprano, y con el
    margen de :data:`MULTIPART_OVERHEAD_ALLOWANCE`.
    """
    message = detail or f"upload exceeds {max_bytes} bytes"
    too_large = HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=message)

    if declared_content_length is not None and declared_content_length > (
        max_bytes + MULTIPART_OVERHEAD_ALLOWANCE
    ):
        raise too_large

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            # Ni se guarda este trozo ni se lee el siguiente: a partir de aquí
            # cada byte leído es memoria gastada en algo que ya está rechazado.
            raise too_large
        chunks.append(chunk)

    return b"".join(chunks)


def declared_content_length(headers: object) -> int | None:
    """Extrae el `Content-Length` de unas cabeceras, o ``None`` si no es útil.

    ``None`` cubre los tres casos en que no se puede confiar en el header —
    ausente, no numérico o negativo—, y todos ellos significan lo mismo aguas
    arriba: no hay rechazo temprano, decide la lectura real.
    """
    raw = getattr(headers, "get", lambda _k: None)("content-length")
    if not isinstance(raw, str) or not raw.lstrip("+").isdigit():
        return None
    value = int(raw)
    return value if value >= 0 else None
