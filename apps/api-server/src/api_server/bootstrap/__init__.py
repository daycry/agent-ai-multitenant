"""El one-shot de finalización de la instalación — segunda mitad del paso 8 del ADR 0161.

`docker compose run --rm bootstrap` ejecuta `python -m api_server.bootstrap`
dentro de la red del stack ya levantado y hace, una sola vez, lo que ningún otro
camino hacía: inicializa Vault, siembra el tenant inicial con su primer System
Owner, siembra el catálogo built-in y **revela las credenciales una vez**.

## Por qué vive en la imagen del api-server y no en la del instalador

Porque el servicio `vault` **no publica ningún puerto** —el único que publica es
Caddy (ADR 0061)— y el instalador, que corre en el host o en su propio
contenedor, no lo alcanza. El camino anterior hablaba con `127.0.0.1:8200` y
moría con una traza cruda; el banner del CLI ofrecía como salida de emergencia
justamente ese camino roto. Desde el ADR 0161 las dos mitades comparten destino:
o llegan las dos, o no llega ninguna.

Y dentro de esta imagen y no en una séptima porque es la que ya trae las dos
cosas que hacen falta: los seeds (`api_server.seeds`,
`api_server.seeds.init_tenant`) y `hvac`.

## El mapa del paquete

===================  ====================================================
`errors`             códigos de salida, redacción y la jerarquía de fallos
`options`            los argumentos, que viajan por ENTORNO y nunca por `argv`
`vault`              init + unseal + KV v2 + las cuatro políticas
`hvac_client`        el adaptador real sobre `hvac`
`database`           pre-flight de esquema + tenant inicial + catálogo
`reveal`             la línea de contrato con el instalador
`runner`             el orden de los cuatro pasos, y los porqués
`__main__`           el punto de entrada
===================  ====================================================

El contrato con la otra mitad (`installer_backend.real_step_executor`) es una
línea de JSON en stdout marcada con ``bootstrap.reveal``. Lo fijó aquella mitad y
aquí no se cambia: se implementa, y hay un test que cruza esta salida con SU
parser de verdad para que las dos no puedan derivar.
"""

from __future__ import annotations

from api_server.bootstrap.errors import (
    BootstrapError,
    DatabaseError,
    ExitCode,
    OptionsError,
    SchemaNotReadyError,
)
from api_server.bootstrap.options import BootstrapOptions, parse_options
from api_server.bootstrap.reveal import BOOTSTRAP_REVEAL_EVENT, Reveal, emit_reveal
from api_server.bootstrap.runner import BootstrapDeps, mint_admin_password, run_bootstrap
from api_server.bootstrap.vault import VaultBootstrapError, bootstrap_vault, initial_policies

__all__ = [
    "BOOTSTRAP_REVEAL_EVENT",
    "BootstrapDeps",
    "BootstrapError",
    "BootstrapOptions",
    "DatabaseError",
    "ExitCode",
    "OptionsError",
    "Reveal",
    "SchemaNotReadyError",
    "VaultBootstrapError",
    "bootstrap_vault",
    "emit_reveal",
    "initial_policies",
    "mint_admin_password",
    "parse_options",
    "run_bootstrap",
]
