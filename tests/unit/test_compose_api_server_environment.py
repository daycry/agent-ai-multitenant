"""Todo contenedor que ejecuta código de la api-server recibe `API_SERVER_ENVIRONMENT`.

## El fail-open que sobrevivió al endurecimiento de prod-09

`workers`, `orchestrator` y `notification-dispatcher` importan y ejecutan módulos
de `api_server.*` (el worker mintea el token interno del sandbox con
`api_server.auth.internal_agent.mint_agent_token`, la ingesta de KB usa las
factorías de MinIO/docling/ClamAV/Ollama de la api-server, el córtex del worker
llama a `api_server.config.get_settings()`, el orchestrator importa medio
`api_server` en `dispatch.py`). Ese código lee sus `Settings` del entorno con
prefijo **`API_SERVER_`**, no con el del servicio anfitrión.

`API_SERVER_ENVIRONMENT` no estaba en ninguno de esos contenedores. Verificado el
2026-07-30 sobre el stack vivo:

```
$ docker exec agentic-platform-workers-1 python -c \
    "from api_server.config import get_settings; print(get_settings().environment)"
dev
```

Consecuencia: `api_server.config.Settings` se construye ahí creyéndose en `dev`
aunque el despliegue sea de producción, y con ello se apagan **dentro de esos
contenedores** las tres guardas de arranque de la api-server:

1. el rechazo de los defaults de dev (`_forbid_dev_secrets_outside_dev`) sobre las
   claves que ESOS procesos sí usan — `minio_access_key` / `minio_secret_key` de
   la ingesta, `admin_database_url`, `internal_token_secret`;
2. el suelo de longitud de los secretos HMAC que firman bearers;
3. la guarda de que `internal_token_secret` DIFIERE de `jwt_secret`.

O sea: cuatro negativas de arranque se convierten en fallos silenciosos en tiempo
de ejecución. (Verificado también que NO es una escalada de privilegio: la
api-server, que es quien VERIFICA los secretos compartidos, sí tiene su guarda
activa y no arranca con un default, así que un secreto de dev en el worker produce
tokens rechazados, no tokens aceptados.)

## Lo que esta guarda cubre y lo que no

Cubre los composes del repositorio. **No** cubre el compose que genera el
instalador (`installer_backend.compose_generator._workers_env` y hermanos), que
tampoco emite la variable: ese fichero está fuera de este carril y queda
reportado. Un test rojo permanente sería peor que ninguno
(verificar-antes-de-implementar §4, corolario).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_DIR = _REPO_ROOT / "docker"

#: Los prefijos de los servicios que ejecutan código de `api_server.*` sin SER la
#: api-server. La api-server queda fuera por construcción: su propio prefijo ES
#: `API_SERVER_`.
_HOSTING_PREFIXES = ("WORKERS", "ORCHESTRATOR", "NOTIFY")

_ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)_ENVIRONMENT:\s*(\S+)\s*$", re.MULTILINE)
_API_ENV_LINE = re.compile(r"^\s*API_SERVER_ENVIRONMENT:\s*(\S+)\s*$", re.MULTILINE)


def _compose_files() -> list[Path]:
    return sorted(_COMPOSE_DIR.glob("docker-compose*.yml"))


def _declarations(text: str) -> list[tuple[str, str]]:
    """``[(PREFIX, value)]`` de cada línea ``<PREFIX>_ENVIRONMENT: <valor>``."""
    return [(m.group(1), m.group(2)) for m in _ENV_LINE.finditer(text)]


def test_the_guard_finds_the_compose_files_and_the_declarations() -> None:
    """Aserción de descubrimiento: sin ella, un renombrado la deja pasando en vacío."""
    files = _compose_files()
    assert len(files) >= 4, f"la guarda dejó de encontrar los composes (vio {files})"

    hosting = [
        (f.name, prefix)
        for f in files
        for prefix, _ in _declarations(f.read_text(encoding="utf-8"))
        if prefix in _HOSTING_PREFIXES
    ]
    assert len(hosting) >= 6, (
        "la guarda dejó de encontrar los servicios que hospedan código de la "
        f"api-server (vio {hosting})"
    )


def test_every_hosting_service_declares_api_server_environment() -> None:
    """Por CADA `<PREFIX>_ENVIRONMENT` de un servicio anfitrión debe haber un
    `API_SERVER_ENVIRONMENT` en el mismo fichero, y con el mismo valor."""
    offenders: list[str] = []
    for path in _compose_files():
        text = path.read_text(encoding="utf-8")
        hosting_values = [v for prefix, v in _declarations(text) if prefix in _HOSTING_PREFIXES]
        if not hosting_values:
            continue
        api_values = _API_ENV_LINE.findall(text)
        if len(api_values) < len(hosting_values):
            offenders.append(
                f"{path.name}: {len(hosting_values)} servicios que ejecutan código "
                f"de api_server pero solo {len(api_values)} API_SERVER_ENVIRONMENT"
            )
            continue
        mismatched = sorted(set(api_values) - set(hosting_values))
        if mismatched:
            offenders.append(
                f"{path.name}: API_SERVER_ENVIRONMENT={mismatched} no coincide con "
                f"el entorno de los servicios ({sorted(set(hosting_values))})"
            )

    assert not offenders, (
        "contenedores que ejecutan código de `api_server.*` sin recibir "
        f"API_SERVER_ENVIRONMENT (o con un valor distinto): {offenders}. Sin esa "
        "variable, `api_server.config` se cree en dev dentro de ese contenedor y "
        "sus guards anti-defaults no disparan."
    )
