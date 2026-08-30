"""Default capability map per agent ROLE for built-in teams (Ola B / ADR 0055).

Los equipos built-in deben salir "completos": cada agente con tools + skills
sensatas por su rol. Este módulo es la fuente DRY del conjunto de SKILLS por rol;
las tools por equipo siguen en el seed del equipo, y un agente puede OVERRIDEar
con sus propios `skill_slugs` (p.ej. el equipo PHP CodeIgniter 4 pone php-phpunit
/ codeigniter4-hmvc / doctrine-orm en sus backend_dev).

Aquí solo skills AGNÓSTICAS de stack (aplican a cualquier lenguaje), porque el
mapa lo comparten TODOS los equipos built-in. Los slugs salen del catálogo
`builtin_skills.py` (incluye las añadidas en la Ola B0.1).
"""

from __future__ import annotations

# rol (AgentRole value) -> slugs de skill del catálogo `builtin_skills.py`.
ROLE_DEFAULT_SKILLS: dict[str, tuple[str, ...]] = {
    "project_manager": ("cost-benefit-analysis", "structured-writing"),
    "architect": ("adr-authoring", "technical-comparison", "mermaid-diagrams"),
    "backend_dev": (
        "database-migrations",
        "api-versioning",
        "sql-optimization",
        "secure-coding-owasp",
    ),
    "frontend_dev": ("responsive-design", "accessibility-aria", "web-performance"),
    "security": ("secure-coding-owasp", "dependency-audit-sca", "secrets-vault"),
    "specialist": ("technical-comparison", "evidence-collection", "web-research"),
    "qa": (
        "test-pyramid-design",
        "regression-test-strategy",
        "edge-case-identification",
        "contract-testing",
    ),
    "reviewer": (
        "regression-test-strategy",
        "edge-case-identification",
        "secure-coding-owasp",
    ),
    "devops": (
        "docker-compose-orchestration",
        "github-actions-ci",
        "observability-otel",
        "backup-recovery",
    ),
    "researcher": (
        "technical-comparison",
        "literature-review",
        "evidence-collection",
        "web-research",
    ),
    "technical_writer": (
        "structured-writing",
        "mermaid-diagrams",
        "api-documentation",
        "changelog-authoring",
    ),
}


# rol -> slugs de tool del catálogo `builtin_tools.py`. Todo rol lee (read/list
# + semantic-search); los que producen código además escriben/ejecutan.
#
# PROJ-08/F3 (auditoría 2026-07-17): `search-code`, `apply-patch` y
# `summarize-text` se RETIRAN — no están cableadas en el runtime
# (RUNTIME_WIRED_TOOL_NAMES): el agente las veía, las invocaba y fallaban
# siempre, quemando iteraciones. El grep vive dentro de shell-exec/stack_exec;
# el patching es write-file; resumir es el propio LLM.
# F5 de registry-egress-followups (2026-07-28): los cuatro `run-*` se RETIRAN de
# los defaults y se sustituyen por `stack-exec`. No es un recorte de capacidades:
# `run-*` son `docker_command` y `DockerCommandTool` falla SIEMPRE dentro del
# sandbox por diseño (la imagen del agent-runtime no lleva cliente Docker ni
# socket), así que lo que los roles tenían concedido era una promesa falsa —
# 62 grants vivos, un turno quemado por invocación.
#
# `stack-exec` (ADR 0093) es la vía que sí ejecuta: el worker corre el toolchain
# del proyecto en su runtime-template. Un rol que producía código sin ella se
# quedaría de verdad sin forma de correr tests o linters, y por eso se añade a
# todos los que la necesitan en vez de dejarlos pelados.
_READ = ("read-file", "list-files", "semantic-search")

# ---------------------------------------------------------------------------
# QUIÉN EJECUTA EL TOOLCHAIN — el criterio, en un solo sitio
# ---------------------------------------------------------------------------
# Hasta hoy convivían DOS criterios en el mismo repo: este mapa era selectivo
# (unos roles con `stack-exec`, otros no) y el equipo CI4 repartía `_BASE_TOOLS`
# a brocha gorda (los diez agentes, PM incluido). Dos criterios en competencia no
# son una tensión estética: es que la respuesta a «¿este agente puede correr
# `composer install`?» dependía de por qué seed hubiese entrado, y nadie podía
# revisarla porque no estaba escrita en ninguna parte.
#
# El criterio, ahora explícito: `stack-exec` (ADR 0093) la necesita quien
# INSTALA, COMPILA, EJECUTA TESTS/LINTERS/MIGRACIONES o ARRANCA EL STACK. No la
# necesita quien sólo lee, redacta o delega.
#
# Los cuatro roles EXCLUIDOS, y por qué (esto es la parte que hay que poder
# auditar dentro de seis meses):
#
#   * `project_manager`  — su propio prompt dice «NO escribes código, delegas».
#   * `technical_writer` — su producto es documentación; escribe, no ejecuta.
#   * `researcher`       — «no implementas; tu producto es un documento».
#   * `reviewer`         — el caso que NO es obvio y que sí es peligroso. El ADR
#     0095 le monta el worktree del implementador en READ-ONLY a propósito, pero
#     `stack_exec` no corre en el sandbox del agente: el worker lo lanza en el
#     runtime-template del proyecto sobre ESE MISMO worktree resuelto por
#     `task_id` y montado `read_only=False` (workers/test_runtime.py:1154). O sea
#     que darle `stack-exec` al reviewer reabre por la puerta de atrás el
#     aislamiento que el ADR 0095 firmó, dejándole escribir `vendor/`, cachés y
#     cobertura encima del trabajo sin commitear del implementador — que es justo
#     de donde el worker commitea después. Y no le hace falta: la plataforma ya
#     corre la suite por él y le entrega el bloque `<test-report>`. Un
#     `stack_exec` de sólo-lectura no existe hoy y no se puede improvisar:
#     `allowed_commands` es UNA lista para las dos puertas (ADR 0162), así que
#     no hay forma de conceder «ejecutar sin escribir» sin un ADR nuevo.
#
# `architect` y `specialist` SÍ entran: el arquitecto «escribe esqueletos y
# módulos base» —y andamiar un módulo es `php spark` / `composer create-project`,
# que es literalmente el comando con el que se atascó el run de 2,22 USD— y el
# specialist de i18n regenera traducciones con el CLI del framework.
ROLES_THAT_EXECUTE_TOOLCHAIN: frozenset[str] = frozenset(
    {
        "architect",
        "backend_dev",
        "frontend_dev",
        "qa",
        "devops",
        "security",
        "specialist",
    }
)

# ---------------------------------------------------------------------------
# QUIÉN NO PUEDE ESCRIBIR EN SU WORKSPACE — el otro criterio, mismo sitio
# ---------------------------------------------------------------------------
# La mitad gemela del bloque de arriba, y llega tarde por la misma razón por la
# que llegó tarde aquélla: se arregló `stack-exec` derivándola del rol y se
# dejaron cableadas a mano las tools de escritura, de modo que el equipo CI4 le
# daba `write-file` y `delete-file` a su reviewer mientras el mapa por rol decía
# `_READ`. Otra vez dos criterios en competencia, otra vez sin escribir.
#
# Esto NO es una preferencia de diseño: es un hecho del runtime. En una ejecución
# de REVIEW el worker monta el worktree del implementador en SÓLO LECTURA
# (`ws.read_only = ...` en `workers/execution.py`, rama `review_worktree`, ADR
# 0095), y es la ÚNICA rama que lo hace. Un `write_file` desde ahí no es una
# capacidad discutible: rebota con EROFS.
#
# Y rebotar es justo la trampa del ADR 0162 — la puerta se abre, el error que
# vuelve es del sistema de ficheros, y el agente no puede distinguir «no me
# dejan» de «me equivoqué de ruta», así que reintenta. Es el mismo mecanismo que
# quemó 24 llamadas buscando `php`: no le falta permiso, le sobra puerta.
ROLES_WITH_READ_ONLY_WORKSPACE: frozenset[str] = frozenset({"reviewer"})

# Las tools que ESCRIBEN en el workspace. Se nombran aquí, y no dentro de cada
# seed, para que añadir una tercera puerta de escritura no obligue a acordarse
# de este caso: quien la añada al catálogo la añade también aquí, y la guarda
# estructural (`tests/unit/test_builtin_prompt_tool_coherence.py`) la cubre sola.
WORKSPACE_MUTATING_TOOLS: frozenset[str] = frozenset({"write-file", "delete-file"})

# `write-file` para `security` y `researcher` (2026-08-30): no es una capacidad
# nueva, es que sus prompts YA les ordenaban producir un fichero y no tenían con
# qué. El Security Specialist «mantiene una lista viva de riesgos conocidos en
# /docs/06-runbooks/security.md» y el Researcher cierra con «tu producto es un
# documento». Un prompt que ordena lo imposible no es un permiso olvidado: es un
# agente que gira hasta agotar reintentos, o que cierra la tarea sin entregable.
ROLE_DEFAULT_TOOLS: dict[str, tuple[str, ...]] = {
    "project_manager": _READ,
    "architect": (*_READ, "write-file", "stack-exec"),
    "backend_dev": (*_READ, "write-file", "stack-exec"),
    "frontend_dev": (*_READ, "write-file", "stack-exec"),
    "qa": (*_READ, "write-file", "stack-exec"),
    # Sin `stack-exec` (ADR 0095, ver arriba) y sin `shell-exec`: el reviewer
    # recibe el diff y el `<test-report>` ya calculados, y su workspace está
    # montado de sólo lectura. Darle una puerta de ejecución cuyo toolchain no
    # existe en el sandbox sólo le enseñaría a perseguir un «not found».
    "reviewer": _READ,
    "devops": (*_READ, "write-file", "stack-exec", "shell-exec"),
    "security": (*_READ, "write-file", "stack-exec"),
    "specialist": (*_READ, "write-file", "http-get", "stack-exec"),
    "researcher": (*_READ, "write-file", "http-get"),
    "technical_writer": (*_READ, "write-file"),
}


def default_skill_slugs(role: str) -> tuple[str, ...]:
    """Skills por defecto de un rol (vacío si el rol no está mapeado)."""
    return ROLE_DEFAULT_SKILLS.get(role, ())


def default_tool_slugs(role: str) -> tuple[str, ...]:
    """Tools por defecto de un rol (vacío si el rol no está mapeado)."""
    return ROLE_DEFAULT_TOOLS.get(role, ())


__all__ = [
    "ROLES_THAT_EXECUTE_TOOLCHAIN",
    "ROLES_WITH_READ_ONLY_WORKSPACE",
    "ROLE_DEFAULT_SKILLS",
    "ROLE_DEFAULT_TOOLS",
    "WORKSPACE_MUTATING_TOOLS",
    "default_skill_slugs",
    "default_tool_slugs",
]
