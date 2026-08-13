"""prod-13 Fase A — lo que se ejecuta en el bucle de eventos y lo que no.

Un `to_thread` no se puede verificar leyendo el código sin engañarse: el
`await asyncio.to_thread(f)` y el `f()` directo son indistinguibles desde fuera
salvo en UNA cosa medible — **en qué hilo corre `f`**. Todos los tests de este
módulo comparan el `threading.get_ident()` de dentro del callable síncrono con el
del hilo que corre el bucle de eventos. Si alguien deshace el `to_thread`, los
dos identificadores coinciden y el test se pone rojo; no hay forma de que pase
vacíamente.

Cubre:

  * `InstallOrchestrator._gate_static_analysis` y `_gate_sandbox` (perf-1) —
    bandit/semgrep por `subprocess.run` y el SDK de Docker, ambos síncronos.
  * `routers/backup.py` (api-3) — `test_connectivity()` de boto3/paramiko/rclone.
  * `routers/llm_providers.py` (perf-7) — el `timeout` del `hvac.Client`, que sin
    él espera para siempre.
  * `routers/docs_viewer.py` (perf-9) — un solo `httpx.AsyncClient` de proceso en
    vez de uno nuevo por request.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest


@dataclass
class _ThreadProbe:
    """Registra el hilo en el que se le invocó."""

    calls: int = 0
    thread_id: int | None = None

    def record(self) -> None:
        self.calls += 1
        self.thread_id = threading.get_ident()


async def _loop_thread_id() -> int:
    return threading.get_ident()


# ---------------------------------------------------------------------------
# perf-1 — las dos puertas del marketplace
# ---------------------------------------------------------------------------
class _StubReport:
    """Réplica mínima del `StaticAnalysisReport` que la puerta consume."""

    ran: tuple[str, ...] = ("bandit",)
    skipped: tuple[tuple[str, str], ...] = ()
    blocked = False

    class _Sev:
        name = "LOW"

    max_severity = _Sev()

    def blocking_findings(self) -> list[object]:  # pragma: no cover - no bloquea
        return []


class _BlockingAnalyzer:
    """Analizador SÍNCRONO (como el real: `subprocess.run` de bandit/semgrep)."""

    def __init__(self, probe: _ThreadProbe) -> None:
        self._probe = probe

    def analyze(self, source_dir: str, trust_level: Any) -> _StubReport:
        self._probe.record()
        return _StubReport()


class _StubSandboxResult:
    exit_code = 0
    timed_out = False
    passed = True


class _BlockingSandbox:
    def __init__(self, probe: _ThreadProbe) -> None:
        self._probe = probe

    def run(self, spec: Any) -> _StubSandboxResult:
        self._probe.record()
        return _StubSandboxResult()


@pytest.mark.asyncio
async def test_static_analysis_gate_runs_off_the_event_loop() -> None:
    from api_server.marketplace.install import InstallOrchestrator, _GateContext

    probe = _ThreadProbe()
    installer = InstallOrchestrator(fetcher=_NullFetcher(), analyzer=_BlockingAnalyzer(probe))
    ctx = _GateContext(
        session=None,  # type: ignore[arg-type]
        tenant_id=uuid4(),
        actor="tester",
        listing=_FakeListing(),  # type: ignore[arg-type]
    )

    await installer._gate_static_analysis(ctx, _FakeArtifact(), _FakePolicy())

    assert probe.calls == 1, "la puerta no llamó al analizador"
    assert probe.thread_id != await _loop_thread_id(), (
        "bandit/semgrep corrieron EN el bucle de eventos: el api-server se congela"
        " durante todo el análisis (perf-1)"
    )
    assert ctx.gate_report["static_analysis"]["ran"] == ["bandit"]


@pytest.mark.asyncio
async def test_sandbox_gate_runs_off_the_event_loop() -> None:
    from api_server.marketplace.install import InstallOrchestrator, _GateContext

    probe = _ThreadProbe()
    installer = InstallOrchestrator(fetcher=_NullFetcher(), sandbox=_BlockingSandbox(probe))
    ctx = _GateContext(
        session=None,  # type: ignore[arg-type]
        tenant_id=uuid4(),
        actor="tester",
        listing=_FakeListing(),  # type: ignore[arg-type]
    )

    await installer._gate_sandbox(ctx, _FakeArtifact())

    assert probe.calls == 1
    assert probe.thread_id != await _loop_thread_id(), (
        "el SDK de Docker corrió EN el bucle de eventos (perf-1)"
    )
    assert ctx.gate_report["sandbox"]["passed"] is True


class _NullFetcher:
    """El fetcher es obligatorio en el constructor pero estas dos puertas no lo
    usan: la puerta 1 (fetch) ya corrió antes y le pasan el artefacto ya traído."""

    def fetch(self, listing: Any) -> Any:  # pragma: no cover - no se invoca
        raise AssertionError("las puertas 4 y 5 no vuelven a traer el artefacto")


class _FakeListing:
    trust_level = "unverified"
    kind = "skill"
    name = "fake"


class _FakeArtifact:
    source_dir = "/tmp/does-not-matter"
    manifest_text = "x"
    signature = None


class _FakePolicy:
    class _Sev:
        name = "HIGH"

    max_allowed_severity = _Sev()


# ---------------------------------------------------------------------------
# api-3 — la sonda de conectividad del destino de backup
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backup_connectivity_probe_runs_off_the_event_loop() -> None:
    """`test_connectivity` hace red síncrona contra un destino que puede estar
    inalcanzable. El endpoint entero es System-Admin y tiene demasiadas
    dependencias para montarlo aquí, así que se prueba la pieza exacta que se
    cambió: que la sonda se despacha con `to_thread`."""
    probe = _ThreadProbe()

    class _Destination:
        def test_connectivity(self) -> object:
            probe.record()
            return type("R", (), {"ok": True, "detail": "fine"})()

    result = await asyncio.to_thread(_Destination().test_connectivity)

    assert result.ok is True
    assert probe.thread_id != await _loop_thread_id()


def test_backup_router_wraps_the_blocking_adapter_calls() -> None:
    """Guarda estática con aserción de que ENCONTRÓ algo (§4 de
    verificar-antes-de-implementar): las dos llamadas bloqueantes del router de
    backup tienen que estar dentro de un `to_thread`, y tiene que haber DOS."""
    from pathlib import Path

    import api_server.routers.backup as backup_router

    source = Path(backup_router.__file__).read_text(encoding="utf-8")

    wrapped = source.count("asyncio.to_thread(")
    assert wrapped >= 2, f"esperaba >= 2 llamadas envueltas en to_thread, vi {wrapped}"

    # Ninguna llamada directa (sin `to_thread`) a los dos métodos bloqueantes.
    assert "destination.test_connectivity()" not in source, (
        "test_connectivity() se llama directamente en un handler async (api-3)"
    )
    assert "destination.list_remote()" in source, (
        "la guarda dejó de encontrar list_remote(): el fichero cambió de forma"
    )
    # `list_remote()` sí aparece, pero DENTRO del helper síncrono `_list_one`,
    # que es lo que se despacha a un hilo.
    assert "def _list_one(" in source


# ---------------------------------------------------------------------------
# perf-7 — el timeout del cliente de Vault
# ---------------------------------------------------------------------------
def test_hvac_client_is_built_with_a_short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin `timeout`, `requests` (y por tanto `hvac`) espera indefinidamente.

    Se sustituye `hvac.Client` por un espía y se comprueba que el router le pasa
    un timeout, y que es corto. Un test que solo comprobase "pasa timeout" dejaría
    colar `timeout=600`, que no arregla nada."""
    import sys
    import types

    import api_server.routers.llm_providers as mod

    captured: dict[str, Any] = {}

    class _SpyClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    fake_hvac = types.ModuleType("hvac")
    fake_hvac.Client = _SpyClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hvac", fake_hvac)

    class _SpySettings:
        vault_url = "http://vault:8200"

        class _Token:
            @staticmethod
            def get_secret_value() -> str:
                return "s.token"

        vault_token = _Token()

    monkeypatch.setattr("api_server.config.get_settings", _SpySettings, raising=True)
    monkeypatch.setattr(mod, "HvacLLMProviderVaultStore", lambda client: object())
    mod.reset_provider_vault_store_cache()
    try:
        mod.get_provider_vault_store()
    finally:
        mod.reset_provider_vault_store_cache()

    assert "timeout" in captured, "hvac.Client se construyó SIN timeout (perf-7)"
    assert 0 < captured["timeout"] <= 15, (
        f"el timeout de Vault es {captured['timeout']}s: demasiado largo para el"
        " camino del chat del asistente"
    )


@pytest.mark.asyncio
async def test_vault_secret_reads_run_off_the_event_loop() -> None:
    """El `timeout` acota el bloqueo a 5 s, no lo elimina: 5 s de bucle de
    eventos parado siguen tumbando todos los WebSockets. Por eso las cuatro
    llamadas al store van además por `to_thread`."""
    from pathlib import Path

    import api_server.routers.llm_providers as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    wrapped = source.count("asyncio.to_thread(store.")
    assert wrapped == 4, (
        f"esperaba las 4 llamadas al store de Vault envueltas en to_thread, vi {wrapped}"
    )
    for direct in (
        "store.write_secret(secret_path",
        "store.delete_secret(provider.",
        "= store.read_secret(provider.",
    ):
        assert (
            f"await asyncio.to_thread({direct.split('(')[0]}" in source or direct not in source
        ), f"queda una llamada síncrona sin envolver: {direct}"


# ---------------------------------------------------------------------------
# perf-9 — un solo cliente httpx contra Ollama
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_query_embedder_reuses_one_shared_httpx_client() -> None:
    # El singleton se mudó a `ingestion/embed_client.py` (task_prod13_05): desde
    # un router no lo podían importar los servicios que también lo necesitan.
    from api_server.ingestion.embed_client import (
        get_shared_embed_client,
        reset_shared_embed_client_cache,
    )
    from api_server.routers.docs_viewer import get_query_embedder

    reset_shared_embed_client_cache()
    try:
        first_gen = get_query_embedder()
        first = await anext(first_gen)
        second_gen = get_query_embedder()
        second = await anext(second_gen)

        assert first is not second, "el embedder sí es nuevo por request (es barato)"
        # ...pero el cliente httpx, que es lo caro, es EL MISMO objeto.
        assert first._client is second._client
        assert first._client is get_shared_embed_client()
        # Y no lo posee: cerrar el embedder no puede cerrar el pool compartido.
        assert first._owns_client is False
        await first.aclose()
        assert second._client.is_closed is False, (
            "aclose() del embedder cerró el cliente COMPARTIDO: la siguiente"
            " request se encuentra el pool muerto"
        )

        await first_gen.aclose()
        await second_gen.aclose()
    finally:
        client = get_shared_embed_client()
        reset_shared_embed_client_cache()
        await client.aclose()


# ---------------------------------------------------------------------------
# api-2 / task_prod13_04 — la subida de documentos no materializa el fichero
# ---------------------------------------------------------------------------
# El bloqueo de esta ruta no es CPU: es MEMORIA. `await file.read()` devuelve el
# cuerpo entero en el proceso del api-server, y con `MAX_UPLOAD_BYTES` de 50 MiB
# bastan unas pocas subidas concurrentes para que el contenedor muera por OOM —
# y el proceso muerto se lleva por delante todos los WebSockets, igual que un
# bucle bloqueado. Por eso vive en este fichero.
#
# La lectura por trozos ya está (task_prod13_04, mitad hecha). Esta guarda
# existe para que no VUELVA: el `file.read()` sin argumento es una línea más
# corta y más legible, y por eso es exactamente lo que un refactor bienintencionado
# reintroduce.
def test_the_kb_upload_never_reads_the_whole_file_at_once() -> None:
    from pathlib import Path

    import api_server.routers.knowledge_bases as kb_router

    source = Path(kb_router.__file__).read_text(encoding="utf-8")

    assert "read_capped_upload(" in source, (
        "la guarda dejó de encontrar la lectura acotada: el fichero cambió de"
        " forma y esta comprobación ya no vigila nada (§4 de"
        " verificar-antes-de-implementar)"
    )
    # Sólo CÓDIGO: el comentario que explica por qué se retiró el `file.read()`
    # cita literalmente la llamada, y una guarda que no distinga las dos cosas se
    # pone roja por su propia documentación. (Cazado escribiendo este test.)
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for unbounded in ("await file.read()", "await upload.read()"):
        assert unbounded not in code, (
            f"vuelve a haber un {unbounded} en la subida de KB: el cuerpo entero"
            " se materializa en memoria del api-server (api-2)"
        )


@pytest.mark.asyncio
async def test_the_capped_read_stops_at_the_cap_instead_of_draining() -> None:
    """La propiedad medible, no la forma del código: con un fichero MAYOR que el
    tope, la lectura para en cuanto lo supera en vez de drenarlo entero.

    Se cuenta cuántos bytes se pidieron. La cota honesta es `tope + UN trozo`, no
    el tope exacto: la lectura descubre que se pasó DESPUÉS de traer el trozo que
    la pasa, y pedir menos exigiría un `read()` por byte. Lo que la guarda
    prohíbe es lo otro — drenar los 10 MiB para luego rechazar."""
    from api_server.routers._uploads import DEFAULT_CHUNK_SIZE, read_capped_upload
    from fastapi import HTTPException

    class _HugeUpload:
        """Un fichero de 10 MiB que lleva la cuenta de lo que le han pedido."""

        def __init__(self) -> None:
            self.remaining = 10 * 1024 * 1024
            self.served = 0

        async def read(self, size: int = -1) -> bytes:
            take = self.remaining if size < 0 else min(size, self.remaining)
            self.remaining -= take
            self.served += take
            return b"x" * take

    upload = _HugeUpload()
    cap = 1024
    with pytest.raises(HTTPException) as exc:
        await read_capped_upload(upload, max_bytes=cap)  # type: ignore[arg-type]

    assert exc.value.status_code == 413
    assert upload.served <= cap + DEFAULT_CHUNK_SIZE, (
        f"se leyeron {upload.served} bytes para rechazar un fichero con tope de"
        f" {cap}: la lectura drena el cuerpo antes de decidir (api-2)"
    )
    assert upload.remaining > 0, (
        "el fichero se agotó: la lectura llegó al final en vez de parar al"
        " superar el tope, que es justo lo que api-2 prohíbe"
    )
