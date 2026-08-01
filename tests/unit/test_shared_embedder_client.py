"""Los hot paths embeben sobre el cliente httpx COMPARTIDO (task_prod13_05, perf-9).

Construir un ``OllamaEmbedder()`` por llamada construye con él un
``httpx.AsyncClient`` nuevo: un handshake TCP por operación y un pool de
conexiones que nace y muere sin reutilizar nada. El singleton de proceso ya
existía (lo estrenó el visor de documentación); lo que faltaba era que lo usaran
los dos llamantes que de verdad están en un camino caliente — el chat de
planificación y el espejo de `docs/` a la KB interna.

Dos niveles, porque prueban cosas distintas:

* **Comportamiento**: el embedder compartido reutiliza el MISMO cliente y su
  ``aclose()`` no lo mata (si lo matara, el primer llamante que cierre el suyo
  dejaría el pool inservible para todos los demás — un fallo peor que el que se
  venía a arreglar).
* **Guarda de código fuente**: que hoy estén cableados no impide que el próximo
  llamante vuelva a escribir ``OllamaEmbedder()`` a pelo. La guarda enumera los
  módulos de camino caliente y falla si aparece uno nuevo sin cablear.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_API_SRC = Path(__file__).resolve().parents[2] / "apps" / "api-server" / "src" / "api_server"

# Módulos en camino CALIENTE: se ejecutan por request/por tarea, así que un
# cliente httpx nuevo en cada pasada se paga en cada pasada. Deliberadamente NO
# incluye los seeds: `seeds/catalog_ingestion.py` crea el embedder una vez por
# PASADA del seed y lo cierra al terminar — sin churn por request, y ahí un
# cliente propio con su ciclo de vida acotado es lo correcto.
_HOT_PATH_MODULES = (
    "chat/responder.py",
    "docs_structure/kb_sync.py",
    "routers/docs_viewer.py",
)


# ---------------------------------------------------------------------------
# Comportamiento
# ---------------------------------------------------------------------------
def test_shared_embedder_reuses_the_process_client() -> None:
    from api_server.ingestion.embed_client import (
        get_shared_embed_client,
        reset_shared_embed_client_cache,
        shared_ollama_embedder,
    )

    reset_shared_embed_client_cache()
    try:
        first = shared_ollama_embedder()
        second = shared_ollama_embedder()
        assert first is not second, "el embedder es barato: no hace falta cachearlo"
        assert first._client is second._client
        assert first._client is get_shared_embed_client()
    finally:
        reset_shared_embed_client_cache()


@pytest.mark.asyncio
async def test_closing_a_shared_embedder_does_not_kill_the_pool() -> None:
    """Un llamante que cierra «su» embedder no puede dejar sin cliente a los demás."""
    from api_server.ingestion.embed_client import (
        get_shared_embed_client,
        reset_shared_embed_client_cache,
        shared_ollama_embedder,
    )

    reset_shared_embed_client_cache()
    try:
        embedder = shared_ollama_embedder()
        await embedder.aclose()
        assert not get_shared_embed_client().is_closed
    finally:
        reset_shared_embed_client_cache()


def test_the_singleton_has_exactly_one_home() -> None:
    """Una sola definición, y por tanto una sola ``lru_cache``.

    El cliente vivía en el router del visor de documentación, que fue su primer
    usuario: desde ahí no lo podía importar ningún servicio sin invertir la
    dependencia, y la salida fácil —redefinirlo donde hiciera falta— serían DOS
    cachés, o sea dos clientes, con el singleton dejando de serlo justo cuando
    alguien crea que lo tiene."""
    homes = [
        path
        for path in _API_SRC.rglob("*.py")
        if "def get_shared_embed_client" in path.read_text(encoding="utf-8")
    ]
    assert [p.name for p in homes] == ["embed_client.py"]


# ---------------------------------------------------------------------------
# Guarda de código fuente
# ---------------------------------------------------------------------------
def _bare_ollama_embedder_calls(path: Path) -> list[int]:
    """Líneas donde se llama a ``OllamaEmbedder(...)`` SIN pasarle ``client=``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "OllamaEmbedder":
            continue
        if any(kw.arg == "client" for kw in node.keywords):
            continue
        offenders.append(node.lineno)
    return offenders


@pytest.mark.parametrize("relpath", _HOT_PATH_MODULES)
def test_hot_paths_do_not_build_their_own_httpx_client(relpath: str) -> None:
    path = _API_SRC / relpath
    assert path.is_file(), f"el módulo {relpath} se movió: actualiza la guarda"
    offenders = _bare_ollama_embedder_calls(path)
    assert not offenders, (
        f"{relpath}: OllamaEmbedder() sin cliente compartido en las líneas "
        f"{offenders} — usa shared_ollama_embedder()"
    )
