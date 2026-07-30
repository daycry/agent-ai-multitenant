"""El `/tmp` del test/stack-runtime deja de ser un literal de 64m (F3).

Follow-up **F3** de `registry-egress-followups.md`. `_build_test_kwargs` montaba
`/tmp` como un tmpfs de **64m escrito a mano**, y por ahí pasan `composer install`
y `npm ci`: descargan y extraen ahí. Composer ya avisaba *«less than 100MiB of
free space»*; guzzle (+8 deps) cupo de milagro, pero un stack con un árbol de
dependencias grande se queda sin sitio en frío.

El detalle que lo delata: la entrega de HOME (task_wf_20, C-01) añadió
`test_runtime_home_size` —configurable, 512m— **en la línea de al lado** y dejó
`/tmp` en el literal. Dos montajes hermanos, uno tunable y el otro no.

Se elige la opción (c) del follow-up —**tamaño configurable**— y no la (a) subir
el literal, porque el tamaño correcto depende del stack: un `generic-shell` no
necesita lo mismo que un monorepo de node. Y no la (b) apuntar el tmp al
worktree, porque eso reintroduce C-01: el `git add -A` de `commit_task` acabaría
comiteando los temporales de la toolchain.

**El invariante que evita cambiar un fallo por otro peor:** las páginas de un
tmpfs se cargan al cgroup de memoria del contenedor. Un `/tmp` de 512m dentro de
un template con `memory_mb=1024` convierte un ENOSPC legible en un OOM-kill mudo,
que desde los logs del agente es mucho más difícil de diagnosticar. Por eso el
último test cruza el tamaño con el límite de memoria de TODO el catálogo.
"""

from __future__ import annotations

from typing import Any

import pytest
from shared_test_runtimes import catalog
from workers import test_runtime
from workers.config import Settings
from workers.test_runtime import RuntimePlan

pytestmark = pytest.mark.unit


def _kwargs(
    settings: Settings | None = None, *, template_id: str = "php-phpunit"
) -> dict[str, Any]:
    # Por módulo, no importados: pytest intentaría recoger `TestRuntimeRunner` /
    # `TestRuntimeSpec` como clases de test por su prefijo `Test`.
    cfg = settings or Settings()
    runner = test_runtime.TestRuntimeRunner(cfg)
    spec = test_runtime.TestRuntimeSpec(
        plan=RuntimePlan(template=catalog.get(template_id), checks=()),
        worktree_host_path="/data/worktrees/t1",
        dep_cache_host_path=None,
        main_env={},
    )
    return runner._build_test_kwargs(spec, "bridge-test")


def _tmp_options(settings: Settings | None = None, **kw: Any) -> str:
    return str(_kwargs(settings, **kw)["tmpfs"]["/tmp"])


def _size_mb(options: str) -> int:
    """Extrae los megas del `size=` de una cadena de opciones de tmpfs."""
    raw = next(part.split("=", 1)[1] for part in options.split(",") if part.startswith("size="))
    raw = raw.strip().lower()
    if raw.endswith("g"):
        return int(float(raw[:-1]) * 1024)
    if raw.endswith("m"):
        return int(float(raw[:-1]))
    if raw.endswith("k"):
        return max(1, int(float(raw[:-1]) / 1024))
    return int(raw) // (1024 * 1024)


# ---------------------------------------------------------------------------
# El defecto: 64m se queda corto para una instalación de dependencias en frío.
# ---------------------------------------------------------------------------
def test_tmp_is_big_enough_for_a_cold_dependency_install() -> None:
    """Composer avisa por debajo de 100 MiB; el default tiene que dejar holgura."""
    assert _size_mb(_tmp_options()) >= 256


def test_the_operator_can_tune_it() -> None:
    """Es el punto entero del follow-up: que el tamaño salga de los settings."""
    assert _size_mb(_tmp_options(Settings(test_runtime_tmp_size="1g"))) == 1024


def test_tmp_keeps_its_hardening_flags() -> None:
    """Configurable no es «relajado»: `nosuid` se queda.

    `noexec` NO se pone, por el mismo motivo documentado para HOME: las
    toolchains ejecutan binarios legítimos desde sus temporales (instaladores
    que se descomprimen y se lanzan). Ponerlo rompería instalaciones reales, y
    se pinea aquí para que nadie lo «endurezca» sin leer esto.
    """
    options = _tmp_options()
    assert "nosuid" in options
    assert "noexec" not in options


def test_the_home_tmpfs_is_untouched() -> None:
    """F3 toca `/tmp`, no el HOME de C-01. Guarda contra el copy-paste."""
    tmpfs = _kwargs()["tmpfs"]
    assert "/home/agent" in tmpfs
    assert "uid=1000" in tmpfs["/home/agent"]


# ---------------------------------------------------------------------------
# El invariante que impide cambiar un ENOSPC legible por un OOM mudo.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", sorted(catalog.list_ids()))
def test_tmp_never_eats_more_than_half_the_container_memory(template_id: str) -> None:
    """Las páginas del tmpfs cuentan contra el cgroup de memoria del contenedor.

    Un `/tmp` dimensionado cerca del `mem_limit` deja al proceso sin RAM y el
    kernel lo mata: el operador ve un exit 137 sin mensaje, en vez del «no queda
    espacio» que sí le dice qué hacer. Se cruza contra el catálogo ENTERO para
    que una plantilla nueva con poca memoria no herede un tmp desproporcionado.
    """
    kwargs = _kwargs(template_id=template_id)
    mem_mb = int(str(kwargs["mem_limit"]).rstrip("m"))
    assert (
        _size_mb(str(kwargs["tmpfs"]["/tmp"])) <= mem_mb // 2
    ), f"{template_id}: /tmp ({kwargs['tmpfs']['/tmp']}) contra mem_limit {mem_mb}m"
