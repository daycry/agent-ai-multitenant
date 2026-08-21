"""Coherencia de las capturas del bundle de backup (prod-04 task_prod_04_06).

El motor de :mod:`workers.backup` ensambla el bundle contra el stack **vivo**. El
`pg_dump` es consistente consigo mismo por construcción, pero cada `tar` retrata
un instante distinto y algunos retratan un fichero que se está escribiendo. Este
módulo aporta las dos piezas que quitan las capturas en caliente ingenuas:

1. :class:`RedisAofRewriter` — le pide a Redis un AOF **fresco y completo**
   (``BGREWRITEAOF``) y espera a que termine, para que el tar del
   ``appendonlydir`` retrate un base file recién cerrado en vez de un río de
   ficheros acumulados durante días.
2. :func:`tree_fingerprint` — la huella de un árbol de ficheros, con la que el
   motor comprueba que un directorio NO cambió durante su captura (el file
   backend de Vault).

Por qué NO es «BGSAVE + capturar el dump.rdb»
---------------------------------------------
Es lo que pedía el plan, y **medido contra ``redis:7-alpine`` el 2026-07-31
restaura una base vacía**. El compose arranca Redis con ``--appendonly yes``; un
Redis con AOF activado que encuentra un ``dump.rdb`` y ningún ``appendonlydir``
no lee el RDB: registra «Creating AOF base file … on server start» y sirve
``DBSIZE 0``. El bundle habría pasado toda verificación y el restore habría
perdido las sesiones, el broker de Celery y los contadores de rate limit sin un
solo error por ninguna parte.

La forma que sí funciona, medida en el mismo banco: ``BGREWRITEAOF`` → esperar →
tar del ``appendonlydir``. Al restaurar, Redis carga el base y el incr
(«DB loaded from base file … / from incr file …») sin ninguna gimnasia de
configuración, e incluso recupera las escrituras posteriores al rewrite que
quedaron en la cola del incr.

Skew residual (documentado en ``docs/06-runbooks/04-disaster-recovery.md``): la
cola del incr que se escriba DURANTE el tar puede quedar truncada. Redis lo
tolera por diseño — ``aof-load-truncated yes`` (el default) descarta el último
comando incompleto y arranca. Lo que se pierde es, como mucho, los últimos
milisegundos de escrituras: dentro del RPO de 24 h declarado.

El seam
-------
Hablar con Redis no cabe en el ``CommandRunner`` del motor (que es un seam de
subprocesos), así que la operación tiene su propio Protocol
(:class:`PersistenceFlusher`). Producción inyecta :class:`RedisAofRewriter`; los
tests, un doble que cuenta llamadas. Igual que el resto del motor: ningún test
habla con un Redis vivo.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog

_log = structlog.get_logger("workers.backup_consistency")


class PersistenceFlusherError(RuntimeError):
    """El almacén no pudo dejar su estado en disco en forma capturable."""


class PersistenceFlusher(Protocol):
    """Pedirle a un almacén que consolide su estado en disco ANTES del tar.

    Devuelve una descripción corta de lo que hizo, para el log del backup.
    Eleva :class:`PersistenceFlusherError` si no puede garantizarlo — el motor
    prefiere no producir bundle a producir uno que no se puede restaurar.
    """

    def flush(self) -> str: ...


@dataclass
class RedisAofRewriter:
    """``BGREWRITEAOF`` + espera activa a que el rewrite termine.

    ``timeout_s`` acota la espera: un rewrite que no termina es un problema real
    (disco lleno, fork sin memoria), y colgarse hasta el timeout de Celery
    dejaría el backup nocturno sin correr y sin decir por qué.

    Se comprueba ``aof_last_bgrewrite_status`` además de
    ``aof_rewrite_in_progress``: un rewrite puede terminar y haber FALLADO, y en
    ese caso el ``appendonlydir`` que capturaríamos sería el viejo — con el
    agravante de que nadie lo sabría.
    """

    url: str
    timeout_s: int = 300
    poll_interval_s: float = 0.5
    #: Inyectable para los tests; producción usa el cliente sync de redis-py.
    client_factory: Any | None = None

    def flush(self) -> str:
        client = self._client()
        try:
            info = client.info("persistence")
            if int(info.get("aof_enabled", 0)) == 0:
                # Sin AOF, el estado durable de Redis es el RDB: un BGSAVE sí es
                # la operación correcta (y el restore lo cargará, porque no hay
                # AOF que le gane la prioridad).
                return self._bgsave(client)
            return self._bgrewriteaof(client)
        except PersistenceFlusherError:
            raise
        except Exception as exc:  # conexión, permisos, protocolo…
            raise PersistenceFlusherError(
                f"no se pudo consolidar la persistencia de redis en {_sanitize(self.url)}: {exc}"
            ) from exc
        finally:
            self._close(client)

    # -- internals ----------------------------------------------------------

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        from redis import Redis

        return Redis.from_url(self.url, decode_responses=True)

    @staticmethod
    def _close(client: Any) -> None:
        # Cerrar es best-effort: un socket que no se cierra limpio no puede tirar
        # el backup nocturno.
        with contextlib.suppress(Exception):
            client.close()

    def _bgrewriteaof(self, client: Any) -> str:
        before = int(client.info("persistence").get("aof_rewrite_in_progress", 0))
        if before:
            self._wait(client, key="aof_rewrite_in_progress")
        client.bgrewriteaof()
        self._wait(client, key="aof_rewrite_in_progress")
        status = str(client.info("persistence").get("aof_last_bgrewrite_status", "unknown"))
        if status != "ok":
            raise PersistenceFlusherError(
                f"el BGREWRITEAOF de redis terminó con estado {status!r}: el appendonlydir "
                "que se capturaría es el anterior al rewrite"
            )
        _log.info("backup.redis.aof_rewritten", url=_sanitize(self.url))
        return "BGREWRITEAOF"

    def _bgsave(self, client: Any) -> str:
        client.bgsave()
        self._wait(client, key="rdb_bgsave_in_progress")
        status = str(client.info("persistence").get("rdb_last_bgsave_status", "unknown"))
        if status != "ok":
            raise PersistenceFlusherError(
                f"el BGSAVE de redis terminó con estado {status!r}: el dump.rdb que se "
                "capturaría es el anterior"
            )
        _log.info("backup.redis.rdb_saved", url=_sanitize(self.url))
        return "BGSAVE"

    def _wait(self, client: Any, *, key: str) -> None:
        deadline = time.monotonic() + self.timeout_s
        while True:
            if int(client.info("persistence").get(key, 0)) == 0:
                return
            if time.monotonic() >= deadline:
                raise PersistenceFlusherError(
                    f"redis sigue con {key}=1 tras {self.timeout_s}s: el fork de "
                    "persistencia no termina (¿disco lleno, o memoria insuficiente "
                    "para el fork?)"
                )
            time.sleep(self.poll_interval_s)


def _sanitize(url: str) -> str:
    """Ocultar la contraseña de una URL de redis antes de loguearla."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, _, hostpart = rest.partition("@")
    if ":" in creds:
        user, _, _ = creds.partition(":")
        creds = f"{user}:***"
    return f"{scheme}://{creds}@{hostpart}"


#: Trozo de lectura para el hash — memoria acotada aunque el fichero sea grande.
_HASH_CHUNK = 1024 * 1024

#: Una entrada de la huella: (ruta relativa, tamaño, sha256 del contenido).
FingerprintEntry = tuple[str, int, str]


def tree_fingerprint(root: Path) -> tuple[FingerprintEntry, ...]:
    """Huella (ruta relativa, tamaño, SHA-256 del contenido) de cada fichero.

    Sí, lee el contenido, y no por gusto. La primera versión usaba
    ``(tamaño, mtime_ns)`` —O(inodos) en vez de O(bytes)— y **una ejecución real
    de su propia suite la pilló** diciendo «estable» justo después de reescribir
    un fichero: dos escrituras del mismo tamaño dentro de la misma marca de reloj
    no mueven ninguno de los dos campos. Peor que no detectar: detectaba **a
    veces**, según cómo cayera el tick del reloj — al revertirla en un banco de
    pruebas, el mismo caso pasaba unas veces y fallaba otras. Una guarda que es
    una carrera no es una guarda, y el mtime no vale como testigo de escritura
    concurrente.

    El coste está acotado por construcción: esto solo corre sobre los paths que
    el operador declara en ``backup_stable_snapshot_paths``, que es el file
    backend de Vault — un árbol de KBs/MBs, no MinIO. Por eso la lista es
    explícita y corta.

    Un fichero que desaparece entre el ``rglob`` y la lectura se ignora: eso YA
    es un cambio y lo detecta la comparación por el otro lado (la huella de antes
    lo tenía y la de después no).
    """
    entries: list[FingerprintEntry] = []
    for child in sorted(root.rglob("*")):
        if not child.is_file():
            continue
        try:
            size = child.stat().st_size
            digest = hashlib.sha256()
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                    digest.update(chunk)
        except OSError:
            continue
        entries.append((child.relative_to(root).as_posix(), size, digest.hexdigest()))
    return tuple(entries)


def fingerprint_diff(
    before: tuple[FingerprintEntry, ...],
    after: tuple[FingerprintEntry, ...],
) -> list[str]:
    """Las rutas que cambiaron entre dos huellas, para un mensaje accionable.

    Sin esto el error diría «cambió» y el operador no sabría qué proceso mirar.
    """
    before_map = {path: (size, digest) for path, size, digest in before}
    after_map = {path: (size, digest) for path, size, digest in after}
    changed = {
        path
        for path in before_map.keys() | after_map.keys()
        if before_map.get(path) != after_map.get(path)
    }
    return sorted(changed)
