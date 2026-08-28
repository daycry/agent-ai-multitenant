"""El token de Vault se renueva solo, o se entera todo el mundo.

Plan prod-10 `task_prod10_07` (hallazgo secrets-4).

## La avería que estaba programada

`routers/llm_providers.py` construye un `hvac.Client` UNA vez con el token de
`API_SERVER_VAULT_TOKEN` y lo cachea en un singleton de módulo. `routers/mcp.py`
hace lo mismo. Buscado en todo el repositorio el 2026-07-31: **cero llamadas a
`renew_self` o `lookup_self`**.

Un service token de Vault tiene TTL (32 días por defecto). El día que caduque,
todas las credenciales de proveedor LLM y toda resolución de `auth_ref` de MCP
dejan de resolverse a la vez, sin un cambio de configuración que lo explique y
sin nada en los logs que apunte a Vault. Una avería con fecha, y la peor clase:
la que ocurre un mes después del despliegue que la causó.

## Lo que se prueba

Con un cliente de mentira y un reloj de mentira, porque lo que hay que verificar
es el CALENDARIO de renovaciones, no que hvac funcione:

* renueva ANTES de la mitad del TTL (no en el minuto 31 del día 32);
* si la renovación falla, **no muere el hilo** — reintenta, y lo dice con nivel
  error, porque un `renew_self` que se rompe en silencio reproduce exactamente
  el problema que este manager existe para evitar (riesgo 6 del plan);
* un token NO renovable (o de root, que no caduca) no entra en bucle;
* la métrica `vault_token_ttl_seconds` refleja el TTL vivo, que es lo que la
  alerta de prod-08 va a mirar.

El manager es de hilo, no `asyncio`: `hvac` va sobre `requests` (síncrono) y las
dependencias de FastAPI que lo construyen son funciones `def`, así que no hay
bucle de eventos donde agarrarse — y si lo hubiera, una llamada bloqueante
dentro de él es el hallazgo perf-7 otra vez.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from api_server.vault_client import (
    VaultTokenManager,
    vault_token_ttl_gauge,
)
from prometheus_client import CollectorRegistry

pytestmark = pytest.mark.unit


class FakeTokenClient:
    """Doble del cliente de Vault. Modela TTL, renovabilidad y fallos."""

    def __init__(
        self,
        *,
        ttl: int = 3600,
        renewable: bool = True,
        fail_renew_times: int = 0,
        policies: tuple[str, ...] = ("api-server",),
    ) -> None:
        self.ttl = ttl
        self.renewable = renewable
        self.fail_renew_times = fail_renew_times
        self.policies = policies
        self.lookups = 0
        self.renewals = 0

    def lookup_self(self) -> dict[str, Any]:
        self.lookups += 1
        return {
            "data": {
                "ttl": self.ttl,
                "renewable": self.renewable,
                "policies": list(self.policies),
            }
        }

    def renew_self(self) -> dict[str, Any]:
        self.renewals += 1
        if self.renewals <= self.fail_renew_times:
            raise RuntimeError("vault says no")
        return {"auth": {"lease_duration": self.ttl, "renewable": self.renewable}}


class FakeClock:
    """Reloj de mentira que corta el bucle tras N esperas.

    `StopIteration` sale del generador de `sleep`, así que `_loop` termina y el
    test puede afirmar sobre lo ocurrido en vez de esperar horas reales.
    """

    def __init__(self, max_sleeps: int) -> None:
        self.slept: list[float] = []
        self.max_sleeps = max_sleeps

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        if len(self.slept) >= self.max_sleeps:
            raise _StopLoop


class _StopLoop(BaseException):
    """Señal de parada del reloj de mentira. Hereda de BaseException para que un
    `except Exception` del código bajo prueba no se la trague."""


def _drain(manager: VaultTokenManager) -> None:
    """Corre el bucle hasta que el reloj de mentira lo corta.

    `run_forever` no atrapa `_StopLoop` a propósito: si lo hiciera, el manager
    estaría tragándose una `BaseException` del propio `sleep`, y este test dejaría
    de distinguir «el bucle terminó» de «el bucle se comió una señal de parada».
    """
    with contextlib.suppress(_StopLoop):
        manager.run_forever()


def _manager(client: Any, clock: FakeClock, **kwargs: Any) -> VaultTokenManager:
    return VaultTokenManager(
        client,
        sleep=clock,
        registry=CollectorRegistry(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# lookup al arrancar
# ---------------------------------------------------------------------------
def test_lookup_self_reports_the_ttl() -> None:
    client = FakeTokenClient(ttl=86_400)
    manager = _manager(client, FakeClock(max_sleeps=1))

    info = manager.lookup()

    assert info is not None
    assert info.ttl_seconds == 86_400
    assert info.renewable is True
    assert client.lookups == 1


def test_lookup_survives_a_broken_vault() -> None:
    """Un Vault sellado o caído no puede tumbar el arranque del api-server: la
    plataforma funciona sin Vault (las escrituras devuelven 503 y ya)."""

    class Broken:
        def lookup_self(self) -> dict[str, Any]:
            raise RuntimeError("connection refused")

    manager = _manager(Broken(), FakeClock(max_sleeps=1))
    assert manager.lookup() is None


# ---------------------------------------------------------------------------
# el calendario de renovación
# ---------------------------------------------------------------------------
def test_renews_before_half_the_ttl() -> None:
    """El margen es la mitad del TTL: renovar en el 90% deja una ventana de
    minutos si Vault está momentáneamente inalcanzable."""
    client = FakeTokenClient(ttl=3600)
    clock = FakeClock(max_sleeps=3)
    manager = _manager(client, clock)

    _drain(manager)

    assert clock.slept, "no llegó a dormir: el bucle no arrancó"
    assert all(s <= 1800 for s in clock.slept), f"durmió demasiado: {clock.slept}"
    assert client.renewals >= 2, f"renovó {client.renewals} veces en 3 ciclos"


def test_a_failed_renewal_does_not_kill_the_loop() -> None:
    """Riesgo 6 del plan: «un fallo silencioso en renew_self reproduce
    exactamente el problema que se pretende arreglar». El hilo tiene que
    sobrevivir al fallo y volver a intentarlo."""
    client = FakeTokenClient(ttl=600, fail_renew_times=1)
    clock = FakeClock(max_sleeps=3)
    manager = _manager(client, clock)

    _drain(manager)

    assert client.renewals >= 2, "se rindió tras el primer fallo"


def test_a_failed_renewal_is_logged_as_an_error() -> None:
    """Y tiene que dejar rastro. Se usa un logger falso en vez de `caplog`: la
    app hace `logging.disable`, y afirmar sobre caplog aquí es frágil (gotcha
    `caplog-y-orden-de-tests`)."""
    seen: list[tuple[str, str]] = []

    class FakeLogger:
        def info(self, event: str, **kw: Any) -> None:
            seen.append(("info", event))

        def warning(self, event: str, **kw: Any) -> None:
            seen.append(("warning", event))

        def error(self, event: str, **kw: Any) -> None:
            seen.append(("error", event))

    client = FakeTokenClient(ttl=600, fail_renew_times=5)
    manager = _manager(client, FakeClock(max_sleeps=2), logger=FakeLogger())

    _drain(manager)

    assert any(level == "error" for level, _ in seen), f"no logueó el fallo: {seen}"


def test_a_non_renewable_token_does_not_spin() -> None:
    """Un token de root (o cualquiera no renovable) no se puede renovar. Entrar
    en bucle contra Vault sería un martilleo inútil cada media hora."""
    client = FakeTokenClient(ttl=0, renewable=False)
    clock = FakeClock(max_sleeps=5)
    manager = _manager(client, clock)

    _drain(manager)

    assert client.renewals == 0
    assert clock.slept == [], f"se puso a dormir por un token no renovable: {clock.slept}"


def test_start_is_idempotent() -> None:
    """`build_vault_client` puede llamarse desde dos routers; dos hilos
    renovando el mismo token es ruido, no redundancia."""
    client = FakeTokenClient(ttl=3600)
    manager = _manager(client, FakeClock(max_sleeps=1))

    first = manager.start()
    second = manager.start()

    assert first is second
    manager.stop()


# ---------------------------------------------------------------------------
# la métrica que consume la alerta de prod-08
# ---------------------------------------------------------------------------
def test_the_ttl_gauge_tracks_the_live_ttl() -> None:
    registry = CollectorRegistry()
    client = FakeTokenClient(ttl=7200)
    manager = VaultTokenManager(client, sleep=FakeClock(max_sleeps=1), registry=registry)

    manager.lookup()

    value = registry.get_sample_value("agentic_vault_token_ttl_seconds")
    assert value == 7200.0, f"la métrica no refleja el TTL (vio {value})"


# ---------------------------------------------------------------------------
# El cableado — «mecanismo entregado, cero llamantes» (§5)
# ---------------------------------------------------------------------------
#: Los ÚNICOS sitios que pueden construir un `hvac.Client` por su cuenta, con el
#: motivo por el que la renovación en segundo plano no les aplica. Es la misma
#: forma de inventario congelado que usa `tests/unit/test_app_boundaries.py`: no
#: relaja la guarda, la hace específica —un `hvac.Client(` NUEVO en cualquier
#: otro fichero sigue poniéndola roja, y una excepción que deje de necesitarse
#: también, porque se comprueba que sigue siendo cierta.
_HVAC_CLIENT_EXEMPTIONS: dict[str, str] = {
    "vault_client.py": (
        "LA fábrica. Es quien construye el cliente de vida larga y quien renueva "
        "su token en un hilo de fondo."
    ),
    "bootstrap/hvac_client.py": (
        "El one-shot de finalización (ADR 0161, paso 8). No cabe en la fábrica y "
        "no le aplica el problema que la fábrica resuelve, por tres razones: "
        "(1) es un proceso EFÍMERO que corre segundos y sale, así que no hay "
        "token que caduque un mes después — el hilo de renovación no tendría a "
        "quién renovar; (2) el token que usa es el ROOT que él mismo acaba de "
        "acuñar con `operator init`, o el que le pasa el operador para esa única "
        "pasada: ninguno de los dos está en `settings.vault_token`; (3) "
        "`build_vault_client()` devuelve None cuando Vault no está cableado, que "
        "es exactamente el estado que este módulo existe para cambiar."
    ),
}


def test_no_api_server_module_builds_its_own_hvac_client() -> None:
    """Un manager que renueva el token de UN cliente no sirve de nada si otro
    módulo se construye el suyo por su cuenta: ese segundo token caducaría igual.

    Guarda de descubrimiento sobre el árbol: cualquier `hvac.Client(` fuera de
    :data:`_HVAC_CLIENT_EXEMPTIONS` es un consumidor que se quedó fuera de la
    renovación.
    """
    import api_server

    root = Path(next(iter(api_server.__path__)))
    builders = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "hvac.Client(" in path.read_text(encoding="utf-8")
    }

    offenders = sorted(builders - set(_HVAC_CLIENT_EXEMPTIONS))
    assert not offenders, (
        "estos módulos construyen su propio hvac.Client en vez de usar "
        f"api_server.vault_client.build_vault_client(): {offenders}"
    )

    # Y al revés: una excepción que ya no construye ningún cliente es una
    # excepción caducada, y una lista de excepciones caducadas es como esta
    # guarda se vuelve decorativa.
    stale = sorted(set(_HVAC_CLIENT_EXEMPTIONS) - builders)
    assert not stale, (
        "estas excepciones ya no construyen un hvac.Client; retíralas de "
        f"_HVAC_CLIENT_EXEMPTIONS: {stale}"
    )


@pytest.mark.parametrize("module", ["routers/llm_providers.py", "routers/mcp.py"])
def test_the_two_vault_consumers_go_through_the_factory(module: str) -> None:
    """Nombrados explícitamente: sin esto, borrar el consumidor dejaría el test
    de arriba verde en vacío."""
    import api_server

    source = (Path(next(iter(api_server.__path__))) / module).read_text(encoding="utf-8")
    assert "build_vault_client" in source, f"{module} ya no usa la fábrica compartida"


def test_the_gauge_is_registered_idempotently() -> None:
    """`prometheus_client` prohíbe registrar dos veces el mismo nombre y el
    registro por defecto es global: construir la app dos veces —cosa que hace
    cualquier suite con más de un módulo de integración— reventaría."""
    registry = CollectorRegistry()
    first = vault_token_ttl_gauge(registry)
    second = vault_token_ttl_gauge(registry)
    assert first is second
