"""`python -m api_server.bootstrap` — el one-shot de finalización del ADR 0161.

Es lo que ejecuta el servicio `bootstrap` del compose generado:

    docker compose run --rm bootstrap

Corre DENTRO de la red del stack ya levantado, porque es donde tiene que correr:
el servicio `vault` no publica ningún puerto —el único que publica es Caddy, ADR
0061— y desde el host no es alcanzable. Ése fue el defecto que costó el rediseño.

## No acepta argumentos, y eso es la decisión de seguridad de este fichero

Un share de Shamir o una contraseña en `argv` queda a la vista de cualquier
usuario del host en `ps` y en el historial del shell. Aquí no se cierra ese
riesgo caso por caso: **no hay parser de argumentos**. Todo entra por el entorno
(ver `api_server.bootstrap.options`), y un argumento cualquiera es un error de
uso que sale con :attr:`~api_server.bootstrap.errors.ExitCode.BAD_INPUT`.

## Códigos de salida

Ver :class:`~api_server.bootstrap.errors.ExitCode`. Un `rc != 0` **o** un `rc = 0`
sin línea de revelado son los dos un fallo para el instalador, que para ahí con
el stack entero y el operador delante: seguir hasta el final para descubrirlo
cuando el instalador ya se ha autodestruido sería mucho peor.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Protocol, TextIO

from api_server.bootstrap.database import SqlAlchemyBootstrapDatabase, packaged_alembic_heads
from api_server.bootstrap.errors import BootstrapError, ExitCode, OptionsError, first_line
from api_server.bootstrap.hvac_client import HvacVaultClient
from api_server.bootstrap.options import BootstrapOptions, parse_options
from api_server.bootstrap.runner import BootstrapDeps, run_bootstrap


class Closable(Protocol):
    """Lo único que :func:`_amain` necesita saber de la base de datos: cerrarla."""

    async def aclose(self) -> None: ...


def report_failure(exc: BootstrapError, *, stderr: TextIO) -> int:
    """Un fallo, contado como mensaje y no como traza. Devuelve el código de salida.

    El instalador convierte esta salida en un `StepExecutionError` y se la enseña
    al operador; una traza de Python ahí no le dice qué hacer, y además puede
    llevar dentro el contenido de una fila. El mensaje ya viene explicado y
    redactado desde donde se levantó.
    """

    stderr.write(f"ERROR [{exc.exit_code.name}] {exc}\n")
    stderr.flush()
    return int(exc.exit_code)


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    args = sys.argv[1:] if argv is None else argv
    if args:
        # Antes de configurar nada: es un error de uso, no un fallo de ejecución.
        # Y NO se hace eco del argumento recibido — podría ser justo el secreto
        # que no debería haberse escrito en la línea de comandos.
        return report_failure(
            OptionsError(
                "Este one-shot no acepta argumentos de línea de comandos. Todo lo "
                "que necesita viaja por ENTORNO (lo pone el servicio `bootstrap` "
                "del compose generado), porque un share de Shamir o una "
                "contraseña en `argv` quedan a la vista en `ps` y en el historial "
                "del shell. Ejecútalo tal cual: "
                "`docker compose run --rm bootstrap`."
            ),
            stderr=err,
        )

    # Se configura DESPUÉS del chequeo de argumentos para no tocar el logging
    # global por un error de uso.
    from api_server.logging import configure_logging

    configure_logging(service="bootstrap")

    import structlog

    log = structlog.get_logger("api-server.bootstrap")

    try:
        options = parse_options(os.environ)
        vault_url = _vault_url()
        database = SqlAlchemyBootstrapDatabase()
        deps = BootstrapDeps(
            database=database,
            vault=_vault_client(vault_url, token=options.vault_token),
            stdout=out,
            log=log,
            expected_revisions=packaged_alembic_heads(),
            vault_url=vault_url,
        )
        asyncio.run(_amain(options, deps, database))
    except BootstrapError as exc:
        return report_failure(exc, stderr=err)
    except Exception as exc:
        # Nada llega aquí por diseño; si llega, sale igualmente como mensaje. Un
        # traceback en el stdout del `compose run` es lo que este módulo existe
        # para no producir.
        return report_failure(
            BootstrapError(
                f"Fallo inesperado del one-shot ({type(exc).__name__}): "
                f"{first_line(exc)}. Reprodúcelo con "
                "`docker compose run --rm bootstrap` para ver el detalle."
            ),
            stderr=err,
        )
    return int(ExitCode.OK)


async def _amain(options: BootstrapOptions, deps: BootstrapDeps, database: Closable) -> None:
    """El one-shot, con el cierre del pool dentro del MISMO bucle de eventos.

    El `finally` no es cortesía: `asyncio.run` cierra el loop al salir, y si el
    pool de asyncpg sigue vivo Python escribe `Exception ignored in: ...` por
    stderr. Eso aparecería justo debajo del revelado —y en la cola de salida que
    el instalador enseña cuando algo falla—, haciendo que un one-shot que ha
    terminado bien parezca roto.
    """

    try:
        await run_bootstrap(options, deps)
    finally:
        await database.aclose()


def _vault_url() -> str:
    """La dirección de Vault, de la Settings del api-server y no reinventada.

    `API_SERVER_VAULT_URL=http://vault:8200` ya viaja en el entorno del servicio
    porque `_bootstrap_service` reutiliza el `_api_server_env()` entero. Leerla de
    aquí —y no de un `VAULT_ADDR` propio— es lo que impide que las dos mitades
    apunten a sitios distintos.
    """

    from api_server.config import get_settings

    try:
        return str(get_settings().vault_url)
    except Exception as exc:
        raise OptionsError(
            "La configuración del api-server no se ha podido construir dentro "
            f"del one-shot ({type(exc).__name__}): {first_line(exc)}. `Settings` "
            "es fail-closed en producción, así que una variable que falte no "
            "degrada una función: impide construir el objeto. El servicio "
            "`bootstrap` recibe el `_api_server_env()` ENTERO precisamente por "
            "esto; si falta algo, falta en el `.env` generado."
        ) from exc


def _vault_client(url: str, *, token: str | None) -> HvacVaultClient:
    try:
        return HvacVaultClient.connect(url, token=token)
    except ImportError as exc:  # pragma: no cover - la imagen sí trae hvac
        raise BootstrapError(
            "Esta imagen no trae `hvac`, y sin él no se puede hablar con Vault. "
            "`hvac` está en las dependencias del api-server (no en un extra), así "
            "que una imagen sin él está mal construida."
        ) from exc


if __name__ == "__main__":  # pragma: no cover - punto de entrada
    raise SystemExit(main())
