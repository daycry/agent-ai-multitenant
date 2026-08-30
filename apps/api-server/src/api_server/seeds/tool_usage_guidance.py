"""El bloque de prompt que explica CUÁL de las dos puertas de ejecución usar.

Existe por un run real: un agente de un proyecto CodeIgniter 4 pidió a la
primera lo correcto —``composer create-project codeigniter4/appstarter .``— por
``stack_exec``, la plataforma contestó ``tool stack_exec not allowed in this
mode`` (no tenía el grant), y el agente se pasó 24 llamadas a ``shell_exec``
buscando PHP dentro de su propio sandbox hasta agotar reintentos. 2,22 USD y
62,2k tokens sin instalar nada.

La trampa no es un permiso olvidado, y por eso no basta con repartir la tool
(ADR 0093 + ADR 0162):

* ``allowed_commands`` del proyecto es **UNA lista para DOS puertas**. Si el
  operador autoriza ``composer``, lo autoriza para ``shell_exec`` y para
  ``stack_exec`` por igual.
* ``shell_exec`` corre DENTRO del sandbox del agente, que sólo lleva python y
  git (principios 2 y 3). ``stack_exec`` se lo pide al worker, que lanza el
  runtime-template del proyecto —donde sí existen composer/php/phpunit/npm—
  sobre el worktree de la tarea.
* Como la lista es común, ``shell_exec`` **acepta** el comando y falla con el
  error CRUDO del sistema operativo: ``not found``. Un agente no puede
  distinguir ese error de un problema de PATH, así que concluye que le falta la
  ruta y se pone a buscar el binario. Le cerramos la puerta buena y le dejamos
  abierta la mala.

De ahí que el texto sea GENERADO y no tecleado agente a agente: se deriva de las
tools que el agente tiene de verdad, así que no puede prometer una puerta que no
existe ni callarse la que sí. Un prompt que no nombra una tool la usa poco y
mal; uno que nombra la que no tiene quema turnos.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Slug de catálogo (kebab) de las dos puertas. Los seeds hablan kebab
#: (``stack-exec``); el runtime y el prompt hablan snake (``stack_exec``).
STACK_EXEC_SLUG = "stack-exec"
SHELL_EXEC_SLUG = "shell-exec"


# ---------------------------------------------------------------------------
# Caso 1 — tiene LAS DOS. El texto tiene que decir cuál es cuál, porque el error
# de equivocarse no lo dice.
# ---------------------------------------------------------------------------
BOTH_DOORS_ES = (
    "\n\nDOS PUERTAS PARA EJECUTAR, Y EL ERROR NO TE DIRÁ CUÁL TE FALTABA. "
    "`stack_exec` lanza el comando en el runtime del PROYECTO, que es donde "
    "existen composer, php, phpunit, npm, pip, pytest y el CLI del framework. "
    "`shell_exec` corre DENTRO de tu sandbox, que sólo lleva python y git. Las "
    "dos comparten la MISMA lista de comandos autorizados del proyecto, así que "
    "`shell_exec` ACEPTARÁ un `composer install` y luego fallará con un «not "
    "found» del sistema operativo que parece un problema de PATH y no lo es. "
    "REGLA: todo lo que sea toolchain del proyecto (instalar dependencias, "
    "compilar, correr tests, linters, análisis estático, migraciones, arrancar "
    "el stack) va por `stack_exec`; `shell_exec` es sólo para utilidades de "
    "lectura del sandbox (grep, ls, cat). NO uses `git` por ninguna de las dos: "
    "en el sandbox el `.git` del worktree apunta a metadatos que no están "
    "montados y CUALQUIER git sale con 128; la plataforma persiste y comitea tu "
    "trabajo por ti. Si un comando responde «not found» o «command "
    "not found», NO busques el binario ni pruebes rutas: lo mandaste por la "
    "puerta equivocada — repítelo con `stack_exec`."
)
BOTH_DOORS_EN = (
    "\n\nTWO DOORS TO EXECUTE, AND THE ERROR WILL NOT TELL YOU WHICH ONE YOU "
    "NEEDED. `stack_exec` runs the command in the PROJECT's runtime, which is "
    "where composer, php, phpunit, npm, pip, pytest and the framework CLI "
    "actually exist. `shell_exec` runs INSIDE your sandbox, which only carries "
    "python and git. Both share the SAME project allow-list of commands, so "
    "`shell_exec` WILL ACCEPT a `composer install` and then fail with a raw OS "
    "«not found» that looks like a PATH problem and is not. RULE: anything that "
    "is the project's toolchain (installing dependencies, building, running "
    "tests, linters, static analysis, migrations, booting the stack) goes "
    "through `stack_exec`; `shell_exec` is only for read-only sandbox utilities "
    "(grep, ls, cat). Do NOT use `git` through either door: in the sandbox the "
    "worktree's `.git` points at metadata that is not mounted, so ANY git exits "
    "128; the platform persists and commits your work for you. If a command "
    "answers «not found» or «command not found», "
    "do NOT hunt for the binary or try paths: you sent it through the wrong "
    "door — retry it with `stack_exec`."
)


# ---------------------------------------------------------------------------
# Caso 2 — sólo `stack_exec`. Sin `shell_exec` no hay puerta equivocada que
# tomar, pero sí el otro modo de fallo del mismo «not found»: el directorio.
# ---------------------------------------------------------------------------
STACK_ONLY_ES = (
    "\n\nCÓMO EJECUTAS: `stack_exec` es tu ÚNICA vía para correr algo, y no "
    "corre en tu sandbox — el worker lanza el comando en el runtime del "
    "PROYECTO, que es donde existen composer, php, phpunit, npm, pip, pytest y "
    "el CLI del framework. Úsala para instalar dependencias, compilar, correr "
    "tests y linters, aplicar migraciones y arrancar el stack. Cada llamada "
    "ejecuta UN solo programa: no encadenes con `&&`, `;` ni `|`, y no uses "
    "`cd` — para trabajar en un subdirectorio pásalo como `cwd`. Si un comando "
    "responde «not found», no es que falte el binario: o estás en el directorio "
    "equivocado (corrige el `cwd`) o el comando no está en la lista autorizada "
    "del proyecto (dilo, no pruebes variantes a ciegas)."
)
STACK_ONLY_EN = (
    "\n\nHOW YOU EXECUTE: `stack_exec` is your ONLY way to run anything, and it "
    "does not run in your sandbox — the worker launches the command in the "
    "PROJECT's runtime, which is where composer, php, phpunit, npm, pip, pytest "
    "and the framework CLI exist. Use it to install dependencies, build, run "
    "tests and linters, apply migrations and boot the stack. Each call runs ONE "
    "single program: do not chain with `&&`, `;` or `|`, and do not use `cd` — "
    "to work in a subdirectory pass it as `cwd`. If a command answers «not "
    "found», the binary is not missing: either you are in the wrong directory "
    "(fix `cwd`) or the command is not in the project's allow-list (say so, "
    "do not blindly try variants)."
)


# ---------------------------------------------------------------------------
# Caso 3 — tiene `shell_exec` y NO `stack_exec`. Es la forma exacta de la
# trampa, así que el prompt tiene que decirlo por su nombre: sin este párrafo el
# agente repite las 24 llamadas del run de 2,22 USD.
# ---------------------------------------------------------------------------
SHELL_ONLY_ES = (
    "\n\nTU SANDBOX NO LLEVA EL TOOLCHAIN DEL PROYECTO. `shell_exec` corre "
    "dentro de tu contenedor, que sólo tiene python y git: ahí NO hay composer, "
    "php, phpunit, npm ni pytest, y tú no tienes forma de lanzarlos en el "
    "runtime del proyecto. Como la lista de comandos autorizados es común a "
    "todas las vías de ejecución, `shell_exec` ACEPTARÁ un `composer ci` y "
    "fallará con un «not found» del sistema operativo que parece un problema "
    "de PATH. No lo es, y no se arregla buscando el binario ni probando rutas: "
    "esos comandos no los puedes correr tú. Usa `shell_exec` sólo para "
    "inspeccionar el workspace (grep, ls, cat, git log/diff) y, cuando "
    "necesites el resultado de la toolchain, apóyate en lo que la plataforma ya "
    "ejecutó y te entrega en el contexto."
)
SHELL_ONLY_EN = (
    "\n\nYOUR SANDBOX DOES NOT CARRY THE PROJECT'S TOOLCHAIN. `shell_exec` runs "
    "inside your container, which only has python and git: there is NO composer, "
    "php, phpunit, npm or pytest there, and you have no way to launch them in "
    "the project's runtime. Because the allow-list of commands is shared by "
    "every execution path, `shell_exec` WILL ACCEPT a `composer ci` and fail "
    "with a raw OS «not found» that looks like a PATH problem. It is not, and it "
    "is not fixed by hunting for the binary or trying paths: those commands are "
    "not yours to run. Use `shell_exec` only to inspect the workspace (grep, ls, "
    "cat, git log/diff) and, when you need a toolchain result, rely on what the "
    "platform already ran and hands you in the context."
)


def execution_guidance(tool_slugs: Iterable[str]) -> tuple[str, str]:
    """El bloque (ES, EN) que corresponde a las tools REALES del agente.

    Devuelve ``("", "")`` para un agente sin ninguna de las dos puertas: no hay
    nada que aclarar y añadirle un párrafo sobre ejecución sería enseñarle a
    intentar lo que no puede.
    """
    slugs = set(tool_slugs)
    has_stack = STACK_EXEC_SLUG in slugs
    has_shell = SHELL_EXEC_SLUG in slugs
    if has_stack and has_shell:
        return BOTH_DOORS_ES, BOTH_DOORS_EN
    if has_stack:
        return STACK_ONLY_ES, STACK_ONLY_EN
    if has_shell:
        return SHELL_ONLY_ES, SHELL_ONLY_EN
    return "", ""


__all__ = [
    "BOTH_DOORS_EN",
    "BOTH_DOORS_ES",
    "SHELL_EXEC_SLUG",
    "SHELL_ONLY_EN",
    "SHELL_ONLY_ES",
    "STACK_EXEC_SLUG",
    "STACK_ONLY_EN",
    "STACK_ONLY_ES",
    "execution_guidance",
]
