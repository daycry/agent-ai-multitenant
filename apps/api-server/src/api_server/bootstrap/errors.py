"""Los fallos del one-shot: un mensaje para el operador y un código de salida.

## Por qué códigos de salida y no un booleano

Quien ejecuta este módulo es `docker compose run --rm bootstrap`, y quien lee su
resultado es un humano delante de una terminal o
:meth:`installer_backend.real_step_executor.RealStepExecutor._bootstrap_vault`,
que convierte lo que salga en un
:class:`~installer_backend.install.StepExecutionError`. Ninguno de los dos ve un
`raise`: ven `rc` y stdout. Un `rc=1` genérico con una traza de Python encima es
lo que este módulo NO puede producir — el operador no distingue «tu Vault está
sellado» de «te falta una migración» de «me he roto yo».

## Por qué el mensaje no lleva nunca un secreto

Lo dice la mitad de enfrente por escrito: el instalador filtra sus líneas de
progreso **por valor** (``_redact_bootstrap_output``) precisamente porque no se
quiso fiar de este módulo. Esta mitad tampoco se fía de sí misma:
:func:`redact` se aplica a todo lo que sale por un error, y el material de una
sola vez —unseal keys, root token, contraseña de admin— nunca vuelve a
imprimirse fuera de la línea de revelado.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum

#: Lo que se enseña en lugar de un secreto que se coló en un mensaje de error.
REDACTED = "***REDACTED***"

#: Longitud máxima de un detalle de excepción que se propaga al operador. Corta
#: el `[SQL: ...] [parameters: ...]` que SQLAlchemy adjunta, que es donde viajan
#: los valores de la fila (un hash argon2, por ejemplo) sin aportar diagnóstico.
MAX_DETAIL_CHARS = 300


class ExitCode(IntEnum):
    """El código con el que sale el proceso. Cerrado y con significado.

    Se separan porque **lo que el operador tiene que HACER es distinto en cada
    caso**: un 2 se arregla en el ``install.yaml``, un 3 corriendo las
    migraciones, un 4 mirando Vault y un 5 mirando PostgreSQL. Un `rc=1` para
    todo obliga a leer la salida entera para saber a dónde ir.
    """

    OK = 0
    #: Nada de lo anterior: un fallo que este módulo no supo clasificar. Sale
    #: como mensaje igualmente — una traza cruda no es un diagnóstico.
    UNEXPECTED = 1
    #: Los argumentos del one-shot (entorno) están mal o faltan.
    BAD_INPUT = 2
    #: El esquema no está migrado, o no está en el head de ESTA imagen.
    SCHEMA_NOT_READY = 3
    #: Vault no responde, está sellado sin claves, o rechazó una operación.
    VAULT = 4
    #: PostgreSQL no responde o rechazó la siembra.
    DATABASE = 5


def redact(text: str, secrets: Iterable[str]) -> str:
    """Tapa por VALOR todo secreto que se haya colado en *text*.

    Es la misma disciplina que ``_redact_bootstrap_output`` en el instalador, y
    por el mismo motivo: el contrato dice que el material sólo sale por la línea
    de revelado, pero una propiedad de seguridad que depende de que nadie se
    equivoque nunca no es una propiedad de seguridad. Los secretos vacíos se
    ignoran (si no, se sustituiría la cadena entera).
    """

    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def first_line(exc: BaseException, *, limit: int = MAX_DETAIL_CHARS) -> str:
    """El detalle de una excepción que SÍ se le puede enseñar al operador.

    Sólo la primera línea, y truncada. En un error de SQLAlchemy esa línea es
    justo el diagnóstico —``(asyncpg.exceptions.UndefinedTableError) relation
    "organizations" does not exist``— y todo lo que viene detrás (``[SQL: ...]``,
    ``[parameters: ...]``) es el contenido de la fila, que puede llevar material
    que no tiene por qué salir a una terminal ni a un log de CI.
    """

    lines = [line for line in str(exc).strip().splitlines() if line.strip()]
    detail = lines[0].strip() if lines else type(exc).__name__
    if len(detail) > limit:
        detail = detail[:limit].rstrip() + "…"
    return detail


class BootstrapError(Exception):
    """Un fallo del one-shot que ya está explicado. NUNCA lleva un secreto.

    El mensaje se le enseña al operador tal cual (por stderr, y por la salida
    del `docker compose run` que el instalador recoge), así que se escribe en
    castellano y diciendo qué hacer, no qué falló por dentro.
    """

    exit_code: ExitCode = ExitCode.UNEXPECTED


class OptionsError(BootstrapError):
    """Los argumentos del one-shot (que viajan por ENTORNO) están mal."""

    exit_code = ExitCode.BAD_INPUT


class SchemaNotReadyError(BootstrapError):
    """El esquema no está donde tiene que estar para sembrar.

    Se levanta ANTES de tocar Vault. Ése es todo el propósito: un fallo de
    esquema descubierto después del `operator init` cuesta unas unseal keys
    irrecuperables; descubierto antes cuesta este mensaje.
    """

    exit_code = ExitCode.SCHEMA_NOT_READY


class DatabaseError(BootstrapError):
    """PostgreSQL no responde, o rechazó la siembra."""

    exit_code = ExitCode.DATABASE
