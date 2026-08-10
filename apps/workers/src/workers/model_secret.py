"""La credencial del proveedor LLM, fuera del env del contenedor (prod-07 task_prod07_10).

El problema
-----------
El worker resuelve el modelo (kind + endpoint + credencial de Vault) y mete el
spec ENTERO en una sola variable de entorno, ``AGENT_TASK_SPEC``
(``workers/execution.py``). Con la credencial dentro, esa variable la ve
cualquiera que pueda hacer ``docker inspect`` del contenedor, aparece en los
volcados del daemon, la heredan todos los procesos hijos del sandbox y viaja a
cualquier sitio donde alguien registre el entorno «para depurar». El agente sólo
necesita *usar* la credencial, no *verla* en su entorno.

La forma
--------
La misma que Docker Compose usa para sus ``secrets:`` y que este repo ya tenía
escrita sin llamante productivo (:mod:`workers.secrets`): el valor se materializa
en un fichero en el host del worker y se **bind-montea read-only** bajo
``/run/secrets``. En el env sólo viaja el PUNTERO
(``spec["model"]["credentials_file"]``), que no es un secreto.

    Vault → spec resuelto → split → fichero 0444 en el staging → mount RO
          → /run/secrets/model-credentials.json → el runtime lo hidrata al arrancar.

Por qué un bind y no un `tmpfs` de Docker
-----------------------------------------
El plan pedía «mount tmpfs read-only». Un ``--tmpfs`` de Docker nace VACÍO: no
hay forma de pre-cargarlo con contenido, así que no sirve para entregar un valor.
Y el worker lanza contenedores HERMANOS a través del daemon, que resuelve el
origen de un bind en el HOST, no dentro del worker — escribir en el ``/dev/shm``
del worker (que sí es tmpfs) no le serviría al daemon. Por eso el staging vive
bajo ``data_root``, la ÚNICA ruta que el compose garantiza idéntica en host y en
worker (``WORKERS_DATA_ROOT=/var/lib/docker/volumes/agentic-platform-agent-data/_data``);
es la misma razón por la que el worktree se monta desde ahí y funciona.

Residuo, escrito porque existe: el fichero toca el disco del host mientras dura
el run. Se acota con (a) directorio por lanzamiento con nombre aleatorio,
(b) modo 0444 y (c) borrado en un ``finally``, pase lo que pase con el run.
Eliminarlo del todo pediría que el runtime fuese a buscar la credencial a la API
interna con su token — otra tarea, y con su propio coste.

Compatibilidad de formato
-------------------------
El runtime acepta LOS DOS formatos (credencial en línea, o puntero + fichero):
:func:`agent_runtime.spec_secrets.hydrate_model_credentials` sólo actúa si
encuentra la clave del puntero. O sea: **imagen nueva + worker viejo funciona**.
Lo que NO funciona es worker nuevo + imagen vieja — la imagen vieja ignoraría el
puntero y arrancaría sin credencial. Por eso hay válvula de escape,
``WORKERS_MODEL_CREDENTIAL_FILE=false``, que devuelve el formato en línea sin
tocar el compose ni reconstruir nada.
"""

from __future__ import annotations

import json
from typing import Any

from shared_llm.credential_fields import CREDENTIAL_FIELDS

from workers.secrets import SECRETS_DIR, StagedSecrets, stage_secrets

# Nombre del fichero dentro del mount y clave del puntero en el spec del modelo.
MODEL_CREDENTIALS_FILENAME = "model-credentials.json"
MODEL_CREDENTIALS_PATH = f"{SECRETS_DIR}/{MODEL_CREDENTIALS_FILENAME}"
CREDENTIALS_FILE_KEY = "credentials_file"

# Subdirectorio de `data_root` donde se escriben los ficheros por lanzamiento.
# Bajo data_root a propósito: es la ruta que host y worker comparten con el
# MISMO nombre, requisito para que el bind del contenedor hermano resuelva.
STAGING_SUBDIR = "run-secrets"


def credential_spec_fields() -> frozenset[str]:
    """Los nombres que una credencial puede tener DENTRO del spec del modelo.

    Se derivan de :data:`shared_llm.credential_fields.CREDENTIAL_FIELDS`, la
    tabla única kind→campos (task_prod07_08), en vez de repetirse aquí: una
    lista copiada es exactamente lo que hizo divergir a las tres copias que esa
    tabla vino a unificar, y aquí el modo de fallo es peor —un campo olvidado no
    da error, deja el secreto en el env y nadie se entera—.
    """
    return frozenset(
        spec_field
        for mapping in CREDENTIAL_FIELDS.values()
        for _, spec_field in mapping.secret_fields
    )


def split_model_credentials(
    model_spec: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Parte el spec del modelo en (parte PÚBLICA, credenciales).

    La parte pública es una copia sin ningún campo de credencial y con el puntero
    ``credentials_file`` puesto **sólo si había algo que mover**: un spec sin
    credencial (ollama local, o el kind ``scripted`` de los tests) sale idéntico,
    sin puntero, y el runtime no busca fichero alguno.

    Nunca muta la entrada. Un valor vacío o no-``str`` NO cuenta como credencial:
    moverlo dejaría al provider sin campo donde antes tenía un ``""``, que es un
    cambio de comportamiento gratuito.
    """
    if not model_spec:
        return model_spec, {}
    fields = credential_spec_fields()
    secrets: dict[str, str] = {}
    public: dict[str, Any] = {}
    for key, value in model_spec.items():
        if key in fields and isinstance(value, str) and value:
            secrets[key] = value
        else:
            public[key] = value
    if secrets:
        public[CREDENTIALS_FILE_KEY] = MODEL_CREDENTIALS_PATH
    return public, secrets


def stage_model_credentials(
    secrets: dict[str, str],
    *,
    base_dir: str,
) -> StagedSecrets:
    """Escribe ``secrets`` como UN fichero JSON y devuelve su mount read-only.

    Un solo fichero y no uno por campo: el runtime necesita el conjunto para
    superponerlo sobre el spec, y N ficheros serían N nombres que mantener
    sincronizados entre worker y runtime sin ganar nada.
    """
    payload = json.dumps(secrets, sort_keys=True)
    return stage_secrets({MODEL_CREDENTIALS_FILENAME: payload}, base_dir=base_dir)


__all__ = [
    "CREDENTIALS_FILE_KEY",
    "MODEL_CREDENTIALS_FILENAME",
    "MODEL_CREDENTIALS_PATH",
    "STAGING_SUBDIR",
    "credential_spec_fields",
    "split_model_credentials",
    "stage_model_credentials",
]
