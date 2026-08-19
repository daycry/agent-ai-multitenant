"""La frontera entre `api_server` y `workers` (prod-15 `task_gov_app_boundary_11`, api-9).

`celery_client.py` lo declara en su primera línea: «api-server runs **no** Celery
tasks — it only *enqueues* them onto the shared broker by name (the `workers`
package owns the implementations) … we never import the `workers` package: that
keeps the app boundary clean».

La frase es falsa desde hace tiempo, y hasta hoy **nada lo vigilaba**: el comando
declarado por el plan (`auto_gov_11_a`) apuntaba a este fichero, que no existía.
Un arnés que nombra un test inexistente es peor que no tener test — sale rojo en
la recolección y no distingue «la frontera está rota» de «el arnés apunta a la
nada».

## Qué vigila esta guarda, y qué NO

**No** exige cero imports: eso sería rojo permanente y el rojo permanente se
ignora. Lo que hace es congelar el inventario. Cada import que hoy existe está
declarado abajo con su motivo y su clasificación, y la guarda se pone roja si
aparece **uno nuevo** o si uno declarado desaparece sin retirar su entrada.

La clasificación importa porque los imports no son la misma falta:

- **`helper`** — importa una FUNCIÓN PURA (`_run_git`, `worktree_coordinates`,
  `sign_review_url`, `DEFAULT_TENANT_SCOPED_TABLES`). Feo por acoplamiento, pero
  importarla no ejecuta trabajo de worker ni arrastra I/O al api-server. La
  salida limpia es mover el helper a `packages/`, no encolar una tarea.
- **`worker-work`** — importa un ADAPTADOR que hace **red síncrona** dentro del
  api-server (boto3 / paramiko / rclone). Ése era el hallazgo api-9 de verdad.

Una allowlist que no distinguiera las dos clases sería deshonesta: daría por
igual de aceptable importar una función pura que ejecutar boto3 en el bucle de
eventos.

## El único `worker-work` está cerrado (2026-08-19)

`routers/backup.py` era el que ejecutaba boto3/paramiko/rclone dentro del
api-server. prod-15 `task_gov_app_boundary_11` lo mudó al worker con dos tareas
encoladas por nombre (`workers.backup_test_destination` /
`workers.backup_list_remote`), como ya hacía el restore en el mismo fichero. No
era sólo acoplamiento: los adaptadores resuelven sus credenciales de
`os.environ` **del proceso que los ejecuta**, y el api-server no declara ninguna
`WORKERS_BACKUP_*` — la sonda daba FAIL en cuanto el destino tenía credencial.
El contrato de los dos endpoints no cambió: el router relaya lo que devuelve el
worker. Ver `tests/unit/test_backup_probe_runs_in_the_worker.py`.

Lo que queda inventariado son cinco `helper`, cuya salida es moverlos a
`packages/`. Esta guarda sigue siendo el suelo que impide que la deuda crezca.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_API_SERVER = _ROOT / "apps" / "api-server" / "src" / "api_server"

#: Inventario congelado a 2026-08-10. Clave: ruta relativa a `api_server/`.
#: Valor: `(clase, motivo)`.
#:
#: `helper`      → función pura; la salida limpia es moverla a `packages/`.
#: `worker-work` → adaptador con I/O de red; la salida es la decisión D5 (tarea
#:                 Celery encolada por nombre). Es el hallazgo api-9 real.
WORKERS_IMPORT_DEBT: dict[str, tuple[str, str]] = {
    "backup_restore.py": (
        "helper",
        "DEFAULT_TENANT_SCOPED_TABLES: una constante con la lista de tablas "
        "tenant-scoped. Duplicarla sería peor que importarla.",
    ),
    "code_diff.py": (
        "helper",
        "_run_git / worktree_coordinates: el visor de diff resuelve rutas de "
        "worktree. La operación de git SÍ se delega al worker (ver el gotcha "
        "fix-code-diff-500-delegar-worker); esto es solo el cálculo de rutas.",
    ),
    "docs_structure/kb_sync.py": (
        "helper",
        "_run_git para leer el árbol del repo del proyecto.",
    ),
    "docs_viewer/service.py": (
        "helper",
        "_run_git para leer ficheros versionados del repo del proyecto.",
    ),
    "routers/review.py": (
        "helper",
        "sign_review_url / verify_review_url: firma HMAC de la URL de revisión. "
        "Import de MÓDULO (no diferido): el api-server carga `workers` al "
        "arrancar. Es el candidato más claro a mudarse a `packages/`.",
    ),
}

#: prod-15 `task_gov_app_boundary_11`, 2026-08-19. `routers/backup.py` era la
#: ÚNICA entrada `worker-work` del inventario —el hallazgo api-9— y ya no está:
#: la sonda de conectividad y el listado remoto se encolan por nombre
#: (`workers.backup_test_destination` / `workers.backup_list_remote`) y corren en
#: el worker, que es donde viven las `WORKERS_BACKUP_*`. Ver
#: `tests/unit/test_backup_probe_runs_in_the_worker.py`.
#:
#: Lo que queda en el inventario son las cinco entradas `helper`: importan
#: funciones puras y su salida limpia es moverlas a `packages/`, no encolar nada.
WORKER_WORK_ENTRIES_EXPECTED: tuple[str, ...] = ()


def _modules_importing_workers() -> dict[str, list[str]]:
    """`{ruta relativa: [símbolos importados]}` por AST, no por grep.

    El AST evita los dos falsos positivos del grep: la palabra `workers` dentro
    de un comentario o de un docstring —y este repositorio los tiene a montones,
    porque explica constantemente por qué NO se importa el paquete.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(_API_SERVER.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - un fichero roto es otro problema
            continue
        symbols: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "workers" or module.startswith("workers."):
                    symbols.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                symbols.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "workers" or alias.name.startswith("workers.")
                )
        if symbols:
            found[path.relative_to(_API_SERVER).as_posix()] = sorted(symbols)
    return found


def test_the_scan_actually_reads_the_api_server() -> None:
    """No-vacuidad: si el descubrimiento deja de encontrar módulos, todo lo de
    abajo pasaría por las razones equivocadas."""
    modules = list(_API_SERVER.rglob("*.py"))
    assert len(modules) >= 300, (
        f"la guarda solo vio {len(modules)} módulos de api_server: ¿cambió la ruta del paquete?"
    )


def test_no_new_api_server_module_imports_workers() -> None:
    """La deuda no crece. Un import nuevo es una decisión, no un descuido."""
    offenders = sorted(set(_modules_importing_workers()) - set(WORKERS_IMPORT_DEBT))

    assert not offenders, (
        f"módulos de api_server que importan `workers` y no están en el "
        f"inventario: {offenders}.\n"
        "El api-server NO importa el paquete de workers: encola por nombre "
        "(`celery_client.send_task`). Si de verdad hace falta el símbolo, "
        "muévelo a `packages/` o encola una tarea; si no hay más remedio, "
        "añádelo a WORKERS_IMPORT_DEBT con su clase y su motivo, y explica el "
        "porqué en el PR."
    )


def test_the_debt_inventory_has_no_dead_entries() -> None:
    """El inventario caduca solo: una entrada que ya no se cumple miente sobre
    una deuda que alguien pagó, y la próxima revisión la busca en vano."""
    actual = set(_modules_importing_workers())
    stale = sorted(set(WORKERS_IMPORT_DEBT) - actual)

    assert not stale, (
        f"entradas del inventario cuya deuda ya no existe: {stale}. "
        "Retíralas de WORKERS_IMPORT_DEBT — si no, la guarda afirma un "
        "incumplimiento que se arregló."
    )


def test_no_module_runs_worker_work_inside_the_api_server_any_more() -> None:
    """El hallazgo api-9, cerrado.

    Era UNO —`routers/backup.py`, que corría boto3/paramiko/rclone dentro del
    api-server— y desde prod-15 `task_gov_app_boundary_11` es NINGUNO: esas dos
    sondas se encolan por nombre y corren en el worker.

    La guarda no se retira al cerrarlo, cambia de umbral: hoy exige CERO. Que
    reaparezca un `worker-work` significa que alguien volvió a ejecutar trabajo
    de worker en el proceso equivocado, y esta vez sin decisión pendiente que lo
    ampare.
    """
    worker_work = tuple(
        sorted(name for name, (kind, _) in WORKERS_IMPORT_DEBT.items() if kind == "worker-work")
    )

    assert worker_work == WORKER_WORK_ENTRIES_EXPECTED, (
        f"volvió a haber módulos que ejecutan trabajo de worker dentro del "
        f"api-server: {list(worker_work)}. La red/los adaptadores corren donde "
        "están sus credenciales — encola una tarea por nombre, como hacen "
        "`workers.backup_test_destination` y `workers.backup_list_remote`."
    )


def test_the_debt_that_remains_is_only_pure_helpers() -> None:
    """No-vacuidad del test de arriba: con el inventario vacío, «cero
    `worker-work`» pasaría por la razón equivocada.

    Las cinco entradas que quedan son `helper` y su salida es moverlas a
    `packages/`, que es otra tarea y otro carril.
    """
    kinds = {kind for kind, _ in WORKERS_IMPORT_DEBT.values()}

    assert len(WORKERS_IMPORT_DEBT) >= 5, (
        f"el inventario bajó a {len(WORKERS_IMPORT_DEBT)} entradas: si de verdad "
        "se saldó más deuda, actualiza este suelo; si el descubrimiento dejó de "
        "encontrar módulos, el test de arriba está pasando en vacío."
    )
    assert kinds == {"helper"}, f"el inventario ya no es sólo de helpers: {sorted(kinds)}"


def test_every_debt_entry_is_classified_and_justified() -> None:
    """Una allowlist sin motivo escrito es un permiso permanente disfrazado."""
    for name, (kind, reason) in WORKERS_IMPORT_DEBT.items():
        assert kind in {"helper", "worker-work"}, f"{name}: clase desconocida `{kind}`"
        assert len(reason) >= 40, (
            f"{name}: el motivo tiene {len(reason)} caracteres. Escribe por qué "
            "este import existe y cuál es su salida, o la próxima revisión "
            "tendrá que reconstruirlo desde cero."
        )


def test_celery_client_still_claims_the_boundary_it_documents() -> None:
    """Si alguien borra la promesa del docstring para "arreglar" la
    contradicción, esta guarda se queda sin norte. Que la promesa siga escrita
    es parte del contrato."""
    raw = (_API_SERVER / "celery_client.py").read_text(encoding="utf-8")
    text = " ".join(raw.split())  # la promesa va partida en dos líneas
    assert "never import the `workers` package" in text, (
        "`celery_client.py` dejó de declarar la frontera. Si la decisión cambió, "
        "cámbiala en un ADR, no borrando la frase que la enunciaba."
    )
