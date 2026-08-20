"""Una casilla marcada no puede declarar un test que no existe.

Cada tarea del roadmap declara sus ``command:`` en un bloque yaml. Cuando la
casilla esta ``[x]``, ese comando es la prueba de que la tarea se verifico. Si el
fichero que nombra no existe, el comando **no puede haber pasado nunca**: pytest
y playwright con un fichero que no casa salen con codigo distinto de cero
(«No tests found», comprobado). O sea que el checkbox afirma una verificacion
imposible.

Medido el 2026-08-19 sobre los 824 comandos declarados del roadmap: **76**
caminos inexistentes en casillas ya marcadas. De ellos, unos 59 tienen un fichero
de nombre parecido —renombrados y consolidaciones, o sea enunciados desfasados— y
17 no tienen ninguno. Esa distincion NO la decide este test: la decide quien
audite cada caso, y hasta entonces las dos poblaciones cuentan como deuda.

Este fichero **no arregla las 76**: las congela, e impide que aparezca la
siguiente. Es el patron de inventario congelado que ya usan
``_GATE_DEBT_2026_07_29`` y ``_DELIVERED_BUT_UNSTARTED_2026_08_12`` en
``test_roadmap_frontmatter.py``. Vigila las dos direcciones: una entrada que deja
de faltar tiene que salir del inventario, o la lista describe un mundo que ya no
existe.

Como se retira una entrada: o se escribe el test, o se corrige el comando para
que nombre el que de verdad cubre esa tarea, con una nota que diga por que. El
caso que destapo esto: ``task_prod16_02`` declaraba ``e2e/lang-toggle.spec.ts``,
que nunca existio; el equivalente real, ``e2e/lang-switcher.spec.ts``, cubre lo
mismo y llevaba meses al lado.

Primera cosecha, 2026-08-19: **-20 entradas** (de 76 a 56), y las tres formas que
tomaba la mentira resultaron ser distintas, lo que importa porque cada una se
arregla de otra manera:

1. **El test existe con otro nombre o en otro arbol** (11). Casi siempre el arbol
   es ``docker/agent-runtimes/agent-runtime/tests/``: los guardrails y las tools
   HTTP corren DENTRO del sandbox, asi que sus tests viven junto al codigo y no
   en ``tests/integration/``. Otras veces el test es ``unit`` y el plan lo
   declaro ``integration`` porque se asumio que haria falta una BD.
2. **La medida no puede ser automatica** (1, ``task_prod10_06``). Afirmaba una
   propiedad del contenedor DESPLEGADO. El bloque se retira y apunta al test
   humano que ya la lleva; escribirlo habria sido fingir.
3. **El comando contradecia la decision que la casilla implementa** (1,
   ``task_prod10_11``): declaraba el test de la migracion a Vault que el
   ADR 0146 descarto. Ahi no hay nada que repuntar, hay que reescribir que se
   verifica.

Y el hallazgo que este inventario no podia ver por si solo: **ocho** de las
entradas (``task_06_20b1``..``b6``) no eran comandos desfasados sino casillas
``[x]`` describiendo codigo BORRADO del repo el 2026-07-26 (``7959cdcb``, el pool
elastico de runtime). El fichero que falta era la sombra de un modulo que
tampoco esta. Este test detecta el sintoma; distinguirlo del resto exige mirar
el ``git log``.

Segunda cosecha, 2026-08-20: **-39 entradas** (de 54 a 15), en cinco formas
que no coinciden con las tres de arriba:

4. **La guarda se equivocaba** (4). Los comandos
   ``npm --prefix apps/admin-panel run e2e -- agent-persona.spec.ts`` CORREN:
   Playwright resuelve ese argumento contra su ``testDir``, y este fichero lo
   resolvia contra la raiz del repo. Cuatro falsos positivos en un inventario de
   54 no son un detalle cosmetico: **un inventario con ruido pierde la autoridad
   para señalar los verdaderos**, que es justo lo que hace util a esta guarda. El
   arreglo esta en ``_PREFIJOS``, atado al ``testDir`` real por
   ``test_the_playwright_test_dir_is_still_e2e``.
5. **La carpeta declarada no es donde acabo el test** (10). El plan escribio
   ``tests/e2e/`` o ``tests/integration/`` cuando se diseño la tarea, y el test
   acabo en ``tests/integration/`` o ``tests/unit/``. Varias notas de cierre lo
   DICEN con todas las letras —``tests/e2e/`` pide runner Docker y CI no lo
   corre, «un test que no se ejecuta no vigila»— pero nadie volvio al bloque
   ``command:``.
6. **El nombre declarado no existio nunca** (13, todas de prod-03 y prod-08). El
   plan escribio el nombre que la tarea imaginaba y el implementador escribio
   otro. Se identifico el fichero real LEYENDOLO, no por parecido: varios lo
   dicen en su propio docstring («coordinado con prod-03 ``task_prod03_02``»,
   «prod-08 Fase B, task 07»). Dos casillas resultaron necesitar DOS ficheros
   —el unit del mapeo y el de integracion de la persistencia—, y una un ``-k``,
   porque un fichero cubre dos casillas y cada orden debe verificar la suya.

7. **La casilla estaba cerrada EN NEGATIVO** (1, ``task_prod13_15``): el
   ADR 0151 descarto la task que pedia, asi que el test que declaraba no existe
   y no debe existir. Su orden pasa a ser la guarda de gobernanza que pinea ese
   cierre (``rejects: [task_prod13_15]`` en el frontmatter del ADR). Y una
   septima (``task_prod12_net_01``) tenia el test nombrado en su propia nota de
   cierre, tres parrafos mas abajo del bloque ``command:`` que lo contradecia.

8. **El fichero se BORRO con la funcionalidad** (1, ``task_prod_02_12``).
   ``test_pool_queue.py`` desaparecio en ``7959cdcb`` (2026-07-26) junto al pool
   elastico de runtime — el mismo commit que ya explicaba las ocho de
   ``task_06_20b*``. La orden se queda con la mitad viva (el rate-limiter). Aqui
   NO basta con repuntar: hay que mirar el ``git log``, porque un fichero que se
   fue con su feature y uno que se renombro se ven igual desde aqui.

**Las 39 correcciones se corrigieron y se CORRIERON**, todas en verde: 354 tests
unit, 108 de integracion y las 8 de la guarda de gobernanza. Cambiar la ruta sin
correr el test habria sustituido una orden imposible por una orden sin comprobar,
que es la misma clase de mentira una capa mas arriba.

Un aviso para la tercera cosecha, aprendido aqui: tres ficheros de integracion
dieron **12 rojos** al correrlos juntos mientras cinco agentes usaban el mismo
stack, y **21/21 verdes** al correrlos de uno en uno. Antes de apuntar un rojo de
integracion en ningun sitio, correlo solo.

Lo que queda (15) ya no es mecanico, y por eso se para aqui: son casillas de
los planes viejos (06, 06.5, 07, 08) **sin nota de cierre**, donde no hay nada
escrito que diga que test las cubre. Varias huelen a una tercera forma distinta
—la casilla describe un diseño que un ADR posterior sustituyo: el ``task_08_01``
pide OIDC por tenant y el SSO se rehizo global (ADR 0047)—, y esa se arregla
sincerando la casilla, no repuntando el comando. Requiere leer el codigo y
decidir caso por caso; hacerlo por parecido de nombre seria cambiar una mentira
por otra.
"""

from __future__ import annotations

import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_ROADMAP = _RAIZ / "docs" / "roadmap"

_TOKEN = re.compile(r"[\w./\-\[\]]+\.(?:py|ts|tsx|mjs|js|sh|ps1)\b")
_CMD = re.compile(r"^\s*command:\s*[\"']?(.+?)[\"']?\s*$")
_TAREA = re.compile(r"^#{2,5}\s+`?(task_[\w.]+)`?")
_CASILLA = re.compile(r"^\s*-\s+\[([ xX])\]")

#: Inventario CONGELADO el 2026-08-19: (fichero del plan, tarea, camino que falta).
#: No crece. Al arreglar una entrada, borrala de aqui: hay un test que se pone
#: rojo si sobra.
_DECLARED_TEST_DEBT_2026_08_19: frozenset[tuple[str, str, str]] = frozenset(
    [
        (
            "06-testing-revision-git.md",
            "task_06_07",
            "tests/integration/test_testcontainers_mode.py",
        ),
        # Las OCHO entradas de `task_06_20b1`..`b6` (el pool elastico de runtime) salieron
        # el 2026-08-19: no eran comandos desfasados sino casillas `[x]` describiendo
        # codigo BORRADO en el commit 7959cdcb (2026-07-26), que se llevo por delante
        # `workers/runtime_pool.py` y esos mismos ocho ficheros de test. Las seis casillas
        # estan hoy desmarcadas y con el enunciado reescrito en
        # docs/roadmap/06-testing-revision-git.md.
        ("06-testing-revision-git.md", "task_06_34", "tests/integration/test_review_cap.py"),
        (
            "06.5-orchestrator-wiring.md",
            "task_06_5_01",
            "tests/integration/test_migration_review_sessions.py",
        ),
        ("07-documentacion-visor.md", "task_07_18", "tests/integration/test_docs_rbac.py"),
        ("08-sso-empresarial.md", "task_08_01", "tests/integration/test_oidc_generic.py"),
        ("08-sso-empresarial.md", "task_08_12", "tests/integration/test_login_discovery.py"),
        (
            "prod-01-despliegue-ejecutable.md",
            "task_prod01_01",
            "tests/smoke/test_app_images_build.py",
        ),
        # `task_prod03_12` salio el 2026-08-19: los cuatro hooks corren DENTRO del sandbox,
        # asi que sus tests viven en docker/agent-runtimes/agent-runtime/tests/
        # (test_llm_guardrail_hooks / test_guardrails_enforce / test_act_guardrail_wiring /
        # test_guardrails_seam), no en tests/integration/.
        (
            "prod-04-backup-dr-restaurable.md",
            "task_prod_04_08",
            "tests/integration/test_restore_grants.py",
        ),
        # Cuatro entradas de prod-05 salieron el 2026-08-19 (`task_prod05_01`, `_05`, `_06`
        # y `_08`): las cuatro declaraban un test de INTEGRACION que nunca existio, y las
        # cuatro propiedades estan probadas en `tests/unit/` porque no necesitan BD — lo
        # que verifican es el anillo de claves, el ORDEN de las operaciones y la cabecera
        # del blob. El `_06` lo decia ya su propia prosa («vive en tests/unit/ porque no
        # necesita Postgres ni Redis») sin que nadie bajara a corregir el `command:`.
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_03",
            "tests/integration/test_mfa_key_rotation_story.py",
        ),
        # `task_prod06_evento_01` salio el 2026-08-19: el resweep de tareas `ready` varadas
        # lo cubre el beat de dag_02 (test_dag_promotion.py + test_dag_promotion_beat.py),
        # como la propia casilla ya decia («no se duplica»).
        # `task_prod06_zombi_03` NO lo retira este carril: otro de la misma ola escribio
        # `tests/unit/test_celery_broker_options.py` (sin comitear todavia) y dejo la
        # entrada atras, con lo que `test_the_inventory_has_no_dead_entries` se ponia rojo
        # por una razon ajena. Se borra aqui para no dejar la suite en rojo; si aquel
        # carril intenta borrarla tambien, su edicion fallara en limpio (no encontrara el
        # texto) en vez de duplicar nada.
        # `task_prod10_06` salio el 2026-08-19: su `auto_..._b` se RETIRA en vez de
        # repuntarse. Afirmaba una propiedad del contenedor DESPLEGADO, no del codigo, y
        # eso es `human_prod10_02`, que ya la lleva en su checklist.
        # `task_prod10_11` salio el 2026-08-19, y era el peor de los 76: el fichero no solo
        # faltaba, es que habria verificado la MIGRACION A VAULT — la opcion A, la que el
        # ADR 0146 descarto. El bloque yaml declara ahora lo que de verdad hay que
        # comprobar: que la salvaguarda de la opcion B se cumple y que la excepcion
        # Fernet-en-columna no ha crecido (tests/unit/test_backup_column_secrets.py).
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_docker_01",
            "tests/unit/test_docker_command_tool_retired.py",
        ),
        # `task_prod12_ssrf_02` salio el 2026-08-19: el anclaje de DNS y el
        # `follow_redirects=False` los prueba
        # docker/agent-runtimes/agent-runtime/tests/test_http_tools_destination_validation.py,
        # al lado del codigo. Ojo: las entradas de `task_prod12_ssrf_01` de aqui arriba
        # apuntan a `tests/unit/` con ESE MISMO nombre de fichero y siguen vivas — son otra
        # casilla y no las toca este arreglo.
        # `task_prod13_10` salio el 2026-08-19: el indice y la unificacion de configuracion
        # FTS los prueba tests/integration/test_bm25_search.py.
        # `task_prod13_17` salio el 2026-08-19: lo cubre tests/unit/test_row_lock_and_pagination.py,
        # que nombra la tarea en su primera linea y es unit a proposito (verifica la FIRMA
        # que FastAPI publica y el SQL emitido; ninguna de las dos necesita Postgres).
        (
            "prod-13-rendimiento-y-datos.md",
            "task_prod13_20",
            "tests/integration/test_assistant_chat_rate_limit.py",
        ),
        (
            "prod-17-bucle-ai-reviewer.md",
            "task_prod17_loop_02",
            "tests/unit/test_review_spec_builder.py",
        ),
        (
            "prod-17-bucle-ai-reviewer.md",
            "task_prod17_test_01",
            "tests/integration/test_test_runtime_wiring.py",
        ),
        (
            "prod-18-worktree-en-ejecucion.md",
            "task_prod18_commit_01",
            "tests/integration/test_execution_commits_to_worktree.py",
        ),
        (
            "prod-18-worktree-en-ejecucion.md",
            "task_prod18_test_01",
            "tests/integration/test_test_runtime_wiring.py",
        ),
    ]
)


#: Prefijos contra los que se resuelve un camino declarado, en el orden en que
#: lo haría quien ejecuta el comando desde la raíz del repo.
#:
#: `apps/admin-panel/e2e/` no es un prefijo más: es el `testDir` de
#: `apps/admin-panel/playwright.config.ts`, y por eso un comando como
#: `npm --prefix apps/admin-panel run e2e -- agent-persona.spec.ts` CORRE aunque
#: el token no sea un camino desde la raíz. Sin esta entrada, la guarda marcaba
#: como inexistentes cuatro specs que existen y pasan — un falso positivo en una
#: guarda es exactamente lo que le quita autoridad para señalar los verdaderos.
#: Que siga siendo `e2e` lo comprueba `test_the_playwright_test_dir_is_still_e2e`.
_PREFIJOS = (
    "",
    "apps/admin-panel/",
    "apps/admin-panel/e2e/",
    "apps/api-server/",
    "apps/installer/",
)


def _existe(token: str) -> bool:
    base = token.split("::", maxsplit=1)[0]
    for pref in _PREFIJOS:
        for t in (token, base):
            if (_RAIZ / (pref + t)).exists():
                return True
    return False


def test_the_playwright_test_dir_is_still_e2e() -> None:
    """`_PREFIJOS` copia el `testDir` de Playwright; si cambia, aquí se entera.

    Sin esto, el día que alguien mueva los specs la guarda seguiría resolviendo
    contra un directorio que ya no existe y volvería a marcar como inexistente
    todo spec nombrado a secas — sin que nada explique por qué.
    """
    config = _RAIZ / "apps" / "admin-panel" / "playwright.config.ts"
    assert config.is_file(), f"no encuentro {config}"
    assert 'testDir: "./e2e"' in config.read_text(encoding="utf-8"), (
        "el `testDir` de Playwright ya no es `./e2e`. Actualiza `_PREFIJOS` o la"
        " guarda volverá a dar falsos positivos con los specs nombrados a secas."
    )


def _declarados_que_faltan() -> set[tuple[str, str, str]]:
    """(plan, tarea, camino) de cada comando de una casilla MARCADA cuyo fichero
    no existe."""
    faltan: set[tuple[str, str, str]] = set()
    for md in sorted(_ROADMAP.glob("*.md")):
        tarea, estado = "?", None
        for linea in md.read_text(encoding="utf-8").split("\n"):
            m_t = _TAREA.match(linea)
            if m_t:
                tarea, estado = m_t.group(1), None
                continue
            m_c = _CASILLA.match(linea)
            if m_c and estado is None:
                estado = "x" if m_c.group(1).lower() == "x" else " "
                continue
            m = _CMD.match(linea)
            if m is None or estado != "x":
                continue
            for token in _TOKEN.findall(m.group(1)):
                if token.startswith(("npx", "node_modules")):
                    continue
                if not _existe(token):
                    faltan.add((md.name, tarea, token))
    return faltan


def test_the_discovery_actually_finds_the_declared_commands() -> None:
    """No-vacuidad: si el parseo se rompe, los dos tests de abajo pasan solos.

    Se afirma sobre el UNIVERSO (comandos declarados), no sobre los que faltan:
    el dia que las 76 se arreglen el inventario quedara vacio y eso esta bien,
    pero que no haya NI UN comando declarado solo puede ser un parser roto.
    """
    total = 0
    for md in _ROADMAP.glob("*.md"):
        for linea in md.read_text(encoding="utf-8").split("\n"):
            if _CMD.match(linea):
                total += 1
    assert total >= 500, f"esperaba cientos de comandos declarados, encontre {total}"


def test_no_new_marked_task_declares_a_test_that_does_not_exist() -> None:
    nuevas = _declarados_que_faltan() - _DECLARED_TEST_DEBT_2026_08_19
    assert not nuevas, (
        "casillas MARCADAS que declaran un test cuyo fichero no existe y que NO"
        " estaban en el inventario del 2026-08-19:\n"
        + "\n".join(f"  {plan} :: {tarea} -> {token}" for plan, tarea, token in sorted(nuevas))
        + "\n\nUn comando que nombra un fichero inexistente no puede haber pasado:"
        " pytest y playwright salen != 0 con «No tests found». O escribes el test,"
        " o corriges el comando para que nombre el que de verdad cubre la tarea."
    )


def test_the_inventory_has_no_dead_entries() -> None:
    """Una entrada que ya no falta describe un mundo que no existe."""
    vivas = _declarados_que_faltan()
    muertas = _DECLARED_TEST_DEBT_2026_08_19 - vivas
    assert not muertas, (
        "estas entradas del inventario YA no faltan (se escribio el fichero, se"
        " corrigio el comando, o se desmarco la casilla). Borralas del"
        " inventario:\n"
        + "\n".join(f"  {plan} :: {tarea} -> {token}" for plan, tarea, token in sorted(muertas))
    )
