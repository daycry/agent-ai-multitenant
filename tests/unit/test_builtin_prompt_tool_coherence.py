"""Ningún prompt built-in ordena algo para lo que su agente no tiene tool.

## Por qué existe este fichero

Un run real de un proyecto CodeIgniter 4 se bloqueó tras quemar 2,22 USD y 62,2k
tokens sin instalar nada. El agente pidió a la primera lo correcto —
``stack_exec("composer create-project codeigniter4/appstarter .")``— y la
plataforma contestó ``tool stack_exec not allowed in this mode``: ninguno de los
34 agentes built-in repartía esa tool. Entonces se pasó 24 llamadas a
``shell_exec`` buscando PHP dentro de su propio sandbox hasta agotar reintentos.

Lo que convierte esto en una TRAMPA y no en un permiso olvidado (ADR 0093 + ADR
0162): ``allowed_commands`` es **UNA lista para DOS puertas**. El proyecto
autoriza ``composer``, ``shell_exec`` acepta el comando porque comparten lista,
el sandbox del agente no tiene el binario, y el error que llega es el crudo del
sistema operativo. El agente no puede distinguir «no autorizado» de «no
instalado» y concluye que le falta la ruta. Le cerramos la puerta buena y le
dejamos abierta la mala.

Y lo que lo convierte en una REGRESIÓN que ya había pasado: en la base viva, 20
agentes SÍ tenían ``stack_exec`` — todos ellos copias de tenant que un humano
parcheó a mano, dos veces, en bloque. Las copias viejas estaban mejor que el
original del que salieron, y cada equipo adoptado desde entonces nacía peor.
Alguien ya arregló esto una vez y **no dejó guarda detrás**. Este fichero es la
guarda.

## Qué fija, y en qué orden de fuerza

1. **Estructural** — el criterio de quién ejecuta el toolchain es UNO
   (``ROLES_THAT_EXECUTE_TOOLCHAIN``) y los dos catálogos (core y CI4) lo
   obedecen. Esto es lo que de verdad impide la regresión: no depende de cómo
   esté redactado ningún prompt.
2. **La trampa, por su forma exacta** — a nadie que necesite el toolchain se le
   deja `shell_exec` como única puerta.
3. **Prompt ↔ tool** — frases LITERALES de los prompts que ORDENAN una acción,
   cada una atada a la tool sin la cual esa orden es imposible de cumplir.
4. **Lo que el prompt NOMBRA** — un prompt que menciona una tool que el agente no
   tiene le quema un turno; uno que calla la que sí tiene la usa poco y mal.

Los tests 3 y 4 son incompletos por construcción (una frase nueva con otra
redacción se les escapa) y no se pretende otra cosa: el que no se escapa es el 1.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from api_server.agent_persona import PERSONA_MAX_CHARS
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from api_server.seeds.builtin_role_capabilities import (
    FILE_WRITING_TOOLS,
    ROLE_DEFAULT_TOOLS,
    ROLES_THAT_EXECUTE_TOOLCHAIN,
    ROLES_WITH_READ_ONLY_WORKSPACE,
    WORKSPACE_MUTATING_TOOLS,
)
from api_server.seeds.ci4_team import CI4_AGENTS
from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR
from api_server.seeds.tool_usage_guidance import (
    BOTH_DOORS_EN,
    BOTH_DOORS_ES,
    SHELL_ONLY_EN,
    SHELL_ONLY_ES,
    STACK_ONLY_EN,
    STACK_ONLY_ES,
)
from shared_domain.tool_names import CANONICAL_TOOL_NAMES

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Builtin:
    """Un agente built-in visto desde fuera del seed que lo declara.

    ``persona`` es lo que un humano ESCRIBIÓ; ``effective`` es lo que el agente
    RECIBE (persona + los bloques generados). La distinción no es cosmética: las
    frases que se auditan como «órdenes» son las escritas a mano, mientras que
    los bloques generados se comprueban aparte, por su identidad exacta.
    """

    slug: str
    role: str
    persona: tuple[str, str]
    effective: tuple[str, str]
    tools: frozenset[str]
    skills: frozenset[str]


def _all_builtins() -> tuple[_Builtin, ...]:
    """Los agentes built-in de los TRES seeds que los declaran.

    El QA E2E Automator entra explícitamente porque vive fuera de
    ``BUILTIN_AGENTS`` (para no mover el conteo que fija ``test_seed_agents``) y
    esa es exactamente la razón por la que se quedó sin tools sin que nada
    fallara. Un inventario que se olvide de él repetiría el descuido.
    """
    out: list[_Builtin] = []
    for agent in (*BUILTIN_AGENTS, QA_E2E_AUTOMATOR):
        out.append(
            _Builtin(
                slug=agent.slug,
                role=agent.role,
                persona=(agent.system_prompt_es, agent.system_prompt_en),
                effective=(agent.effective_prompt_es, agent.effective_prompt_en),
                tools=frozenset(agent.resolved_tool_slugs()),
                skills=frozenset(agent.resolved_skill_slugs()),
            )
        )
    for ci4 in CI4_AGENTS:
        out.append(
            _Builtin(
                slug=ci4.slug,
                role=ci4.role,
                persona=(ci4.system_prompt_es, ci4.system_prompt_en),
                effective=(ci4.effective_prompt_es, ci4.effective_prompt_en),
                tools=frozenset(ci4.resolved_tool_slugs()),
                skills=frozenset(ci4.resolved_skill_slugs()),
            )
        )
    return tuple(out)


BUILTINS = _all_builtins()


# ---------------------------------------------------------------------------
# 1. Estructural — un solo criterio, obedecido por los dos catálogos
# ---------------------------------------------------------------------------
def test_the_role_map_and_the_executing_set_say_the_same_thing() -> None:
    """``ROLE_DEFAULT_TOOLS`` no puede contradecir al criterio escrito.

    Son dos declaraciones del mismo hecho en el mismo módulo; si divergen, la
    que gana es la que casualmente lea el seed, y el criterio escrito pasa a ser
    decoración.
    """
    for role, tools in ROLE_DEFAULT_TOOLS.items():
        expected = role in ROLES_THAT_EXECUTE_TOOLCHAIN
        assert ("stack-exec" in tools) is expected, (
            f"rol {role!r}: ROLE_DEFAULT_TOOLS dice stack-exec="
            f"{'stack-exec' in tools} y ROLES_THAT_EXECUTE_TOOLCHAIN dice {expected}"
        )


def test_every_builtin_that_touches_code_can_run_the_toolchain() -> None:
    """El agujero que costó 2,22 USD, en una sola afirmación."""
    blind = sorted(
        b.slug
        for b in BUILTINS
        if b.role in ROLES_THAT_EXECUTE_TOOLCHAIN and "stack-exec" not in b.tools
    )
    assert not blind, (
        "agentes built-in cuyo rol ejecuta el toolchain y NO tienen stack_exec "
        f"(no podrán instalar, compilar ni correr tests): {blind}"
    )


def test_no_builtin_outside_the_executing_roles_gets_stack_exec() -> None:
    """La otra mitad: el reparto no puede volver a brocha gorda.

    Importa sobre todo por el `reviewer`. El ADR 0095 le monta el worktree del
    implementador en READ-ONLY, pero `stack_exec` no corre en el sandbox: el
    worker lo lanza sobre ESE MISMO worktree montado en escritura. Concedérselo
    reabre por la puerta de atrás el aislamiento que aquel ADR firmó.
    """
    over = sorted(
        b.slug
        for b in BUILTINS
        if "stack-exec" in b.tools and b.role not in ROLES_THAT_EXECUTE_TOOLCHAIN
    )
    assert not over, (
        "agentes built-in con stack_exec cuyo rol NO ejecuta el toolchain "
        f"(reviewer = ADR 0095): {over}"
    )


def test_nobody_with_a_read_only_workspace_carries_a_writing_tool() -> None:
    """La misma trampa por la otra puerta: escribir donde el montaje no deja.

    En una ejecución de REVIEW el worker monta el worktree del implementador en
    SÓLO LECTURA (`workers/execution.py`, rama `review_worktree`, ADR 0095) — es
    la única rama que lo hace. Desde ahí `write_file` no es una capacidad
    discutible: rebota con EROFS, y un error del sistema de ficheros es
    indistinguible de una ruta mal puesta, así que el agente reintenta.

    Se midió el 2026-08-30: `ci4-reviewer` llevaba `write-file` y `delete-file`
    mientras el mapa por rol decía `_READ` y su propio prompt decía «tu copia del
    workspace está montada en SÓLO LECTURA». Tres declaraciones, dos de ellas
    ciertas. Es el mismo defecto que `stack-exec` —dos criterios en competencia,
    ninguno escrito— y se arregló medio: derivando uno y dejando el otro cableado
    a mano en `_BASE_TOOLS`.
    """
    armed = sorted(
        (b.slug, sorted(b.tools & WORKSPACE_MUTATING_TOOLS))
        for b in BUILTINS
        if b.role in ROLES_WITH_READ_ONLY_WORKSPACE and (b.tools & WORKSPACE_MUTATING_TOOLS)
    )
    assert not armed, (
        "agentes built-in con el workspace montado en sólo lectura y tools de "
        f"escritura (cada intento rebota con EROFS y quema un turno): {armed}"
    )


def test_the_role_map_agrees_about_who_may_write() -> None:
    """Y el mapa por rol no puede decir lo contrario que el criterio escrito.

    Gemelo de `test_the_role_map_and_the_executing_set_say_the_same_thing`: si
    las dos declaraciones divergen, gana la que casualmente lea el seed y el
    criterio escrito pasa a ser decoración.
    """
    for role in ROLES_WITH_READ_ONLY_WORKSPACE:
        tools = frozenset(ROLE_DEFAULT_TOOLS.get(role, ()))
        assert not (tools & WORKSPACE_MUTATING_TOOLS), (
            f"rol {role!r}: ROLE_DEFAULT_TOOLS le da "
            f"{sorted(tools & WORKSPACE_MUTATING_TOOLS)} pero su workspace se "
            "monta en sólo lectura (ADR 0095)"
        )


def test_no_roster_grants_a_writing_tool_its_role_withholds() -> None:
    """La AUTORIDAD sobre quién escribe es el mapa por rol, no cada seed.

    Este es el guarda general, y llega tras ver el mismo defecto TRES veces:
    `stack-exec` repartida a brocha gorda por el equipo CI4 mientras el mapa era
    selectivo; `write-file`/`delete-file` en el `ci4-reviewer` contra un montaje
    de sólo lectura; y `ci4-pm` con las cuatro de `_FILE_TOOLS` mientras el rol
    `project_manager` está en `_READ` y su propio prompt dice «NO escribes».

    Las tres tenían la misma forma: un roster concediendo por su cuenta una
    puerta de escritura que el mapa por rol no da. Cazar cada una por separado
    deja la cuarta esperando, así que lo que se fija aquí es la REGLA, no los
    casos: un equipo puede añadir tools de lectura o de red que su rol no liste
    —`http-get` al devops, por ejemplo— pero **quién puede ESCRIBIR lo decide el
    mapa por rol y sólo él**.
    """
    exceso = []
    for b in BUILTINS:
        permitidas = frozenset(ROLE_DEFAULT_TOOLS.get(b.role, ())) & FILE_WRITING_TOOLS
        tiene = b.tools & FILE_WRITING_TOOLS
        de_mas = sorted(tiene - permitidas)
        if de_mas:
            exceso.append((b.slug, b.role, de_mas))

    assert not exceso, (
        "rosters que conceden por su cuenta una tool de ESCRITURA DE FICHEROS que "
        "el mapa por rol NO da: "
        f"{exceso}. O la tool entra en ROLE_DEFAULT_TOOLS para ese "
        "rol —y entonces la reciben todos los agentes del rol, que es el punto—, "
        "o el roster deja de concederla."
    )


def test_the_writing_tools_named_by_the_criterion_exist() -> None:
    """El criterio nombra slugs; si el catálogo los renombra, deja de proteger.

    Un frozenset de strings que ya no casa con nada pasa todos los tests de
    arriba en verde y no impide absolutamente nada — la forma más silenciosa que
    tiene una guarda de dejar de existir.
    """
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    catalogo = {t.slug for t in BUILTIN_TOOLS}
    huerfanas = sorted(WORKSPACE_MUTATING_TOOLS - catalogo)
    assert not huerfanas, (
        f"WORKSPACE_MUTATING_TOOLS nombra slugs que no están en el catálogo: {huerfanas}"
    )


# ---------------------------------------------------------------------------
# 2. La trampa, por su forma exacta
# ---------------------------------------------------------------------------
def test_the_wrong_door_is_never_the_only_door() -> None:
    """`shell_exec` a solas, para quien necesita el toolchain, ES la trampa.

    Las dos puertas comparten `allowed_commands`, así que `shell_exec` acepta el
    `composer install` y devuelve un «not found» del SO indistinguible de un
    problema de PATH. Dárselo sin `stack_exec` a quien tiene que instalar o
    compilar es garantizar las 24 llamadas del run que motivó esta guarda.
    """
    trapped = sorted(
        b.slug
        for b in BUILTINS
        if "shell-exec" in b.tools
        and "stack-exec" not in b.tools
        and b.role in ROLES_THAT_EXECUTE_TOOLCHAIN
    )
    assert not trapped, (
        f"agentes con shell_exec como ÚNICA puerta y trabajo de toolchain: {trapped}"
    )


def test_an_agent_left_with_only_the_sandbox_door_is_told_so() -> None:
    """Y si aun así alguien tiene sólo `shell_exec`, el prompt lo advierte.

    Es el único caso en el que un «not found» es la respuesta CORRECTA del
    sistema y no un error a depurar. Si el prompt no lo dice, el agente lo
    depura — que es literalmente lo que pasó.
    """
    for b in BUILTINS:
        if "shell-exec" in b.tools and "stack-exec" not in b.tools:
            assert b.effective[0].endswith(SHELL_ONLY_ES) or SHELL_ONLY_ES in b.effective[0], (
                f"{b.slug}: tiene shell_exec sin stack_exec y su prompt ES no avisa "
                "de que el toolchain no está en su sandbox"
            )
            assert SHELL_ONLY_EN in b.effective[1], (
                f"{b.slug}: idem en el prompt EN (los dos idiomas tienen que decir lo mismo)"
            )


# ---------------------------------------------------------------------------
# 3. Prompt ↔ tool — frases que ORDENAN, atadas a la tool que las hace posibles
# ---------------------------------------------------------------------------
#: ``(fragmento literal en minúsculas, slug de tool, por qué)``.
#:
#: Los fragmentos se copian TAL CUAL de los prompts sembrados, no se inventan:
#: una tabla de patrones «por si acaso» envejece hasta que nadie se fía de ella.
#: `test_every_claim_matches_at_least_one_prompt` impide que sobreviva una
#: entrada que ya no case con nada.
_PROMPT_CLAIMS: tuple[tuple[str, str, str], ...] = (
    # --- ejecutar ---------------------------------------------------------
    ("corre tests", "stack-exec", "Backend Junior: el paso (4) de su procedimiento numerado"),
    ("run tests", "stack-exec", "idem en EN"),
    ("tests de integración", "stack-exec", "Backend Senior: features end-to-end CON tests"),
    ("integration tests", "stack-exec", "idem en EN"),
    ("corres la suite de calidad", "stack-exec", "ci4-backend: 'composer ci' antes de cerrar"),
    ("run the quality suite", "stack-exec", "idem en EN"),
    ("arranque limpio", "stack-exec", "DevOps: arrancar el stack es ejecutar"),
    ("boots clean", "stack-exec", "idem en EN"),
    ("php spark", "stack-exec", "ci4-dba: generar proxies es el CLI del framework"),
    ("composer", "stack-exec", "cualquier prompt que nombre composer ordena instalar/correr"),
    ("phpunit", "stack-exec", "las suites no se comprueban leyéndolas"),
    ("@test-coverage", "stack-exec", "ci4-qa: los scripts composer del proyecto"),
    # --- escribir ---------------------------------------------------------
    ("mantienes una lista viva", "write-file", "Security: /docs/06-runbooks/security.md"),
    ("maintain a living list", "write-file", "idem en EN"),
    ("tu producto es un documento", "write-file", "Researcher: su única razón de ser"),
    ("your product is a document", "write-file", "idem en EN"),
    ("produces un informe", "write-file", "Researcher: el informe es un fichero"),
    ("produce a short report", "write-file", "idem en EN"),
    ("documentas cada gotcha", "write-file", "DevOps: docs/03-guides/gotchas/"),
    ("document every toolchain gotcha", "write-file", "idem en EN"),
    ("escribes los e2e", "write-file", "QA: los specs son ficheros"),
    ("you write the e2es", "write-file", "idem en EN"),
    ("escribe specs playwright", "write-file", "QA E2E Automator: su entregable"),
    ("write deterministic playwright specs", "write-file", "idem en EN"),
    ("entrada de changelog en", "write-file", "Technical Writer: docs/07-changelog/"),
    ("changelog entry at", "write-file", "idem en EN"),
    ("escribes esqueletos", "write-file", "Architect: esqueletos y módulos base"),
    ("you do write skeletons", "write-file", "idem en EN"),
    # --- red --------------------------------------------------------------
    ("url o referencia verificable", "http-get", "Researcher: cada afirmación con cita"),
    ("a url or verifiable reference", "http-get", "idem en EN"),
)


#: Órdenes RETIRADAS de un prompt porque el agente no podía cumplirlas, que se
#: conservan como alambre de tropiezo: si alguien las reescribe, tendrá que darle
#: la tool o mover la entrada de sitio a conciencia.
#:
#: Están separadas de ``_PROMPT_CLAIMS`` porque el test de abajo exige que cada
#: fragmento VIVO case con algún prompt —una entrada que no casa con nada es una
#: guarda que pasa vacía— y estas, por definición, ya no casan con ninguno.
_RETIRED_ORDERS: tuple[tuple[str, str, str], ...] = (
    (
        "@ci completo",
        "stack-exec",
        "ci4-reviewer decía «y el @ci COMPLETO en cada PR» sin poder ejecutar nada; "
        "hoy la suite la corre la plataforma y él lee el <test-report> (ADR 0095)",
    ),
    ("the full @ci", "stack-exec", "idem en EN"),
    (
        "script @quality",
        "stack-exec",
        "ci4-reviewer: cs-fixer + PHPStan + Psalm + phpcpd; los corre la plataforma",
    ),
    ("@quality script", "stack-exec", "idem en EN"),
)


def test_every_prompt_that_orders_an_action_has_the_tool() -> None:
    offenders: list[str] = []
    for b in BUILTINS:
        haystack = (b.persona[0] + "\n" + b.persona[1]).lower()
        for fragment, tool, why in (*_PROMPT_CLAIMS, *_RETIRED_ORDERS):
            if fragment in haystack and tool not in b.tools:
                offenders.append(f"{b.slug}: «{fragment}» exige {tool} ({why})")
    assert not offenders, "prompts que ordenan lo imposible:\n  " + "\n  ".join(sorted(offenders))


def test_every_live_claim_matches_at_least_one_prompt() -> None:
    """Una entrada que ya no casa con nada es una guarda que pasa vacía.

    Sólo se exige a ``_PROMPT_CLAIMS``: ``_RETIRED_ORDERS`` existe justamente
    para frases que ya NO están en ningún prompt.
    """
    corpus = "\n".join(b.persona[0] + "\n" + b.persona[1] for b in BUILTINS).lower()
    dead = sorted({f for f, _tool, _why in _PROMPT_CLAIMS if f not in corpus})
    assert not dead, f"fragmentos que ya no aparecen en ningún prompt built-in: {dead}"


# ---------------------------------------------------------------------------
# 4. Lo que el prompt NOMBRA
# ---------------------------------------------------------------------------
#: Nombres canónicos que un prompt puede mencionar SIN que el agente los tenga
#: asignados, porque el runtime los cablea a TODO agente al margen de
#: `agent_tools` (familias de sistema: memoria y RAG) o porque el nombre aparece
#: como concepto y no como invocación.
_ALWAYS_WIRED = frozenset({"memory_recall", "memory_store", "rag_search", "task_comment"})


def test_a_prompt_never_names_a_tool_its_agent_does_not_have() -> None:
    """Nombrar una tool ausente le quema un turno al modelo.

    Se mira la persona escrita a mano; los bloques generados se comprueban en
    :func:`test_the_generated_guidance_matches_the_tools`, que es más fuerte
    (identidad exacta, no presencia de un nombre).
    """
    offenders: list[str] = []
    for b in BUILTINS:
        owned = {slug.replace("-", "_") for slug in b.tools}
        haystack = (b.persona[0] + "\n" + b.persona[1]).lower()
        for name in sorted(CANONICAL_TOOL_NAMES - _ALWAYS_WIRED):
            if name in haystack and name not in owned:
                offenders.append(f"{b.slug}: el prompt nombra `{name}` y no la tiene")
    assert not offenders, "\n  ".join(["prompts que nombran tools ausentes:", *sorted(offenders)])


def test_the_generated_guidance_matches_the_tools() -> None:
    """El bloque de ejecución que recibe cada agente es el de SUS tools.

    Generado, no tecleado: un párrafo escrito a mano en 21 personas se
    desincroniza a la primera vez que cambia el reparto, y entonces el prompt
    promete una puerta que ya no existe.
    """
    for b in BUILTINS:
        has_stack, has_shell = "stack-exec" in b.tools, "shell-exec" in b.tools
        if has_stack and has_shell:
            expected: tuple[str, str] | None = (BOTH_DOORS_ES, BOTH_DOORS_EN)
        elif has_stack:
            expected = (STACK_ONLY_ES, STACK_ONLY_EN)
        elif has_shell:
            expected = (SHELL_ONLY_ES, SHELL_ONLY_EN)
        else:
            expected = None
        if expected is None:
            for block in (BOTH_DOORS_ES, STACK_ONLY_ES, SHELL_ONLY_ES):
                assert block not in b.effective[0], (
                    f"{b.slug}: sin puertas de ejecución y su prompt habla de ellas"
                )
            continue
        assert expected[0] in b.effective[0], f"{b.slug}: falta la guía de ejecución ES"
        assert expected[1] in b.effective[1], f"{b.slug}: falta la guía de ejecución EN"


def test_the_effective_prompt_fits_the_persona_cap() -> None:
    """Un prompt por encima del tope se ENTREGA TRUNCADO, sin error.

    `agent_persona` corta a :data:`PERSONA_MAX_CHARS` y añade un marcador. Los
    bloques generados se añaden al final, o sea justo donde caería el corte: sin
    esta guarda, engordar una persona borraría en silencio la guía de ejecución
    que el resto del fichero se esfuerza en poner ahí.
    """
    for b in BUILTINS:
        for lang, text in zip(("es", "en"), b.effective, strict=True):
            assert len(text) <= PERSONA_MAX_CHARS, (
                f"{b.slug} [{lang}]: {len(text)} caracteres > {PERSONA_MAX_CHARS}; "
                "se entregaría truncado y el corte se come el final del prompt"
            )


# ---------------------------------------------------------------------------
# 5. Lo mismo, para SKILLS — la mitad que faltaba
# ---------------------------------------------------------------------------
# La auditoría del 2026-08-30 encontró tres huecos que todas las guardas de
# arriba dejaban pasar porque miran TOOLS: el Frontend Developer nombraba
# Next.js, Tailwind, shadcn/ui y TanStack Query sin tener ninguna de las cuatro
# skills; el Technical Writer perdía `changelog-authoring` porque declarar
# `skill_slugs` SUSTITUYE la herencia del rol; y el QA de CodeIgniter mantenía
# «tres suites PHPUnit» sin `php-phpunit`.
#
# Un prompt que nombra una skill que el agente no tiene no falla —por eso no
# aparecía en ningún sitio— pero produce trabajo peor: el modelo cree tener un
# repertorio que no tiene. Es el gemelo silencioso del defecto de las tools.

#: (fragmento LITERAL del prompt, skill sin la cual esa mención es hueca).
#: Cada par se comprobó midiendo la cadena contra el prompt efectivo, no a ojo.
_SKILL_QUE_EL_PROMPT_NOMBRA: tuple[tuple[str, str], ...] = (
    ("Next.js App Router", "nextjs-app-router"),
    ("Tailwind", "tailwind-design"),
    ("shadcn/ui", "shadcn-components"),
    ("TanStack Query", "tanstack-query"),
    ("PHPUnit", "php-phpunit"),
    ("Twig", "twig-templating"),
    ("Doctrine ORM", "doctrine-orm"),
)

#: Agentes que NOMBRAN un stack sin pretender ejecutarlo, con su motivo. La
#: distincion no es cosmetica: sin ella la tabla de arriba obliga a repartir
#: skills a quien solo describe el contexto del proyecto, y una skill de mas
#: ensancha el prompt sin anadir capacidad.
_NOMBRA_PERO_DELEGA: dict[tuple[str, str], str] = {
    ("ci4-pm", "twig-templating"): (
        "Su persona abre nombrando el stack como CONTEXTO — «un equipo de "
        "desarrollo sobre CodeIgniter 4 (con Doctrine ORM via daycry/doctrine, "
        "Twig via daycry/twig...)»— y sigue con «NO escribes ni revisas codigo "
        "a fondo; delegas». Es el encuadre del proyecto, no una promesa suya."
    ),
    ("ci4-pm", "doctrine-orm"): ("Idem: contexto del proyecto, no trabajo del PM."),
    # El Technical Writer abre con la MISMA frase de encuadre que el PM, y por la
    # misma razón: enumera el stack del proyecto para situarse. Lo que hace con
    # Twig y Doctrine es LEERLOS para describirlos —su prompt dice «LEES el
    # código antes de describirlo»— y para eso tiene `codeigniter4-hmvc`, que es
    # la disposición que el framework impone. Darle además las skills de
    # implementación de cada pieza del stack ensancharía su prompt sin añadir
    # capacidad: no escribe plantillas ni mapea entidades.
    ("ci4-tech-writer", "twig-templating"): (
        "Documenta las vistas Twig, no las escribe: su prompt dice expresamente "
        "«no escribes código de aplicación ni tests»."
    ),
    ("ci4-tech-writer", "doctrine-orm"): (
        "Documenta el modelo de datos, no lo mapea. Misma frase de encuadre que "
        "el PM y mismo motivo."
    ),
}


@pytest.mark.parametrize(("fragmento", "skill"), _SKILL_QUE_EL_PROMPT_NOMBRA)
def test_a_prompt_that_names_a_stack_has_its_skill(fragmento: str, skill: str) -> None:
    """Quien NOMBRA un stack en su prompt tiene la skill de ese stack.

    Incompleto por construcción, como los tests 3 y 4: una mención con otra
    redacción se escapa. No se pretende otra cosa — lo que se fija es que los
    casos MEDIDOS no vuelvan, y que añadir un par a la tabla sea barato.
    """
    # Se mira la PERSONA, no el prompt efectivo. El efectivo lleva concatenados
    # bloques generados que comparten los 34 agentes, asi que buscar ahi marcaba
    # media plantilla por una mencion que no es suya — medido, no supuesto.
    huerfanos = sorted(
        b.slug
        for b in BUILTINS
        if any(fragmento.lower() in p.lower() for p in b.persona)
        and skill not in b.skills
        and (b.slug, skill) not in _NOMBRA_PERO_DELEGA
    )
    assert not huerfanos, (
        f"agentes cuyo prompt nombra {fragmento!r} y NO tienen la skill "
        f"{skill!r}: {huerfanos}. O se les concede, o el prompt deja de "
        "prometer un repertorio que no está detrás."
    )


def test_declaring_skills_never_silently_drops_the_roles_own() -> None:
    """Declarar `skill_slugs` SUSTITUYE la herencia; que sea a propósito.

    Es la mecánica exacta que dejó `changelog-authoring` sin llegar a NADIE: el
    Technical Writer listó cinco skills y la sexta —la que su rol sí traía, y la
    que CLAUDE.md exige para cerrar un plan— desapareció sin que nada lo dijera.

    No se prohíbe recortar: se exige que lo recortado esté en la lista de
    excepciones con su motivo, para que un descuido no pase por decisión.
    """
    from api_server.seeds.builtin_role_capabilities import ROLE_DEFAULT_SKILLS

    # (slug del agente, skill del rol que se retira) -> por qué.
    permitido: dict[tuple[str, str], str] = {}

    perdidas = []
    for b in BUILTINS:
        del_rol = set(ROLE_DEFAULT_SKILLS.get(b.role, ()))
        faltan = sorted(s for s in del_rol - b.skills if (b.slug, s) not in permitido)
        if faltan:
            perdidas.append((b.slug, b.role, faltan))

    assert not perdidas, (
        "agentes que pierden en silencio skills que su ROL sí trae, por declarar "
        f"`skill_slugs` sin incluirlas: {perdidas}. Si el recorte es deliberado, "
        "añádelo al dict `permitido` de este test con el motivo escrito; si no, "
        "vuelve a listar la skill en el agente."
    )


# ------------------------------------------------------------------ task_cv_35
# Auditoría 2026-09-01 (F-04): cuatro textos contradecían el ADR 0163 (el `.git`
# del worktree NO está en el sandbox mientras corre el agente) y la retirada de
# `mv` de la base de shell (ADR 0164): prometían «donde ya está el repo», «git
# log/diff» por shell, «mv» como utilidad y «a git worktree» como directorio.

_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "donde ya está el repo",
    "where the repo already is",
    "git log/diff",
    "(ls, cat, grep, mv",
    "(a git worktree)",
)


def test_the_prompts_no_longer_promise_git_or_mv_inside_the_sandbox() -> None:
    import inspect

    from agent_runtime import providers
    from api_server.seeds import builtin_tools, ci4_team, tool_usage_guidance

    corpus = "\n".join(
        [
            ci4_team._CI4_STACK_HYGIENE_ES,
            ci4_team._CI4_STACK_HYGIENE_EN,
            tool_usage_guidance.SHELL_ONLY_ES,
            tool_usage_guidance.SHELL_ONLY_EN,
            providers._DECIDE_SYSTEM,
            inspect.getsource(builtin_tools),
        ]
    )
    found = [phrase for phrase in _FORBIDDEN_PHRASES if phrase in corpus]
    assert not found, f"textos que contradicen el ADR 0163/0164: {found}"
