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

Tercera cosecha, 2026-08-20: **-15 entradas (el inventario queda VACÍO)**. Eran
las que la segunda dejó por no mecanizables: casillas de planes viejos (06, 06.5,
07, 08, prod-01, prod-04, prod-05, prod-12, prod-13, prod-17, prod-18) **sin nota
de cierre**, o sea sin nada escrito que dijera qué test las cubría. Se reparten
así, y cuatro de las cinco formas ya estaban catalogadas:

* **forma 1** (el test existe con otro nombre o en otro árbol), **10**: las diez
  se identificaron LEYENDO el fichero candidato, y en siete el propio docstring
  nombra la tarea. Ocho piden `-k` porque el fichero cubre varias casillas.
* **forma 8** (el fichero se fue con la funcionalidad), **1**: ``task_06_07``, el
  modo testcontainers, borrado en ``7959cdcb`` — el MISMO commit de las ocho de
  ``task_06_20b*`` y de ``task_prod_02_12``. Tres cosechas y sigue apareciendo:
  ese commit se llevó 2.200 líneas y **nadie repasó las casillas que las
  declaraban**. Casilla DESMARCADA: no hay dos maneras de cerrar en ``[x]`` código
  que no existe.
* **forma 9, NUEVA** (la casilla describe un diseño que un ADR posterior
  sustituyó), **1**: ``task_08_01`` implementó OIDC *per-tenant* y el ADR 0047
  rehízo el SSO GLOBAL, retirando esas rutas sin redirección. El comando pasa a
  apuntar al test del diseño VIGENTE y la casilla lo dice. Aquí repuntar a secas
  habría sido lo peor de todo: dejaría el plan afirmando un diseño derogado, y con
  un test en verde debajo.
* **forma 10, NUEVA** (el test no existía y hacía falta: se escribe), **2**:
  ``task_06_5_01`` (migración ``review_sessions``) y ``task_08_12``
  (``GET /auth/discover``). Los dos llevan más de dos años en producción y de los
  dos había lo mismo: una guarda genérica que los cubría *por descubrimiento* y
  ni un test de su comportamiento propio. 22 tests nuevos.
* **forma 11, NUEVA** (la medida es automática pero NO es un pytest), **1**:
  ``task_prod01_01`` declaraba ``tests/smoke/test_app_images_build.py``, o sea
  cuatro ``docker build`` de imágenes pesadas dentro de la suite. Eso es el job
  ``build-images`` de CI. La orden pasa a las dos guardas que sí son pytest y que
  cubren el modo de fallo real —que el job y el workflow de publicación **no se
  dejen una app atrás**, derivando la lista de los Dockerfiles—. Distinta de la
  forma 2: allí la medida no podía ser automática; aquí puede, pero no aquí.

**Lo que enseña esta cosecha, y no se veía desde el inventario:** en dos casos el
comando roto era el síntoma menor. ``task_06_34`` promete «cap por tenant
**configurable** + **cola** cuando se llega al cap»; existe el cap, es la
constante ``DEFAULT_TENANT_CAP = 5`` y la N+1 se **rechaza**, no se encola. Ni el
test borrado probaba una cola: esa mitad **nunca se construyó**. Y el título de
``task_08_12`` dice «email → tenant», mitad que el ADR 0047 descartó
expresamente. O sea: **un ``command:`` que apunta a un fichero inexistente es
además el sitio donde se esconde un enunciado que nadie ha releído**, porque es lo
único del bloque que una máquina puede comprobar. Buscar el test obliga a leer el
código, y ahí salen las promesas que sobran.

Dos trampas para quien añada órdenes con ``-k``, las dos pagadas aquí:

* **``-k`` casa contra el node id COMPLETO, nombre de fichero incluido.**
  ``test_redis_cache_and_chat_rate_limit.py -k 'rate_limit or …'`` selecciona los
  8 tests del fichero, no los 4 de la casilla — el ``rate_limit`` del NOMBRE casa
  siempre. Se comprueba con ``--collect-only`` antes de escribir la orden; así se
  detectó.
* **Un test puede pasar por el motivo equivocado.** El primer test de la
  invariante ``plan_id NULL ⇒ kind='preview'`` insertaba con un ``tenant_id``
  inventado: habría pasado por la violación de la FK del tenant, y habría seguido
  verde el día que el CHECK desapareciera. Lleva ahora el nombre de la constraint
  en el assert y su control al lado (la misma fila como ``preview`` SÍ entra).

Las 15 correcciones se CORRIERON, una a una y los ficheros de integración en
solitario (la máquina llevaba cinco agentes): 22 tests nuevos en verde, 55 de
integración y 51 unit sobre los repuntes. Y con rojos deliberados, para que no
sean asserts decorativos: cuatro mutaciones del router de discovery (filtro
``enabled``, filtro ``deleted_at``, ``order_by`` invertido, sin normalizar a
minúsculas) dieron 5 rojos, cada uno en su test, y quitar ``review_sessions`` de
la migración 0125 puso en rojo el assert del ``FORCE``.

**El inventario queda vacío, y ése es el estado bueno**: los dos tests de abajo
siguen vigilando las dos direcciones — que no aparezca una casilla marcada nueva
con un test que no existe, y que nadie vuelva a meter aquí una entrada muerta. El
por qué de cada caso NO vive en este fichero: vive en la nota de cierre de su
casilla, que es donde mira quien va a implementarla.
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
#: Nacio con **76** entradas y esta **VACIO desde el 2026-08-20** (tres cosechas:
#: -20, -39, -15). Se queda aquí, vacío y con su nombre y su fecha, porque su
#: trabajo no era la lista: es el par de tests de abajo, que ahora vigilan un
#: suelo de cero. Al inventario NO se vuelve a añadir nada — una casilla nueva que
#: declare un test inexistente rompe `test_no_new_marked_task_declares_a_test_that
#: _does_not_exist`, y la respuesta es escribir el test o corregir el comando, no
#: apuntarlo aquí.
#:
#: Por qué no se borra el símbolo: un `frozenset()` vacío con nombre fechado dice
#: «esto se midió, se saldó y sigue medido». Sin él, `_declarados_que_faltan()`
#: quedaría comparándose contra nada y el día que alguien reintrodujese deuda no
#: habría dónde ver que un día hubo 76 ni que se cerraron una por una. El relato
#: de las tres cosechas está en el docstring del módulo; el por-qué de cada caso,
#: en la nota de cierre de su casilla del roadmap.
_DECLARED_TEST_DEBT_2026_08_19: frozenset[tuple[str, str, str]] = frozenset()


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
