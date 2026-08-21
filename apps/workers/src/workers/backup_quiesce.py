"""Quiesce de escritores durante la captura del bundle (ADR 0149, opción A).

Qué decide el ADR 0149 y qué añade al firmarlo
----------------------------------------------
El bundle se ensambla contra el stack **vivo**, así que cada artefacto retrata
un instante distinto y algunos retratan un fichero que se está escribiendo. La
opción A del ADR —parar los escritores mientras dura la captura— elimina esa
segunda clase de incoherencia sin exigirle nada al host (que es lo que descarta
la opción B, el snapshot de LVM/ZFS).

Pero A, tal como estaba descrita, tiene un modo de fallo que el ADR no nombraba
y que se añadió **al firmar**: *un quiesce que no termina convierte el backup
nocturno en una caída*. Si un worker no atiende la señal de parada —un run
largo, un contenedor colgado, un `docker compose stop` que espera su timeout—
la ventana de 1-3 minutos se estira, y a las 03:00 no hay nadie mirando.

De ahí el contrato de este módulo, que es literalmente el de la firma:

1. Se pide la parada y se espera un máximo configurable
   (``WORKERS_BACKUP_QUIESCE_TIMEOUT_SECONDS``, 180 s por defecto).
2. Paran a tiempo → captura coherente, y el acta lo registra (``full``).
3. Vence el plazo → **el backup SIGUE ADELANTE** con los escritores que queden
   en pie, y el acta registra ``partial`` con quién no paró.
4. Los servicios rearrancan SIEMPRE, en un ``finally``, aunque la captura falle.

Un backup con skew registrado es mucho mejor que un backup que no existe, y
muchísimo mejor que un stack parado a las 03:00 esperando a un worker que no va
a responder. Por eso **ningún camino de este módulo eleva**: la degradación se
devuelve como dato, no como excepción.

Comprobar, no suponer
---------------------
Tras el ``stop`` se pregunta a compose quién sigue corriendo, en vez de dar por
bueno el rc=0. Un ``stop`` puede volver con éxito habiendo dejado en pie un
contenedor que se reinició solo (``restart: unless-stopped`` está en el compose
de todos los servicios). Y si esa comprobación no se puede hacer, el acta dice
``partial``, no ``full``: «no comprobado» y «coherente» no son lo mismo, y
confundirlos es el verde vacío que el resto del motor evita.

El seam
-------
Se reutiliza el ``CommandRunner`` del motor de backup (subprocesos con argv
explícito, nunca ``shell=True``). Se declara aquí como Protocol estructural para
no importar :mod:`workers.backup` —que a su vez importa este módulo— y romper el
ciclo. Mismo patrón que :mod:`workers.backup_consistency`.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog

_log = structlog.get_logger("workers.backup_quiesce")

#: No había nada que parar (lista de servicios vacía). El backup se comporta
#: exactamente como antes del ADR 0149.
QUIESCE_DISABLED = "disabled"
#: Todos los escritores pedidos pararon dentro del plazo: captura coherente.
QUIESCE_FULL = "full"
#: Alguno no paró (o no se pudo comprobar). El backup sigue adelante y el acta
#: registra el skew a sabiendas — la degradación a la opción C del ADR.
QUIESCE_PARTIAL = "partial"

#: Margen sobre el plazo del operador para que el proceso hijo termine por su
#: cuenta antes de que lo matemos: `docker compose stop --timeout N` manda
#: SIGKILL a los N segundos, y queremos ver su salida, no un TimeoutExpired.
_RUNNER_GRACE_S = 30


class CommandResultLike(Protocol):
    """Lo que este módulo necesita del resultado de un comando externo."""

    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


class CommandRunner(Protocol):
    """El seam de subprocesos del motor de backup (:class:`workers.backup.CommandRunner`)."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResultLike: ...


@dataclass(frozen=True)
class QuiesceRecord:
    """El acta del quiesce: qué se pidió, qué paró y qué no.

    Viaja al ``manifest.json`` del bundle porque es el único sitio donde quien
    restaure meses después puede enterarse de que ESE bundle se capturó con
    escritores en pie — y por tanto de que las divergencias que
    ``restore_reconcile`` reporte son el comportamiento acordado y no una
    incidencia. Un skew que no consta no se puede juzgar.
    """

    mode: str = QUIESCE_DISABLED
    requested: tuple[str, ...] = ()
    still_running: tuple[str, ...] = ()
    duration_s: float = 0.0
    resumed: bool = False
    resume_error: str | None = None
    error: str | None = None

    @classmethod
    def disabled(cls) -> QuiesceRecord:
        return cls()

    @property
    def degraded(self) -> bool:
        """True cuando la captura NO fue coherente y hay que decirlo."""
        return self.mode == QUIESCE_PARTIAL

    def to_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["requested"] = list(self.requested)
        payload["still_running"] = list(self.still_running)
        payload["duration_s"] = round(self.duration_s, 3)
        return payload


@dataclass
class ComposeQuiescer:
    """Para y rearranca los escritores con ``docker compose`` alrededor de la captura.

    ``never_stop`` NO es cosmético: la lane que corre el backup
    (``workers-privileged``, la que drena la cola ``privileged``) está en la lista
    de escritores de PostgreSQL con todo el derecho, y pararla mata este mismo
    proceso a mitad de la captura dejando el resto del stack parado hasta que
    alguien lo note. La lista de servicios la escribe el operador, así que la
    guarda no puede ser «no lo pongas».
    """

    runner: CommandRunner
    project: str
    compose_file: Path
    services: tuple[str, ...] = ()
    timeout_s: int = 180
    never_stop: tuple[str, ...] = ()
    #: Reloj inyectable para que la duración del acta sea determinista en test.
    clock: Callable[[], float] = time.monotonic
    _stopped: tuple[str, ...] = field(default=(), init=False, repr=False)

    # -- API pública ---------------------------------------------------------

    def quiesce(self) -> QuiesceRecord:
        """Pedir la parada y devolver el acta. **Nunca eleva** (punto 3 del ADR)."""
        targets = self._targets()
        if not targets:
            self._stopped = ()
            return QuiesceRecord.disabled()

        missing = self._missing_compose_file()
        if missing is not None:
            # Sin compose no hay quiesce posible. Se degrada igual que si nadie
            # hubiera parado —el backup NO se cae— pero diciendo qué variable
            # arreglar, en vez de dejar tres `rc=1` de docker en el log cada noche.
            self._stopped = ()
            _log.warning("backup.quiesce.no_compose_file", error=missing)
            return QuiesceRecord(mode=QUIESCE_PARTIAL, requested=targets, error=missing)

        self._stopped = targets
        started = self.clock()
        error = self._stop(targets)
        still_running, probe_error = self._still_running(targets)
        duration = self.clock() - started
        error = error or probe_error

        mode = QUIESCE_FULL if (not still_running and error is None) else QUIESCE_PARTIAL
        record = QuiesceRecord(
            mode=mode,
            requested=targets,
            still_running=still_running,
            duration_s=duration,
            error=error,
        )
        if record.degraded:
            _log.warning(
                "backup.quiesce.partial",
                requested=list(targets),
                still_running=list(still_running),
                duration_s=round(duration, 3),
                error=error,
                hint=(
                    "el backup sigue adelante con skew registrado (ADR 0149): un bundle "
                    "con constancia del skew es mejor que un stack parado esperando"
                ),
            )
        else:
            _log.info(
                "backup.quiesce.full",
                requested=list(targets),
                duration_s=round(duration, 3),
            )
        return record

    def resume(self, record: QuiesceRecord) -> QuiesceRecord:
        """Rearrancar lo que se paró. Se llama SIEMPRE, desde un ``finally``.

        ``start`` es el inverso exacto de ``stop`` y no recrea contenedores; si
        falla —porque el contenedor ya no existe— se cae a ``up --detach``, que
        sí lo recrea. Dejar el stack parado porque el rearranque elegante no
        funcionó sería el peor resultado posible de un backup.
        """
        targets = self._stopped
        if not targets:
            return record

        error = self._run_ok([*self._base(), "start", *targets], label="start")
        if error is not None:
            _log.warning("backup.quiesce.start_failed", error=error, fallback="up --detach")
            fallback = self._run_ok([*self._base(), "up", "--detach"], label="up")
            error = None if fallback is None else f"{error}; up --detach: {fallback}"

        self._stopped = ()
        if error is not None:
            _log.error(
                "backup.quiesce.resume_failed",
                services=list(targets),
                error=error,
                hint="el stack puede haber quedado parado: `docker compose up -d` a mano",
            )
            return dataclasses.replace(record, resumed=False, resume_error=error)
        _log.info("backup.quiesce.resumed", services=list(targets))
        return dataclasses.replace(record, resumed=True, resume_error=None)

    # -- internals -----------------------------------------------------------

    def _targets(self) -> tuple[str, ...]:
        never = set(self.never_stop)
        kept = tuple(s for s in self.services if s and s not in never)
        skipped = [s for s in self.services if s in never]
        if skipped:
            _log.warning(
                "backup.quiesce.self_service_skipped",
                skipped=skipped,
                reason=(
                    "esa lane corre ESTE backup: pararla lo mata a mitad de la captura "
                    "y deja el stack parado"
                ),
            )
        return kept

    def _missing_compose_file(self) -> str | None:
        """El compose que se va a pilotar, ¿está donde dice la configuración?

        El instalador **no emite** `WORKERS_RESTORE_COMPOSE_FILE`, y el default
        asume `data_root=/data/agent-platform`. Quien instale con otro data root
        se queda con un puntero a un fichero inexistente, y el síntoma sin esta
        guarda sería un `rc=1` de docker cada madrugada que no nombra la variable.
        """
        try:
            if self.compose_file.is_file():
                return None
        except OSError:  # ruta inválida para el SO
            pass
        return (
            f"el compose {self.compose_file} no existe, así que no se puede parar nada: "
            "apunta WORKERS_RESTORE_COMPOSE_FILE al compose que el instalador escribió "
            "bajo tu data_root (`{data_root}/docker-compose.yml`)"
        )

    def _base(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--file",
            str(self.compose_file),
        ]

    def _stop(self, targets: tuple[str, ...]) -> str | None:
        """``docker compose stop`` acotado. Devuelve el error como TEXTO, no eleva."""
        args = [
            *self._base(),
            "stop",
            f"--timeout={self.timeout_s}",
            *targets,
        ]
        return self._run_ok(args, label="stop", timeout=self.timeout_s + _RUNNER_GRACE_S)

    def _still_running(self, targets: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
        """Quién de ``targets`` sigue en pie según compose.

        Se pregunta en vez de deducirlo del rc: todos los servicios del stack
        llevan ``restart: unless-stopped``, así que un contenedor puede volver
        solo entre el ``stop`` y la captura.
        """
        args = [*self._base(), "ps", "--services", "--status=running"]
        try:
            result = self.runner.run(args, timeout=self.timeout_s + _RUNNER_GRACE_S)
        except Exception as exc:
            return (), f"no se pudo comprobar qué sigue corriendo: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            rc = result.returncode
            return (), f"no se pudo comprobar qué sigue corriendo (rc={rc}): {detail}"
        running = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return tuple(s for s in targets if s in running), None

    def _run_ok(
        self,
        args: list[str],
        *,
        label: str,
        timeout: int | None = None,
    ) -> str | None:
        """Correr un comando y devolver su error como texto. Nunca eleva."""
        cap = timeout if timeout is not None else self.timeout_s
        try:
            result = self.runner.run(args, timeout=cap)
        except Exception as exc:
            return f"`docker compose {label}` no terminó: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return f"`docker compose {label}` devolvió rc={result.returncode}: {detail}"
        return None


__all__ = [
    "QUIESCE_DISABLED",
    "QUIESCE_FULL",
    "QUIESCE_PARTIAL",
    "CommandRunner",
    "ComposeQuiescer",
    "QuiesceRecord",
]
