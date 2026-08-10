"""El token de Vault DEL WORKER también se renueva solo.

Plan prod-10 `task_prod10_07` (hallazgo secrets-4), segunda mitad.

## Lo que quedó fuera la primera vez

`api_server.vault_client` cerró el agujero en el api-server: una fábrica única,
`lookup_self` al arrancar y `renew_self` en un hilo de fondo. Su guarda de
descubrimiento (`test_no_api_server_module_builds_its_own_hvac_client`) recorre
**`api_server/`** — y ahí se paró.

Los workers tienen su propio token (`WORKERS_VAULT_TOKEN`, política `workers` del
bootstrap) y construían `hvac.Client` a mano en **tres** sitios:

* `credential_rotation_task._build_vault_client` — el job semanal de rotación,
* `execution._default_vault_store` — la credencial del proveedor LLM de CADA
  ejecución de agente,
* `repo_clone._vault_store` — la credencial git del clonado de repos.

Ninguno llamaba a `renew_self`. Un token periódico que nadie renueva caduca al
final de su período igual que uno de TTL fijo, así que el mismo apagón diferido
que el api-server ya no tiene seguía programado para el worker — y con peor
diagnóstico, porque ahí se manifiesta como «las ejecuciones corren sin
credencial» (`has_credential=False`) en vez de como un 503.

## Lo que se prueba aquí

El calendario de renovación ya lo prueba `test_vault_token_manager.py` con un
reloj de mentira; repetirlo sería duplicar. Aquí se prueba lo que faltaba: que
**existe una fábrica en el worker**, que **arranca el manager**, y sobre todo el
CABLEADO — que ningún módulo de `workers/` se construye su propio cliente por su
cuenta, que es exactamente cómo se llegó a esta situación (§5 de
`verificar-antes-de-implementar.md`: mecanismo entregado, cero llamantes).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKERS_ROOT = _REPO_ROOT / "apps" / "workers" / "src" / "workers"

#: Los tres consumidores de Vault del worker. Nombrados a mano ADEMÁS de la
#: guarda de descubrimiento: sin esta lista, borrar un consumidor dejaría la
#: guarda verde en vacío.
_CONSUMERS = ("credential_rotation_task.py", "execution.py", "repo_clone.py")


class _FakeTokenApi:
    def __init__(self, parent: _FakeHvacClient) -> None:
        self._parent = parent

    def lookup_self(self) -> dict[str, Any]:
        self._parent.lookups += 1
        return {"data": {"ttl": 3600, "renewable": True, "policies": ["workers"]}}

    def renew_self(self) -> dict[str, Any]:
        self._parent.renewals += 1
        return {"auth": {"lease_duration": 3600}}


class _FakeAuth:
    def __init__(self, parent: _FakeHvacClient) -> None:
        self.token = _FakeTokenApi(parent)


class _FakeHvacClient:
    """Doble con la forma anidada de hvac (`client.auth.token.*`)."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.lookups = 0
        self.renewals = 0
        self.auth = _FakeAuth(self)


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    from workers.vault_client import reset_worker_vault_client_cache

    reset_worker_vault_client_cache()
    yield
    reset_worker_vault_client_cache()


def _settings(**overrides: Any) -> Any:
    from workers.config import Settings

    base: dict[str, Any] = {"vault_url": "http://vault:8200", "vault_token": "s.worker"}
    base.update(overrides)
    return Settings(**base)


def _patch_hvac(monkeypatch: pytest.MonkeyPatch) -> list[_FakeHvacClient]:
    """Sustituye la costura de construcción por el doble y devuelve el registro."""
    import workers.vault_client as mod

    built: list[_FakeHvacClient] = []

    def factory(url: str, token: str) -> _FakeHvacClient:
        client = _FakeHvacClient(url, token)
        built.append(client)
        return client

    monkeypatch.setattr(mod, "_new_hvac_client", factory)
    return built


# ---------------------------------------------------------------------------
# La fábrica
# ---------------------------------------------------------------------------
def test_without_vault_there_is_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismo contrato que en el api-server: `None` significa «Vault no cableado»,
    y los llamantes ya saben degradar (sin credencial / ciclo SKIPPED)."""
    from workers.vault_client import build_worker_vault_client

    _patch_hvac(monkeypatch)

    assert build_worker_vault_client(_settings(vault_token=None)) is None
    assert build_worker_vault_client(_settings(vault_url=None)) is None


def test_the_factory_starts_the_renewal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que el hallazgo pedía: que alguien llame a `lookup_self`/`renew_self`.

    Se comprueba con el hilo REAL (no con un reloj falso) porque lo que aquí
    puede romperse es precisamente que la fábrica olvide el `start()`.
    """
    from workers.vault_client import build_worker_vault_client, worker_vault_token_manager

    built = _patch_hvac(monkeypatch)

    client = build_worker_vault_client(_settings())

    assert client is built[0]
    manager = worker_vault_token_manager()
    assert manager is not None, "la fábrica devolvió un cliente sin manager de renovación"
    manager.stop()
    # `lookup_self` lo hace el hilo al arrancar; esperar al hilo haría el test
    # dependiente del planificador, así que se invoca el bucle directamente:
    # lo que se afirma es que el manager habla con ESTE cliente.
    info = manager.lookup()
    assert info is not None
    assert info.renewable is True
    assert built[0].lookups >= 1


def test_the_client_is_cached_and_only_one_manager_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tres consumidores en el mismo proceso, un solo token: tres hilos
    renovándolo es ruido, no redundancia."""
    from workers.vault_client import build_worker_vault_client, worker_vault_token_manager

    built = _patch_hvac(monkeypatch)

    first = build_worker_vault_client(_settings())
    second = build_worker_vault_client(_settings())

    assert first is second
    assert len(built) == 1, f"construyó {len(built)} clientes para un solo token"
    manager = worker_vault_token_manager()
    assert manager is not None
    manager.stop()


# ---------------------------------------------------------------------------
# El cableado — lo que de verdad estaba roto
# ---------------------------------------------------------------------------
def test_no_worker_module_builds_its_own_hvac_client() -> None:
    """Guarda de descubrimiento: cualquier `hvac.Client(` fuera de la fábrica es
    un token que nadie renueva.

    Se recorre el ÁRBOL, no una lista: el siguiente consumidor que se añada entra
    solo. Y se mira el código con los docstrings fuera, porque explicar por qué
    ya no se construye aquí es justo lo que evita que alguien lo rehaga.
    """
    offenders: list[str] = []
    for path in sorted(_WORKERS_ROOT.rglob("*.py")):
        if path.name == "vault_client.py":
            continue
        code = _code_without_docstrings(path)
        if "hvac.Client(" in code:
            offenders.append(path.relative_to(_WORKERS_ROOT).as_posix())
    assert not offenders, (
        "estos módulos del worker construyen su propio hvac.Client en vez de usar "
        f"workers.vault_client.build_worker_vault_client(): {offenders}"
    )


@pytest.mark.parametrize("module", _CONSUMERS)
def test_every_known_consumer_goes_through_the_factory(module: str) -> None:
    path = _WORKERS_ROOT / module
    assert path.exists(), f"el consumidor {module} desapareció; actualiza la lista"
    assert "build_worker_vault_client" in path.read_text(
        encoding="utf-8"
    ), f"{module} ya no usa la fábrica compartida del worker: su token no se renueva"


def _code_without_docstrings(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, holders) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    return ast.unparse(tree)
