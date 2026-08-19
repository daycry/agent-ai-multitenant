"""prod-13 `task_prod13_01` — la mitad de LATENCIA: el análisis fuera del HTTP.

La mitad de EVENT LOOP de esta casilla ya estaba hecha y la mide
`tests/unit/test_no_blocking_calls_in_event_loop.py` **por hilo**: bandit/semgrep
y el SDK de Docker corren bajo `asyncio.to_thread`, así que no congelan el
proceso. Lo que este fichero mide es lo otro, que no es lo mismo: con `to_thread`
el request **sigue durando lo que dura el análisis** — hasta 2×120 s de escáner
(`DEFAULT_SCAN_TIMEOUT_S`) más la prueba de humo del sandbox. Cuatro minutos no
caben en un HTTP aunque no bloqueen a nadie: los tumba el timeout del proxy y el
cliente reintenta encima.

Cómo se mide, y por qué no se puede pasar en vacío:

1. **Con sentinela, no leyendo el código.** Se instala un espía en
   `subprocess.run`/`Popen` y en el constructor del sandbox y se conduce el
   camino asíncrono de instalación ENTERO. Si alguien vuelve a meter el escáner
   en el request, el espía cuenta ≥ 1 y el test se pone rojo. El mismo espía
   sirve de prueba de vida: el test verifica primero que el camino SÍNCRONO
   dispara el espía (`test_el_espia_de_subprocess_ve_el_camino_sincrono`), así
   que un espía que dejara de enganchar no pasaría desapercibido — sería rojo en
   ese test antes de dar un falso verde en el otro.

2. **El contrato productor↔consumidor, por nombre y por cola.** El patrón
   dominante de esta base es «mecanismo entregado, cero llamantes»
   (§5 de verificar-antes-de-implementar), y una task Celery lo padece de forma
   silenciosa: si el productor encola `workers.foo` y el worker registra
   `workers.bar`, el mensaje se queda en el broker para siempre y el endpoint
   devuelve 202 igualmente. Aquí se comparan los dos nombres reales.

3. **La cola tiene quien la drene, en los DOS composes.** El ADR 0083 retiró
   `heavy` y `gpu` por ser colas declaradas sin consumidor —«una isolación que el
   despliegue no entregaba»—. Declarar `marketplace` y no drenarla sería repetir
   exactamente eso, y en producción el compose no es este: lo genera el
   instalador. Así que se comprueban ambos.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import pytest
import yaml

_RAIZ = Path(__file__).resolve().parents[2]
_COMPOSE_DEV = _RAIZ / "docker" / "docker-compose.manuals.yml"

#: La cola dedicada que pide la casilla. Constante local a propósito: si el
#: nombre cambia en el código, este fichero tiene que decirlo, no seguirle.
_COLA = "marketplace"


# ===========================================================================
# 1. El espía de subprocess / sandbox
# ===========================================================================
class _Espia:
    """Cuenta invocaciones y guarda los argumentos de la primera."""

    def __init__(self) -> None:
        self.llamadas: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.llamadas.append((args, kwargs))
        raise AssertionError("esto no debería ejecutarse: el espía sustituye al escáner real")


class _AnalizadorQueLanzaSubprocess:
    """Un analizador cuyo `analyze` hace lo que hace el de verdad: subprocess."""

    def __init__(self, espia: _Espia) -> None:
        self._espia = espia

    def analyze(self, source_dir: str, trust_level: str) -> Any:
        # Exactamente la forma del real (`StaticAnalyzer._exec`): argv + timeout.
        self._espia(["bandit", "-r", source_dir], timeout=120)
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_el_espia_de_subprocess_ve_el_camino_sincrono() -> None:
    """Prueba de vida del espía: el camino SÍNCRONO sí lo dispara.

    Sin este test, el de abajo («el camino asíncrono no lo dispara») podría pasar
    porque el espía no engancha nada — el modo de fallo del §4 de
    verificar-antes-de-implementar: una guarda que no puede fallar no es una
    guarda.
    """
    from api_server.marketplace.install import InstallOrchestrator, _GateContext

    espia = _Espia()
    orquestador = InstallOrchestrator(
        fetcher=_FetcherNulo(), analyzer=_AnalizadorQueLanzaSubprocess(espia)
    )
    ctx = _GateContext(
        session=None,  # type: ignore[arg-type]
        tenant_id=uuid4(),
        actor="user:test",
        listing=_ListingFalso(),
    )
    with pytest.raises(AssertionError):
        await orquestador._gate_static_analysis(ctx, _ArtefactoFalso(), _PoliticaFalsa())

    assert len(espia.llamadas) == 1, (
        "el espía no vio el subprocess del camino síncrono: ha dejado de enganchar "
        "y cualquier aserción negativa basada en él sería un falso verde"
    )


@pytest.mark.asyncio
async def test_el_camino_asincrono_no_ejecuta_ni_un_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El servicio que devuelve el 202 no escanea: encola.

    Es la aserción central de la casilla, y se mide sobre el módulo `subprocess`
    ENTERO, no sobre un colaborador inyectado: así queda cubierto cualquier
    camino que alguien añada mañana dentro de la llamada, no sólo el analizador
    que este test conoce.
    """
    import subprocess

    from api_server.marketplace import async_gates

    espia = _Espia()
    monkeypatch.setattr(subprocess, "run", espia)
    monkeypatch.setattr(subprocess, "Popen", espia)
    monkeypatch.setattr(subprocess, "check_output", espia)

    encolados: list[tuple[str, str]] = []
    programados: list[Any] = []

    async def _encolar_falso(*, installation_id: Any, tenant_id: Any) -> bool:
        encolados.append((str(installation_id), str(tenant_id)))
        return True

    def _programar_falso(session: Any, factory: Any) -> None:
        programados.append(factory)

    instalacion = _InstalacionFalsa()
    await async_gates.queue_install_gates(
        _SesionFalsa(),
        installation=instalacion,  # type: ignore[arg-type]
        listing=_ListingFalso(),  # type: ignore[arg-type]
        actor="user:test",
        requested_permissions=[],
        enqueue=_encolar_falso,
        schedule=_programar_falso,
    )

    assert not espia.llamadas, (
        f"el camino asíncrono ejecutó {len(espia.llamadas)} subprocess DENTRO del "
        "request: el análisis volvió al HTTP y el 202 es una mentira"
    )
    assert instalacion.status == "analyzing", (
        "la instalación no quedó en el estado transitorio que el cliente va a "
        f"consultar (quedó en {instalacion.status!r})"
    )
    # El publish va DIFERIDO al post-commit, no inline: el worker busca la fila
    # por `id` y publicar antes de que sea durable es la carrera que documenta
    # `schedule_after_commit` (el consumidor llega primero y se la salta en
    # silencio). Así que aquí todavía no se ha encolado nada...
    assert not encolados, (
        "se encoló ANTES del commit: el worker puede leer la fila que aún no existe"
    )
    assert len(programados) == 1, (
        f"el 202 no programó el encolado post-commit (programó {len(programados)}): "
        "el mecanismo existe y no lo llama nadie (§5 de "
        "verificar-antes-de-implementar)"
    )
    # ...y al correr el callback que `open_tenant_session` dispara tras comitear, sí.
    await programados[0]()
    assert encolados == [(str(instalacion.id), str(instalacion.tenant_id))], encolados


# ===========================================================================
# 2. El contrato productor ↔ consumidor
# ===========================================================================
def test_el_nombre_de_la_task_del_productor_es_el_que_registra_el_worker() -> None:
    """Un nombre distinto en cada lado = mensaje huérfano y 202 mentiroso."""
    from api_server import celery_client
    from workers import marketplace_gates

    assert celery_client.MARKETPLACE_GATES_TASK == marketplace_gates.TASK_NAME, (
        "el productor encola un nombre que ningún worker registra"
    )
    assert celery_client.MARKETPLACE_GATES_QUEUE == marketplace_gates.QUEUE == _COLA, (
        "productor y consumidor no coinciden en la cola"
    )

    # Y el nombre declarado es de verdad el que Celery tiene registrado.
    registrado = marketplace_gates.run_install_gates.name
    assert registrado == marketplace_gates.TASK_NAME, (
        f"el decorador registró {registrado!r} y el módulo anuncia {marketplace_gates.TASK_NAME!r}"
    )


def test_la_cola_esta_en_la_topologia_y_el_worker_la_importa() -> None:
    """Una cola que Celery no conoce, o un módulo que el worker no importa."""
    from workers.celery_app import QUEUE_NAMES, build_celery_app
    from workers.config import Settings

    assert _COLA in QUEUE_NAMES, f"{_COLA!r} no está en la topología de colas"

    app = build_celery_app(
        Settings(
            broker_url="redis://localhost:6379/1",
            result_backend="redis://localhost:6379/2",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )
    )
    declaradas = {q.name for q in app.conf.task_queues}
    assert _COLA in declaradas, f"{_COLA!r} no llega a `task_queues`"
    assert "workers.marketplace_gates" in app.conf.imports, (
        "el worker no importa el módulo de la task: arrancaría sin registrarla y "
        "los mensajes de la cola morirían con NotRegistered"
    )


# ===========================================================================
# 3. La cola tiene consumidor en LOS DOS composes
# ===========================================================================
def _colas_drenadas(comando: Any) -> set[str]:
    """Las colas del `--queues=a,b,c` de un comando de celery (lista o str)."""
    texto = " ".join(comando) if isinstance(comando, list) else str(comando or "")
    if "celery" not in texto or " worker" not in texto:
        return set()
    m = re.search(r"--queues[=\s]+([A-Za-z0-9_,.\-]+)", texto)
    return {q.strip() for q in m.group(1).split(",") if q.strip()} if m else set()


def test_la_cola_tiene_consumidor_en_el_compose_de_dev() -> None:
    doc = yaml.safe_load(_COMPOSE_DEV.read_text(encoding="utf-8"))
    servicios = doc["services"]
    cobertura = {
        nombre: _colas_drenadas(svc.get("command"))
        for nombre, svc in servicios.items()
        if _colas_drenadas(svc.get("command"))
    }
    assert len(cobertura) >= 3, (
        "el descubrimiento de pools de celery dejó de encontrar servicios "
        f"(vio {len(cobertura)}): la guarda pasaría en vacío"
    )
    drenan = {n for n, colas in cobertura.items() if _COLA in colas}
    assert drenan, (
        f"ningún servicio de {_COMPOSE_DEV.name} drena {_COLA!r} — es la cola "
        "muerta que el ADR 0083 retiró para heavy/gpu, otra vez"
    )


def test_la_cola_tiene_consumidor_en_el_compose_generado_por_el_instalador() -> None:
    """En producción el compose no es el de dev: lo genera el instalador."""
    from installer_backend.compose_generator import generate_compose

    doc = generate_compose(_config_instalador())
    cobertura = {
        nombre: _colas_drenadas(svc.get("command"))
        for nombre, svc in doc["services"].items()
        if _colas_drenadas(svc.get("command"))
    }
    # El compose generado tiene MENOS pools que el de dev (no lleva el auxiliar
    # de `test,review`): dos antes de esta casilla, tres con la lane nueva. El
    # suelo es 2 para que la guarda siga siendo «encontró algo» y no un número
    # inventado.
    assert len(cobertura) >= 2, (
        f"el descubrimiento dejó de encontrar pools en el compose generado (vio {len(cobertura)})"
    )
    drenan = {n for n, colas in cobertura.items() if _COLA in colas}
    assert drenan, (
        f"el compose de PRODUCCIÓN no drena {_COLA!r}: la cola existiría declarada "
        "y sin consumidor exactamente donde importa"
    )


def _config_instalador() -> Any:
    """La config mínima que `generate_compose` necesita, tal cual la usan sus tests."""
    from tests.unit.test_compose_generator import _config  # type: ignore[attr-defined]

    return _config()


# ===========================================================================
# Dobles mínimos
# ===========================================================================
class _FetcherNulo:
    def fetch(self, listing: Any) -> Any:
        raise AssertionError("el fetch no se usa en estos tests")


class _ArtefactoFalso:
    source_dir = "/tmp/no-existe"
    manifest_text = ""
    signature = None


class _PoliticaFalsa:
    class _Sev:
        name = "LOW"

    max_allowed_severity = _Sev()


class _ListingFalso:
    id = uuid4()
    kind = "tool"
    name = "falso"
    version = "1.0.0"
    trust_level = "community"
    manifest: ClassVar[dict[str, Any]] = {}


class _InstalacionFalsa:
    def __init__(self) -> None:
        self.id = uuid4()
        self.tenant_id = uuid4()
        self.listing_id = _ListingFalso.id
        self.project_id = None
        self.version = "1.0.0"
        self.status = "enabled"
        self.granted_permissions: list[Any] = []
        self.denied_permissions: list[Any] = []


class _SesionFalsa:
    """Lo mínimo que `queue_install_gates` toca de una sesión."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes = 0
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1
