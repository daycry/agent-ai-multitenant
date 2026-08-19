---
plan_id: gov-01-precedencia-prompts-y-rigor
title: Precedencia normativa, prompts versionados y rigor por tamaño — las seis decisiones del 2026-08-12
status: approved
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 14
estimated_cost_human_eur: 5.600 € – 8.400 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: análisis de AgentShekel/agentic-workflow + seis decisiones del operador (2026-08-12)
docs_language: es
---

# Plan `gov-01` — precedencia, prompts versionados y rigor por tamaño

> **Fuente de verdad del QUÉ y el POR QUÉ:**
> [`2026-08-12-analisis-agentic-workflow.md`](2026-08-12-analisis-agentic-workflow.md),
> §4, donde están las seis preguntas con la respuesta del operador al lado. Este
> documento es el CÓMO. Ante conflicto entre los dos, gana el informe; ante
> conflicto entre ambos y el código ya mergeado, se para y se re-verifica.

## En una frase

Las seis decisiones tienen un hilo común que conviene leer antes que las tareas:
**cinco de las seis eligen medir antes de construir**. Por eso el plan está
ordenado así y no por tamaño — las fases 3 y 4 producen el dato con el que se
decide si las caras merecen la pena.

## El hallazgo que reordena las prioridades

El informe no encontró que nos faltaran ideas: encontró que **tres mecanismos ya
existen y no los consume nadie**.

- `Task.estimated_complexity` (`xs`…`xl`) se calcula desde el planner y **solo lo
  lee el estimador de coste**. Review y aprobación no lo miran jamás.
- El golden-set de evals existe entero —datasets, merge-gate, workflow que se
  dispara al cambiar un prompt— y **corre siempre en `--dry-run`** contra un
  dataset de ceros.
- Hay precedencia escrita en cinco ADR, **ninguna** sobre qué manda cuando un
  plan y un ADR se contradicen.

Por eso la mayor parte de este plan es cableado, no construcción.

## Estado de esta pasada (2026-08-19)

Se cerraron las **tres** casillas que no dependen de una decisión del operador:
`task_gov_01` (fase 0), `task_gov_06` y `task_gov_07` (fase 3). Las ocho
restantes siguen abiertas, así que el plan NO pasa a
`pending_human_validation` y el `status` se queda en `approved` — además,
`marketplace-v2-despliegue` ya está `in_progress` y la regla dura de `CLAUDE.md`
sólo admite una fase activa a la vez.

**Segunda pasada, misma fecha: la fase 1 entera.** `task_gov_02` (historial
versionado del prompt, migración **0143**) y `task_gov_03` (el sello del prompt del
agente dentro de `executions.prompt_version`). Con eso queda **desbloqueada la
fase 2** —el aviso nº2 de más abajo decía que sin versionado una eval que bloquea
no tiene contra qué comparar— y también uno de los dos disparadores escritos de
`task_gov_11`, que se reabre cuando existan `task_gov_02` **y** `task_gov_05`: la
primera mitad ya está. El `status` del plan sigue en `approved`: quedan casillas
abiertas y `marketplace-v2-despliegue` está `in_progress`.

**Los ids de test se renombraron a `auto_govp_*` / `human_govp_*`.** Los
originales (`auto_gov_01_a`…`auto_gov_09_a`, `human_gov_01`…`03`) los usa ya
[`prod-15-gobernanza-roadmap-docs`](prod-15-gobernanza-roadmap-docs.md), y dos
planes con el mismo id de test hacen imposible saber cuál falló al leer un
informe de CI.

## Avisos al implementador (léelos, ahorran horas)

1. **Verifica los números antes de usarlos.** Las citas `fichero:línea` de abajo
   se comprobaron el 2026-08-12; en este repo se mueven cada semana.
2. **La fase 1 desbloquea a la 2.** Sin versionado del prompt, una eval que
   bloquea no tiene a dónde volver: rechaza el cambio pero no puede decir contra
   qué versión comparaba. No las reordenes.
3. **`--dry-run` no es un fallo del workflow, es su rama sin secreto.** Antes de
   «arreglarlo», lee `eval-on-prompt-change.yml:63-94`: la rama existe a
   propósito para que un fork sin credenciales no falle. Lo que falta es la OTRA
   rama, no quitar ésta.
4. Los tests que escribas deben poder **fallar**: rompe la implementación,
   comprueba el rojo, restaura.

---

## Fase 0 — La precedencia normativa (1-2 días)

La más barata de las seis y la única que resuelve un problema que está pasando
**ahora**: durante agosto, un plan pidió tres veces algo que un ADR posterior
había rechazado, y se resolvió a ojo cada vez.

### `task_gov_01` — La regla de precedencia, escrita y con test

- [x] **Título**: Sección de precedencia en `CLAUDE.md` + `rejects:` en el frontmatter de los ADR + guarda de gobernanza
  - ✅ **Cerrada (2026-08-19).** Las tres piezas, y la premisa del recon
    verificada antes de tocar nada: **cero** ADR con `rejects:` y **cero**
    menciones de precedencia en `CLAUDE.md`.
  - **(a)** `CLAUDE.md` §«Qué manda cuando dos documentos se contradicen», con la
    cadena del 2026-08-12, la obligación de actualizarlo en el MISMO commit y el
    precedente Fernet del ADR 0146 citado. `## Sobre el Documento Maestro` remite
    a la sección nueva para que las dos no digan cosas distintas.
  - **(b)** `rejects:` puesto en los **cuatro** ADR cuya relación se verificó
    contra el roadmap, casilla a casilla: **0133**→`task_prod09_12`,
    **0141**→`task_prod08_shared_logging_08` + `task_prod08_metrics_workers_05`,
    **0150**→`task_prod07_09`, **0151**→`task_prod13_15`. Las cinco casillas
    están `[x]` y cerradas en negativo desde antes; este campo sólo hace
    mecánico lo que ya estaba en prosa. El **0117 (b) NO entra**: retiró una
    promesa de `CLAUDE.md` (`task.human_validation_required`), no una casilla, y
    `rejects:` apunta a casillas. Y el renderer de ADR
    (`tech_writer/adr.py`) aprende el campo, de modo que un ADR nuevo puede
    nacer con él — se omite entero cuando está vacío, como las secciones de cola.
  - **(c)** `tests/docs/test_adr_precedence.py` (8): existencia del id, casilla
    `[x]`, y **cita de vuelta** del documento rechazado al ADR — sin esa tercera
    regla el campo sería una anotación de un solo lado y el implementador que
    abre el plan seguiría sin enterarse.
  - **Rojo verificado**, como pedía el enunciado: inventando en el 0150 un
    `rejects: [task_prod07_09, task_gov_02, task_que_no_existe_9999]` caen los
    **tres** tests (referencia muerta, casilla abierta y falta de cita de vuelta).
    También se rompió el renderer emitiendo `rejects: []` siempre → 2 rojos.
  - **Hallazgo lateral** (no arreglado, no es este carril): los ADR **0107** y
    **0108** tienen frontmatter que **PyYAML no carga** — `related: [hallazgo #11
(…), ADR 0072]`, donde el `#` abre un comentario dentro de una secuencia de
    flujo. Por eso el parseo del `rejects:` es un escáner de líneas y no
    `yaml.safe_load`: si dependiera de PyYAML, un `rejects:` en cualquiera de
    esos dos sería invisible y la guarda pasaría en verde ignorando el fichero roto.
- **Tiempo**: 1-2 días · **Complejidad**: s
- **Descripción**: Tres piezas, y la tercera es la que hace que las dos primeras
  no envejezcan.

  **(a) El orden, en `CLAUDE.md`.** Decisión del operador del 2026-08-12:
  `.docx` maestro > `CLAUDE.md` > decisión escrita del operador > ADR `accepted`
  posterior > plan > código > intuición. Y la regla fina que se firmó:
  **un ADR que contradiga el `CLAUDE.md` está OBLIGADO a actualizarlo en el mismo
  commit**. No gana por ser posterior: gana porque al aceptarse deja el
  `CLAUDE.md` diciendo la verdad. Hay precedente y hay que citarlo — la excepción
  Fernet del [ADR 0146](../05-architecture-decisions/0146-fernet-en-db-vs-vault.md)
  vive en `CLAUDE.md` precisamente porque «una excepción que no consta donde se
  busca no es una excepción».

  **(b) `rejects:` en el frontmatter del ADR.** Lista de `plan_id` o `task_id`
  cuyas casillas quedan invalidadas por esta decisión. Hoy esa relación existe
  —el ADR 0117 (b) retiró `task.human_validation_required`, el 0150 retiró dos
  sub-tareas de `task_prod07_09`— pero vive en prosa, así que solo la encuentra
  quien ya sabe que está. Con el campo, un implementador que abre una casilla
  puede preguntar «¿la rechaza algún ADR?» de forma mecánica.

  **(c) El test.** En `tests/docs/`: todo ADR con `rejects:` apunta a plan_ids o
  task_ids que EXISTEN (si no, es una referencia muerta), y toda casilla nombrada
  en un `rejects:` está marcada `[x]` con su nota de cierre en negativo. Es el
  guard que convierte la regla en algo comprobable en vez de una costumbre.

- **Tests automáticos**:

  ```yaml
  - id: auto_govp_01_a
    runtime: python-pytest
    command: "pytest tests/docs/test_adr_precedence.py -q"
  - id: auto_govp_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_adr_template.py tests/unit/test_docs_governance.py -q"
  ```

  El primero debe fallar de verdad al inventar un `rejects:` que apunte a una
  casilla inexistente Y al apuntar a una casilla abierta.

  Ejecutados el 2026-08-19: `auto_govp_01_a` → **8 passed**; `auto_govp_01_b` →
  **47 passed** (36 del renderer, con los tres casos nuevos de `rejects:`, y 11
  de la guarda documental que vigila que el `CLAUDE.md` editado siga cuadrando
  con el repo).

---

## Fase 1 — Versionar el prompt del agente (2-3 días)

**Habilita la fase 2 entera.** Es la idea nº2 del informe y la única de las cinco
sin decisión pendiente.

### `task_gov_02` — El `system_prompt` deja de reescribirse sin rastro

- [x] **Título**: Historial versionado del prompt del agente, con diff y autor
  - ✅ **Cerrada (2026-08-19).** Premisa re-verificada antes de escribir nada:
    **cero** apariciones de `agent_prompt_versions` en todo el repo (sólo las tres
    del propio roadmap), y `PUT /agents/{id}` seguía siendo
    `apply_partial_update` + `flush_or_conflict` sin fila de versión, sin
    auditoría y sin diff.
  - **Migración 0143** (`20260819_0143_agent_prompt_versions.py`), encadenada a la
    cabeza REAL comprobada con `ls` —`0142_cortex_forget_sweep_index`, no la 0142
    que decía el enunciado por número— con el patrón EXACTO de la 0127:
    `ENABLE` + `FORCE ROW LEVEL SECURITY` + policy `tenant_isolation` sobre
    `app.tenant_id`. Más `UNIQUE (agent_id, version)`, un `CHECK version >= 1` y
    el índice de `tenant_id` que `TenantScopedMixin` declara — sin ese último la
    deriva modelo↔BD habría crecido en silencio a partir de hoy.
  - **Append-only en la capa**, patrón de `db/task_audit_repo.py`:
    `db/agent_prompt_version_repo.py` sólo tiene `record_*` y `list_*`/`latest_*`.
    Y la tabla **no lleva `updated_at`**, que es la señal en el esquema de que ese
    invariante no es una convención opcional. Se comprueba con un test que lee el
    fuente del módulo y su superficie pública, porque en la BD el rol de la
    aplicación **sí** puede hacer UPDATE: el `ALTER DEFAULT PRIVILEGES` de
    producción concede los cuatro verbos a `app_user` sobre toda tabla nueva, y un
    `REVOKE` en la migración lo desharía `workers/restore.py` al restaurar un
    backup. La garantía real es que ningún camino del código lo escriba.
  - **Dos filas en la primera edición, no una.** El enunciado pide «una fila ANTES
    de escribir»; lo que hace falta es que quede registrado el estado ANTERIOR, y
    las dos sentencias van en la misma transacción, así que el orden no lo observa
    nadie —lo que ordena el historial es `version`—. Registrar sólo el estado
    nuevo dejaría la primera edición sin nada contra lo que diffear, que es justo
    cuando alguien abre la pantalla. La fila de base lleva `changed_by` a **NULL**:
    nadie apuntó quién escribió ese prompt, y atribuírselo a quien edita hoy sería
    inventar un autor. `POST /agents` sí escribe su `version 1` con el autor de
    verdad, para que los agentes nuevos no hereden esa laguna.
  - **La detección de cambio va sobre los valores CRUDOS**, y no es un detalle: si
    se comparase el texto efectivo, dos ediciones reales no se registrarían nunca
    —tocar el idioma que la precedencia NO prefiere, y tocar el prompt más allá de
    `PERSONA_MAX_CHARS`—. Las dos dejan el efectivo idéntico. Simétricamente, un
    `PUT` que sube `max_concurrent_tasks` o reenvía el mismo prompto **no** abre
    versión: un historial con filas de diff vacío no se vuelve a abrir.
  - **La mitad de lectura**, que es la que esta base se suele dejar sin hacer
    (`verificar-antes-de-implementar.md` §5): `GET /agents/{id}/prompt-versions`,
    más reciente primero, con el diff calculado al servir por `agent_prompt_diff`
    (puro, sin BD). Se diffea un renderizado canónico de la versión COMPLETA —campo
    plano + una sección por lengua, ORDENADAS— y no el texto efectivo: con el
    efectivo, una edición del idioma no preferido daría una fila con diff vacío,
    o sea una versión que existe y no se puede explicar. Y `404` en lugar de «200
    con lista vacía» para un agente ajeno, indistinguible del inexistente.
  - **Rojos verificados**, uno por decisión: (1) registrar en TODO `PUT` (`if True`)
    → cae `..._does_not_touch_the_prompt_opens_no_version` con 3 filas donde debía
    haber 0; (2) sin fila de base → caen **4** tests (la cadena, el historial, el
    sello y el aislamiento); (3) sin el bloque RLS en la migración → caen **2** de
    `test_rls_invariant.py`, que es la prueba de que la guarda que descubre tablas
    en el catálogo cubre de verdad la nueva; (4) orden de lenguas no determinista
    en el diff → cae la guarda de determinismo.
  - **Inventarios congelados que había que hacer crecer, no romper**: `__all__` del
    dominio (`test_domain_models_package.py`) y el conjunto de rutas del router
    (`test_agents_router_package.py`) eran igualdades contra lo capturado del
    monolito. Meter ahí lo nuevo habría convertido la afirmación en una mentira
    («el monolito lo tenía»), así que se han partido en
    `*_BEFORE_THE_SPLIT` + `*_ADDED_AFTER_THE_SPLIT`, con la tarea de procedencia
    escrita al lado de cada adición. Lo que la guarda sigue impidiendo es lo que
    importa: que algo desaparezca, o que aparezca sin declararse.
  - **Hallazgo lateral, arreglado**: `test_provider_options_is_still_registered_before_the_agent_id_wildcard`
    nombraba UNA ruta, y la trampa es de la FORMA. Se ha añadido la versión general
    —ninguna ruta literal de un segmento bajo `/agents` puede quedar tras el
    comodín— y su primer borrador **pasaba en verde con la trampa puesta**: agrupaba
    los comodines en un dict por camino, y `/agents/{agent_id}` son TRES rutas
    (GET/PUT/DELETE), así que sólo sobrevivía el DELETE y la comparación de métodos
    salía vacía. Lo destapó el rojo provocado a mano; está escrito en el test.
- **Tiempo**: 2 días · **Complejidad**: m
- **Descripción**: Hoy `PUT /agents/{id}` sobrescribe `system_prompt`
  (`routers/agents/crud.py:225-245`) **sin versión, sin auditoría y sin diff**.
  Si la calidad de un agente cae, no hay forma de saber qué cambió ni de volver.

  Tabla nueva `agent_prompt_versions` con `tenant_id` + RLS `FORCE` (patrón de
  `user_invitations`, migración 0127): `agent_id`, `version`, `system_prompt`,
  `persona`, `changed_by`, `created_at`, y un `parent_version_id` para la cadena.
  El `PUT` inserta una fila ANTES de escribir, y el endpoint de lectura devuelve
  el histórico con el diff calculado.

  Lo que NO se hace aquí: rollback automático. Poder volver es la fase 2.

- **Tests automáticos**:

  ```yaml
  - id: auto_govp_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_agent_prompt_versions.py -q -p no:randomly"
  - id: auto_govp_02_b
    runtime: python-pytest
    command: "pytest tests/integration/test_rls_invariant.py -q -p no:randomly"
  - id: auto_govp_02_c
    runtime: python-pytest
    command: "pytest tests/unit/test_agent_prompt_diff.py tests/unit/test_domain_models_package.py tests/unit/test_agents_router_package.py tests/unit/test_integrity_error_sanitized.py -q"
  ```

  **La carrera entre dos `PUT`, traducida y no filtrada**: los dos calculan el
  mismo `version` y el `UNIQUE` deja fuera al segundo. `record_prompt_change` hace
  su propio `flush` para poder encadenar el `parent_version_id`, así que la
  `IntegrityError` NO pasaba por `flush_or_conflict` y habría salido como **500 con
  el mensaje crudo de PostgreSQL** — que nombra la constraint y trae el `tenant_id`
  en el `DETAIL:`, o sea la fuga exacta que `routers/_integrity.py` existe para
  evitar. Ahora el perdedor recibe un 409 con el código estable
  `concurrent_prompt_edit`. Rojo verificado cambiando el `context` del `except`.

  **`auto_govp_02_b` estaba mal declarado**: `tests/security/test_rls_invariant.py`
  no existe ni existió — el invariante de cobertura RLS vive en
  `tests/integration/`, porque descubre las tablas en el catálogo de PostgreSQL y
  necesita una base. Con el camino viejo el comando habría salido != 0 por «no
  tests found» y la casilla habría afirmado una verificación imposible
  (`tests/unit/test_declared_tests_exist.py`). Corregido al fichero que existe y
  que se ejecutó.

  Ejecutados el 2026-08-19 contra la base `agentic_ola4_l1`: `auto_govp_02_a` →
  **16 passed** (6 de la migración y sus constraints, 5 de la escritura, 4 de la
  lectura, 2 de aislamiento y append-only, 1 del cableado); `auto_govp_02_b` →
  **10 passed**; `auto_govp_02_c` → **40 passed**.

### `task_gov_03` — `prompt_version` sella el prompt del AGENTE, no tres módulos

- [x] **Título**: `executions.prompt_version` incluye la versión del prompt del agente
  - ✅ **Cerrada (2026-08-19).** Premisa verificada: `_PROMPT_MODULES` tenía
    exactamente los tres módulos del runtime (`providers`, `nudges`,
    `review_contract`) y ni un byte del `system_prompt` del agente.
  - **Se entrega con la versión Y el hash**, no sólo con el hash: la 02 salió
    entera, así que el sello es `v{N}:{sha256}` cuando el agente tiene fila de
    historial y `p:{sha256}` cuando no. La diferencia importa — «corrió con este
    texto» frente a «corrió con la versión 7, que firmó tal usuario tal día»— y es
    lo que hace accionable la etiqueta cuando alguien pregunta qué cambió.
  - **La cadena completa**: `dispatch._assemble_run_request` emite
    `agent_prompt_version` **pegado a `agent_persona`** (nunca sin ella: sin
    persona no hay texto que sellar, y el hash del vacío movería la etiqueta de
    todos esos runs sin distinguir nada) → `run_contract.ExecutionRequest` →
    `run_spec` lo pone en el `AGENT_TASK_SPEC` → `__main__.run_task` resuelve el
    sello con `agent_prompt_seal(spec)` → `run_agent(..., agent_seal=…)` →
    `prompt_version(agent_seal)`. Cubre implementador **y** revisor, porque las dos
    ramas pasan por `_assemble_run_request`.
  - **El hash sale del agente VIVO, no de la fila.** Una fila puede estar desfasada
    respecto a un `UPDATE` hecho por fuera de la API, y lo que corrió es el prompt
    que el agente tiene hoy.
  - **Sella el texto EFECTIVO** (`resolve_agent_persona`: es → en → plano, capado a
    `PERSONA_MAX_CHARS`), no `agents.system_prompt`. Sellar el campo plano haría
    idénticos a dos agentes con el mismo plano y distinto `system_prompts.es`,
    cuando al modelo le llegan personas distintas; y sellar sin capar afirmaría una
    diferencia que el modelo no vio.
  - **El sello se calcula en DOS imágenes que no se pueden importar la una a la
    otra** —api-server (`agent_persona.prompt_text_hash`) y agent-runtime
    (`prompt_version.agent_prompt_seal`, para el agente sin historial, que es el
    caso mayoritario el primer día)—. Es la forma clásica de que dos mitades se
    desincronicen sin que nada falle: el mismo prompt daría dos etiquetas según por
    qué rama entró. `tests/unit/test_agent_prompt_seal_contract.py` es el único
    sitio del repo donde se comparan, y fija además la FÓRMULA (sha256 desnudo del
    texto), porque cambiar las dos a la vez dejaría la comparación en verde
    rompiendo el histórico entero.
  - **Sin sello, la etiqueta es la de siempre byte a byte.** Es lo que impide que
    el arreglo parta el eje del dashboard en dos y deje la métrica peor que antes.
  - **Rojos verificados**: (1) ignorar el sello en el digest → caen los dos tests
    de la propiedad; (2) hashear el campo plano en vez del efectivo → caen **5**
    del contrato entre imágenes; (3) aceptar `version: true` (`bool` es subclase de
    `int`, y un spec mal formado ataría el run a una versión que no existe) → cae
    su test; (4) `agent_seal=None` en el entrypoint y (5) borrar la clave del
    dispatch → caen las guardas de cableado de los dos lados.
- **Tiempo**: 1 día · **Complejidad**: s
- **Descripción**: Hoy `_PROMPT_MODULES`
  (`agent_runtime/prompt_version.py:33-37`) hashea `providers`, `nudges` y
  `review_contract` — el andamiaje del runtime. **Ni un byte del `system_prompt`
  del agente.** O sea que dos runs con el mismo `prompt_version` pueden haber
  corrido con personas completamente distintas, y la etiqueta que existe para
  atribuir un cambio de comportamiento **no puede atribuir nada**.

  El dispatch pasa la versión de `task_gov_02` en el `AGENT_TASK_SPEC` y el
  runtime la mezcla en el hash. Un test debe fijar que **cambiar el
  `system_prompt` cambia el `prompt_version`** — es la propiedad entera.

- **Tests automáticos**:

  ```yaml
  - id: auto_govp_03_a
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests/test_prompt_version.py -q"
  - id: auto_govp_03_b
    runtime: python-pytest
    command: "pytest tests/unit/test_agent_prompt_seal_contract.py -q"
  - id: auto_govp_03_c
    runtime: python-pytest
    command: "pytest tests/integration/test_dispatch_agent_persona.py -q -p no:randomly"
  ```

  Tres y no uno porque las tres piezas se rompen por separado: el runtime, el
  contrato entre las dos imágenes, y el despacho que lo emite. Ejecutados el
  2026-08-19: `auto_govp_03_a` → **17 passed** (los 10 de `task_wf_52` intactos + 7
  nuevos, incluido el que lee el entrypoint para comprobar que llama de verdad);
  `auto_govp_03_b` → **16 passed**; `auto_govp_03_c` → **4 passed** contra
  `agentic_ola4_l1` (los 2 de la persona, que siguen verdes, + 2 del sello sobre el
  payload que sale a la cola).

---

## Fase 2 — Que las evals dejen de ser decorado (3-4 días)

Decisión del operador: **bloquean, pero solo en `production` y
`customer-external`**. En desarrollo y sandbox avisan y dejan guardar.

### `task_gov_04` — El gate de evals corre de verdad

- [ ] **Título**: La rama con secreto de `eval-on-prompt-change.yml`, contra un dataset real
- **Tiempo**: 1-2 días · **Complejidad**: m
- **Descripción**: El workflow existe y toma siempre la rama `--dry-run`, con sus
  tres argumentos apuntando a variables no definidas y UUIDs todo-ceros
  (`.github/workflows/eval-on-prompt-change.yml:80-94`). Y vigila **dos ficheros
  del repo**, no la ruta por la que un tenant edita un prompt de verdad.

  Aquí: sembrar un dataset golden real, definir las variables, y que la rama con
  secreto ejecute la evaluación. **No quites la rama `--dry-run`**: existe para
  que un fork sin credenciales no falle, y eso sigue siendo correcto.

  #### Cerrado el 2026-08-19: el defecto que hacía inútil esta casilla

  El reconocimiento encontró que la casilla **no podía funcionar aunque el
  operador pusiera el secreto**, y no por lo que dice el enunciado. Dos defectos
  que se tapaban entre sí:

  1. **El gate mentía.** `ci_run.main` devolvía **PASS** cuando
     `diff_provider is None`, con el mensaje «no live diff provider available —
     nothing to gate, treating as pass». Y **ningún** sitio del repo cablea un
     productor de diff vivo (`DiffProvider` sólo se inyecta desde los tests), así
     que ése era el único camino que la rama con secreto podía tomar: el
     merge-gate de regresión era estructuralmente incapaz de bloquear nada, **y
     lo decía en verde**. Peor que no tener gate.
  2. **Los argumentos no existían.** La rama viva interpolaba
     `${EVAL_GOLDEN_DATASET}` y `${EVAL_BASELINE_RUN}` sin definirlas en ningún
     `env:` —se expandían a la cadena vacía, que `required=True` de argparse
     acepta— y el agente salía de un default inventado
     `:-changed-prompt-agent`.

  **Cómo se resolvió el choque con el ADR.** El [ADR
  0038](../05-architecture-decisions/0038-evals-continuos-llm-as-judge-golden-promote-merge-gate-shadow-cross-tenant.md)
  (`accepted`) descarta expresamente el fail-closed **incondicional** («fallar si
  no hay proveedor… bloquear todo merge sería inviable»), así que por la cadena de
  precedencia de `CLAUDE.md` no se podía hacer que la ausencia de proveedor
  fallase a secas. Pero el mismo ADR enumera las salidas del gate y **no incluye**
  «sin proveedor, sin `--dry-run`, exit 0»: eso lo inventó el código, y el código
  no gana por estar desplegado.

  La frontera correcta no es «con o sin proveedor», es **qué afirma el
  invocante** — y por eso hay ahora un tercer estado en vez de dos:

  | Invocación                | Afirma                         | Salida            |
  | ------------------------- | ------------------------------ | ----------------- |
  | `--dry-run`               | «no vengo a gatear»            | `0` PASSED        |
  | sin `--dry-run`, con diff | «medí, y este es el veredicto» | `0` / `1` BLOCKED |
  | sin `--dry-run`, sin diff | «vengo a gatear» — y no pudo   | `2` INCONCLUSIVE  |

  El `2` es no-cero (no puede leerse como aprobado) **y** distinto del `1` a
  propósito: las dos situaciones piden arreglos distintos de quien lee el check
  —«el prompt empeoró» vs «el gate está mal configurado»—. Un fail-closed que
  reusara el `1` diría «regresión» de algo que nunca se evaluó.

  **Lo que se entrega:**

  - `GateOutcome` (PASSED / BLOCKED / INCONCLUSIVE) + `EXIT_GATE_INCONCLUSIVE` +
    `inconclusive_gate()` en
    [`evals/ci_run.py`](../../apps/api-server/src/api_server/evals/ci_run.py);
    `GateDecision.blocked` pasa a propiedad derivada del `outcome`, así que nadie
    puede construir una decisión cuyo bit de bloqueo contradiga su estado.
  - Los tres argumentos requeridos rechazan un valor **en blanco** al parsear
    (`_non_blank`), que es la forma en que llegaba una variable de CI sin definir.
  - El workflow: `continue-on-error: false` **explícito** (mismo criterio que el
    SCA de `ci.yml` — el modo del job a la vista, y volver a informe tiene que
    ser una decisión escrita); las tres variables se leen de `vars.*` **sin
    default**; y la detección exige **secreto Y configuración**, de modo que quien
    dé de alta la credencial sin sembrar el dataset cae en la rama `--dry-run` con
    un `::warning::` que **nombra las variables que faltan** en vez de encontrarse
    el gate en rojo sin saber por qué.
  - La rama `--dry-run` sigue donde estaba, con placeholders **con nombre** en vez
    de UUIDs todo-ceros: un `00000000-…` se lee como un id real y sugiere que el
    gate corrió contra algo.
  - Se **reescribió** `test_cli_without_provider_does_not_block` de
    [`tests/integration/test_regression_block.py`](../../tests/integration/test_regression_block.py),
    que afirmaba el defecto («no regression signal to act on -> do NOT block») y
    por tanto lo protegía de este arreglo. Trampa nº2 de
    [verificar-antes-de-implementar.md](../03-guides/verificar-antes-de-implementar.md).

  #### Lo que sigue pendiente (y de quién es)

  **Del operador**, y esta casilla no se marca hasta entonces:

  1. Dar de alta el secreto del proveedor (cualquiera de los cuatro caminos del
     ADR 0021) en los secretos del repositorio.
  2. Sembrar un **dataset golden real** y un run **baseline**, promocionando
     tareas aprobadas (`POST /tasks/{id}/promote-to-dataset`).
  3. Definir las tres **variables de repositorio** (Settings → Variables, no
     secretos: son ids): `EVAL_SUBJECT_AGENT`, `EVAL_GOLDEN_DATASET`,
     `EVAL_BASELINE_RUN`.
  4. Añadir el check a los _required status checks_ de la protección de rama. El
     nombre exacto es el `name:` del job — «Eval prompt change (regression
     gate)» —, no su id; con el id la protección no casa nunca y queda de adorno.
     Misma trampa que documenta el SCA en `ci.yml`.

  **De código, y todavía sin hacer**: cablear el **productor de diff vivo** —
  construir el run candidato con el prompt nuevo vía el motor de juez y diffearlo
  contra el baseline. Necesita sesión tenant-bound + proveedor LLM, que es
  justamente por lo que el seam `DiffProvider` está inyectado. **Mientras falte,
  la rama viva sale en `2` INCONCLUSIVE**, que es la verdad: ya no finge un
  verde. Ese es el trabajo que queda de esta casilla, y ahora es visible en vez
  de estar tapado por un PASS.

  Lo que el enunciado señala y sigue igual de cierto: el workflow vigila **dos
  ficheros del repo**, no la ruta por la que un tenant edita un prompt de verdad.
  Esa mitad la cubre `task_gov_05` (`PUT /agents/{id}`).

- **Tests automáticos**:

  ```yaml
  - id: auto_govp_04_a
    runtime: python-pytest
    command: "pytest tests/docs/test_supply_chain_docs.py tests/unit/test_eval_gate_config.py -q"
  - id: auto_govp_04_b
    runtime: python-pytest
    command: "pytest tests/integration/test_regression_block.py tests/unit/test_ci_eval_gate.py -q -p no:randomly"
  ```

  `tests/unit/test_eval_gate_config.py` **no existía** cuando la casilla lo
  declaró: el comando de arriba nombraba un fichero inexistente y no podía haber
  pasado nunca. Existe desde el 2026-08-19, con las guardas del tercer estado y
  las del modo del job.

### `task_gov_05` — La eval bloquea al editar un prompt, según el preset

- [ ] **Título**: `PUT /agents/{id}` corre la eval y bloquea en `production` / `customer-external`
- **Tiempo**: 2 días · **Complejidad**: m
- **Descripción**: Al cambiar `system_prompt`, se lanza la eval contra el golden
  set. En un proyecto `production` o `customer-external`, un resultado peor que
  el umbral **rechaza la escritura** con el detalle de qué escenarios empeoraron;
  en `development` y `sandbox` se guarda y se avisa.

  Dos cosas que no son opcionales: (1) **el mensaje dice qué empeoró**, no «la
  eval falló» — un rechazo mudo se salta desactivando la feature; (2) hay una
  válvula de escape documentada para el caso de eval caída, porque un tenant-admin
  bloqueado por una infraestructura que no responde es una llamada de soporte y
  un incentivo a apagar el gate.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_prompt_edit_eval_gate.py -q -p no:randomly"
  ```
  Nodos irrenunciables: bloquea en `production`, NO bloquea en `development`, y
  el mensaje de rechazo nombra los escenarios que empeoraron.

---

## Fase 3 — Medir antes de construir (2-3 días)

Las dos tareas que el operador prefirió a sus alternativas caras. Producen el
dato con el que se decidirá si aquéllas merecen la pena.

### `task_gov_06` — Detector de Goodhart: ¿el revisor juzga o repite?

- [x] **Título**: Medir cuánto se parece el veredicto del revisor al relato del implementador
  - ✅ **Cerrada (2026-08-19).** Premisa verificada contra el código antes de
    medir nada: `_format_prior_outputs` + `_REVIEW_PRIOR_OUTPUTS = 3` en
    `orchestrator/dispatch.py` — el revisor recibe los tres últimos intentos y,
    cuando sólo hay uno, **verbatim**; y `_build_review_request` resuelve el
    modelo con el MISMO `_resolve_model_spec` que el implementador.
  - **La métrica**: `api_server/review_contamination.py`, pura y determinista
    (sin reloj, sin red, sin LLM). Tres números por review: `phrase_overlap`
    (contención **dirigida** revisor→autor sobre 5-gramas), `verbatim_share`
    (superficie del veredicto cubierta por tiradas literales de 12 tokens
    compartidas) y `echoed_conclusion` (el veredicto frente al `finish_status`
    que el propio autor se puso; `None` cuando no se autoevaluó — un dato
    ausente no es un dato negativo y sesgaría la media de la semana). Lleva
    `METRIC_VERSION` para que un agregado de meses no mezcle dos fórmulas: es
    el fallo que ya se pagó con `EvalRun.subject_prompt_version`.
  - **Cableada**, que es la mitad que esta base se suele dejar
    (`verificar-antes-de-implementar.md` §5): `workers/execution.py` la calcula
    en la rama `if request.review:` con el veredicto YA aplicado y deja un
    evento de auditoría `review_contamination` + una línea structlog que va a
    Loki, que es por donde se leerá la ventana de `human_govp_03` sin escribir
    SQL. `kind` propio y no un campo dentro de `review_comment`: un APPROVE sin
    desglose de criterios no emite `review_comment`, así que colgarlo de ahí
    perdería la métrica en la mitad de los casos que interesa medir. Best-effort
    en SAVEPOINT, como `_persist_guardrail_events`.
  - **Un detalle que sólo aparece leyendo el ciclo real**: el «relato del autor»
    excluye la ejecución del propio revisor **y** las de su mismo `agent_id` —
    un review no concluyente se re-despacha (ADR 0095 D3), así que la ejecución
    anterior puede ser otra pasada del revisor y compararlo consigo mismo daría
    contaminación altísima por construcción.
  - **Rojos verificados**: (1) cambiar la contención por un Jaccard simétrico →
    caen `test_containment_is_directional_not_symmetric` y
    `test_copying_the_author_verbatim_scores_high`; (2) tokenizar con `\S+` (sin
    descartar el andamiaje markdown) → cae
    `test_markdown_scaffolding_is_invisible_to_the_metric`; (3) borrar la llamada
    del worker → caen las dos guardas de cableado. La guarda de cableado se
    endureció **por ese tercer rojo**: contar apariciones del nombre daba verde
    con la llamada borrada (el comentario y la `async def` ya suman dos), así que
    busca la invocación `await …(`.
- **Tiempo**: 1-2 días · **Complejidad**: s
- **Descripción**: Hoy el revisor recibe los tres últimos intentos del
  implementador, **el último verbatim** (`dispatch.py:182-206`), y resuelve el
  mismo modelo que él. O sea que hereda su encuadre entero antes de opinar.

  En vez de pagar la pasada ciega a ciegas (4-6 días y un ADR), **medir primero**:
  una métrica por review que compare el veredicto con el relato del autor —
  solapamiento de n-gramas, coincidencia de conclusiones, reutilización literal
  de frases. Cero tokens extra: es post-proceso de texto que ya existe.

  Si el número sale alto, la pasada ciega queda justificada con evidencia. Si
  sale bajo, nos hemos ahorrado duplicar el coste de review. **El resultado de
  esta tarea es un dato, no una feature**, y así hay que leerlo.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_review_contamination_metric.py -q"
  ```
  Ejecutado el 2026-08-19: **21 passed** (18 de la métrica + 3 guardas de que el
  worker la llama de verdad, en la rama de review y con su `kind` propio).

### `task_gov_07` — Aviso de linaje compartido entre autor y revisor

- [x] **Título**: El Hub de Capacidad avisa cuando implementador y revisor son de la misma familia
  - ✅ **Cerrada (2026-08-19).** Confirmado lo que el enunciado ya avisaba: el
    mapa `KIND_TO_LITELLM_FAMILIES` existe y **no se ha duplicado** —
    `capabilities.model_families()` lo lee de `pricing/litellm_sync.py`, con un
    test que lo compara entrada por entrada contra el mapa vivo. Y `model_origin`
    ya se resolvía, serializaba y pintaba: se ha reutilizado, no rehecho.
  - **Backend**: `shared_lineage_warning()` compara **familias**, no proveedores
    —`claude_sdk` y `copilot` son entradas distintas del catálogo y el segundo
    sirve modelos de Anthropic, así que un «¿son proveedores distintos?» ingenuo
    daría por bueno el peor caso—, y el endpoint lo emite con el `code` estable
    `shared_model_lineage`, bilingüe como el resto de avisos del Hub.
  - **Quién es «el revisor»**: el agente de rol `reviewer` del equipo del
    proyecto, la MISMA fuente que usa el planner al materializar tareas
    (`sync_to_kanban._resolve_assignment`). No `tasks.reviewer_agent_id`: el Hub
    es una vista por agente, no por tarea, y preguntarle a una tarea concreta
    ataría el aviso al azar de cuál se mirase.
  - **Se comparan los proveedores EFECTIVOS**, resueltos por la misma
    `resolve_model_config_chain` que el dispatch, default de plataforma incluido.
    Comparar los `model_config` crudos daría «no comparten linaje» para dos
    agentes que en realidad heredan los dos el mismo default, que es el caso más
    común de todos.
  - **Frontend**: `sharedLineageNotice()` en `lib/capability/hub.ts` + su caja en
    el Hub, emparejando por `code` y nunca por el texto castellano — hacerlo por
    texto ya dejó muerta la rama EN una vez. Tono neutro (`Info`), no de aviso:
    no bloquea nada, y el operador decidió expresamente quedarse en avisar.
  - **Lo que descubrió el código y no la especificación**: `model_config
["provider"]` guarda HOY las dos formas — el catálogo cerrado del ADR 0021 y
    `DEFAULT_MODEL_CONFIG` usan el **kind** (`claude_sdk`), pero los **once**
    agentes built-in se siembran con la **familia** (`anthropic`,
    `seeds/builtin_agents.py`). Un resolutor que entendiera sólo una de las dos
    daría «sin linaje compartido» justo para los equipos built-in, que son los
    que más lo comparten. Se aceptan las dos, y hay test para cada una.
  - **Rojo verificado**: quitando el bloque de render del Hub caen los dos tests
    de pintado (es/en) y sobreviven los del selector — o sea que las dos mitades
    se comprueban por separado.
  - **Regresión**: `pytest tests/integration/test_capabilities_endpoint.py -q -p
no:randomly` → **7 passed** contra la base `agentic_ola3_l3`, y los tests
    vivos del Hub (`i18n.test.tsx` + `capability-hub.test.ts`) siguen verdes.
  - **Corregido el `command:` declarado**: apuntaba a `app/admin/projects`, donde
    no hay ni un `*.test.tsx`, así que `npx vitest run` habría salido != 0 por
    «no test files found». Ahora nombra el fichero que existe y que se ejecutó.
- **Tiempo**: 1 día · **Complejidad**: s
- **Descripción**: El override por proyecto **ya existe**: la cadena
  agente→equipo→proyecto→plataforma (`db/platform_settings.py:1017`) resuelve el
  modelo, y el Hub ya muestra de qué nivel viene. Lo que falta no es poder
  configurarlo: es que el sistema **sepa que compartir linaje importa**.

  Se añade la comparación de familia entre el agente que implementa y el que
  revisa, y un aviso en el Hub cuando coinciden. Ni bloquea ni cambia nada:
  convierte una decisión invisible en visible. El operador decidió expresamente
  quedarse aquí y no exigirlo — un proyecto sin segundo proveedor no puede
  quedarse sin poder cerrar reviews.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_07_a
    runtime: vitest
    command: "npx vitest run components/capability/shared-lineage.test.tsx --reporter=dot"
  - id: auto_govp_07_b
    runtime: python-pytest
    command: "pytest tests/unit/test_shared_model_lineage.py -q"
  ```
  Ejecutados el 2026-08-19 (el de vitest, desde `apps/admin-panel/`):
  `auto_govp_07_a` → **9 passed** (6 del selector + 3 de que el Hub lo pinta);
  `auto_govp_07_b` → **14 passed**.

---

## Fase 4 — El rigor se adapta al tamaño (4-6 días)

Decisión del operador: **sí, pero solo las pasadas de review**. La validación
humana al cierre del plan NO se toca — sigue siendo del operador siempre.

### `task_gov_08` — ADR del rigor por niveles

- [ ] **Título**: ADR que fija qué cambia en cada nivel y quién clasifica
- **Tiempo**: 4 h · **Complejidad**: s
- **Descripción**: Nace `accepted` con la decisión del 2026-08-12 escrita
  —incluida su frontera: **la validación humana no participa**—, y con lo que
  queda por resolver como parte del ADR: quién clasifica (hoy el planner emite
  `estimated_complexity` y nadie lo audita), si el operador puede promover un
  nivel a mano, y qué pasa cuando la clasificación no existe (el fallback debe
  ser el nivel ALTO: un cambio sin clasificar no es un cambio pequeño).
- **Tests automáticos**:
  ```yaml
  - id: auto_govp_08_a
    runtime: python-pytest
    command: "pytest tests/docs/test_docs_internal_links.py -q"
  ```

### `task_gov_09` — `estimated_complexity` gobierna las pasadas de review

- [ ] **Título**: El número de pasadas de review depende del nivel del cambio
- **Tiempo**: 4-5 días · **Complejidad**: l
- **Descripción**: `Task.estimated_complexity` (`xs`…`xl`) se calcula en el
  planner (`sync_to_kanban.py:477`) y hoy solo alimenta al estimador de coste.
  Aquí pasa a decidir cuántas pasadas de review lleva la tarea.

  Tres cosas que definen si esto sale bien:
  1. **Fallback al nivel alto.** Sin clasificación, rigor máximo. Lo contrario
     convierte un fallo del planner en una puerta abierta.
  2. **El nivel se registra en la ejecución**, no se recalcula al leer. Si el
     planner cambia de criterio, los runs viejos deben seguir explicando por qué
     tuvieron el rigor que tuvieron.
  3. **La validación humana no se toca.** Está escrito en el ADR y hay que
     comprobarlo con un test: un plan `xs` sigue exigiendo la firma del operador
     al cierre.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_09_a
    runtime: python-pytest
    command: "pytest tests/integration/test_review_passes_by_tier.py -q -p no:randomly"
  ```
  Nodo irrenunciable: una tarea SIN `estimated_complexity` recibe el rigor máximo.

---

## Fase 5 — El dato que hace posible aprender (2-3 días)

### `task_gov_10` — Reflexión estructurada del rechazo

- [x] **Título**: El rechazo se registra como `target` × `class`, no como prosa
  - ✅ **Cerrada (2026-08-19).** Verificado antes de tocar nada: el veredicto era
    prosa y **sólo** prosa (`failed_criterion` / `testreport_evidence` /
    `what_to_fix` en el payload del evento `review_comment`,
    `reviewer_bridge.py:277-295` antes del cambio), sin un solo campo agregable.
  - **(a) El vocabulario, en `shared-domain`**:
    [`reject_taxonomy.py`](../../packages/shared-domain/src/shared_domain/reject_taxonomy.py)
    declara `RejectTarget` (`code`/`tests`/`scope`/`deliverable`) y `RejectClass`
    (`incorrect`/`incomplete`/`unproven`/`regression`/`contract_drift`/`overreach`),
    el tope de tres por eje y `GENERIC_LABELS`. **No hay bucket «otros»**: lo
    genérico se descarta y el rechazo queda sin clasificar, que es lo que pedía
    el enunciado. Y no hay sinónimos interpretativos (`bug` NO es `incorrect`):
    se perdona la FORMA, no se adivina el VALOR.
  - **(b) Anuncio y parseo, una sola cadena**: los tags y la instrucción salen
    del mismo módulo, así que el prompt del reviewer no puede ofrecer un valor
    que el parser tire. Interpolado en los **tres** sitios que piden el bloque
    `<rejection>`: el preámbulo de todo run de review
    (`agent_runtime/__main__.py`), el system del run reviewer
    (`agent_runtime/providers.py`) y el prompt semilla del agente `reviewer`
    (`seeds/builtin_agents.py`, ES + EN). `reviewer_bridge` construye sus regex
    desde los mismos tags. Es la lección de dos incidentes: el `<verdict>`
    deletreado a mano en cinco prompts (H3) y las 13 categorías de aprobación
    que no intersecaban con ninguna política (g6).
  - **(c) Dónde se escribe**: `apply_reviewer_verdict` añade `reject_targets` /
    `reject_classes` al payload JSONB del evento `review_comment` que ya lleva la
    prosa. **Sin migración y sin CHECK, a propósito**: el par no aterriza en una
    columna, y el precedente de esta casa para un value-set cerrado que vive en
    JSONB es `shared_domain.approval_categories` (13 categorías, cero CHECK, test
    de contrato). El cierre lo garantizan el escritor (`normalise_*`) y la
    lectura (`label = ANY(:allowed)`).
  - **(d) Que agregue de verdad**:
    [`db/reject_taxonomy_repo.py`](../../apps/api-server/src/api_server/db/reject_taxonomy_repo.py)
    contesta «¿qué se rechaza más?» y «¿qué clase domina?» por tenant y por
    proyecto — más `unlabelled`, la cobertura del dato, sin la cual las otras dos
    engañan. Sin este lado sería el patrón de siempre: mecanismo entregado, cero
    llamantes.
  - **Rojo verificado** (cinco roturas, con el test que cayó cada vez): añadir
    `OTHER = "other"` al enum → 3 rojos (`test_classes_are_closed_and_short`,
    `test_neither_axis_has_a_generic_bucket`,
    `test_the_prompt_advertises_exactly_the_parsed_vocabulary`); subir el tope a
    99 → `test_the_cap_is_three_per_axis`; re-teclear la instrucción a mano en el
    runtime → 5 rojos, incluido el del prompt semilla; que el parser deje de leer
    `<reject_target>` → 4 rojos; que el payload deje de llevar el par → 6 rojos
    del test de integración; y quitar el filtro de tenant y el `ANY(:allowed)` de
    la lectura → `test_the_breakdown_does_not_cross_tenants` +
    `test_a_label_outside_the_vocabulary_is_not_counted`.
  - **Hallazgo propio al romper**: el descarte explícito de `GENERIC_LABELS` era
    una guarda que **no podía fallar** (un `other` se cae igual por no estar en el
    enum), así que se añadió el test que sí la ejerce —un alias genérico metido
    por descuido— en vez de dejar un filtro decorativo.
  - **Gotcha de Postgres, medido**: `CAST(:x AS uuid)` y no `:x::uuid` — el regex
    de bind params de `text()` no reconoce un parámetro seguido de `::`, y un
    `$n IS NULL` sin tipo es `AmbiguousParameterError` aunque el mismo parámetro
    aparezca luego en una comparación tipada. Documentado en
    [`gotchas/postgres-parametro-opcional-sin-tipo-en-text.md`](../03-guides/gotchas/postgres-parametro-opcional-sin-tipo-en-text.md).
  - ⚠️ **Divergencia resuelta con el informe, y dicha aquí para que no se
    resuelva a ojo la próxima vez.** El informe
    [§2.4](2026-08-12-analisis-agentic-workflow.md) proponía los mismos nombres
    con otro significado: `target` como PUNTERO (`skills/X` | `agents/Y`) y
    `class` como `rule_missing | rule_wrong | rule_ignored` — o sea la mitad
    barata de **SkillOpt**. Se ha implementado la versión de ESTA casilla (los
    ejes describen el trabajo rechazado) por dos razones: (1) §4 del informe, que
    es la parte que este plan declara fuente de verdad, **no se pronuncia** sobre
    los ejes (la fila 4 de su tabla dice «¿Decide el operador? No»); y (2) la
    variante del informe alimenta un bucle que el operador **aplazó** (decisión 6,
    `task_gov_11`), mientras que el enunciado de aquí exige explícitamente un dato
    que «sirve por sí solo aunque ese bucle no llegue nunca». Queda anotado en
    `reject_taxonomy.py` (§«Qué NO es esto») que la reflexión de SkillOpt, si se
    reabre, es un par **aditivo** y no una redefinición de estos ejes: `code x
incorrect` no dice qué regla le falta al reviewer de CI4. **Si el operador
    prefiere la lectura del informe, el cambio es barato**: la maquinaria
    (normalización, tope, descarte, tags, punto de escritura, agregado) se
    reutiliza entera y sólo cambia el vocabulario.
- **Tiempo**: 2-3 días · **Complejidad**: m
- **Descripción**: Hoy un rechazo se memoriza y se reinyecta como texto. Sirve
  para el reintento inmediato y **no agrega**: no se puede preguntar «¿por qué se
  rechaza más en este proyecto?».

  El veredicto pasa a llevar, además de la prosa, un par acotado: `target` (qué
  se rechaza: el código, los tests, el alcance, el formato del entregable) y
  `class` (por qué). Vocabulario CERRADO y corto, con tope de tres por veredicto,
  y lo genérico se descarta en vez de guardarse — una etiqueta «otros» que se
  lleva el 60 % no informa de nada.

  Es el dato sin el cual ningún bucle de mejora es posible, y sirve por sí solo
  aunque ese bucle no llegue nunca.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_reject_taxonomy.py tests/integration/test_review_verdict_shape.py -q -p no:randomly"
  ```

### `task_gov_11` — El disparador de SkillOpt, escrito para que no se olvide

- [ ] **Título**: Nota de decisión aplazada CON su condición de reapertura
- **Tiempo**: 1 h · **Complejidad**: s
- **Descripción**: El operador aplazó SkillOpt —el bucle que convierte rechazos
  repetidos en parches a las instrucciones del agente— **con disparador escrito**,
  no indefinidamente. Queda anotado en el ADR de la fase 4 y en el informe: se
  reabre cuando existan las dos redes que sus propios frenos presuponen y que
  este plan construye — `task_gov_02` (versionado) y `task_gov_05` (evals que
  bloquean).

  Montar el bucle antes que las redes sería lo contrario de lo que hace el
  framework que lo inspira: ellos lo tienen porque PRIMERO tienen el golden-set.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_11_a
    runtime: python-pytest
    command: "pytest tests/docs/ -q"
  ```

---

## Tests humanos del Plan

```yaml
- id: human_govp_01
  title: La precedencia resuelve un caso real
  steps: >-
    Coge uno de los tres casos de agosto en que un plan pedía algo que un ADR
    posterior rechazó (0117 b, 0150, 0141). Comprueba que hoy, con la regla y el
    campo `rejects:`, se resuelve leyendo el frontmatter y sin deliberar.
- id: human_govp_02
  title: Editar un prompt en producción y ver que la eval te para
  steps: >-
    En un proyecto `production`, edita el `system_prompt` de un agente
    empeorándolo a propósito. Debe rechazarse Y decir QUÉ escenarios empeoraron.
    Repite en un proyecto `development`: debe guardar y avisar.
- id: human_govp_03
  title: El número del detector de Goodhart, leído
  steps: >-
    Tras una semana de runs, mira la métrica de contaminación. La DECISIÓN de si
    se implementa la pasada ciega se toma con ese número delante — es el objetivo
    entero de la fase 3.
```

## Criterios de cierre

1. Las once casillas marcadas `[x]` con sus tests en verde.
2. Los tres tests humanos validados por el operador.
3. Entrada en `docs/07-changelog/gov-01-precedencia-prompts-y-rigor.md`.
4. PR mergeado a `master`.

## Lo que este plan NO hace, y por qué

- **No implementa la pasada ciega del revisor.** Se mide primero (fase 3). Si el
  dato la justifica, será su propio plan con su ADR.
- **No implementa SkillOpt.** Aplazado con disparador escrito (`task_gov_11`).
- **No toca la validación humana.** El rigor por niveles se queda en las pasadas
  de review por decisión expresa del operador.
- **No adopta el consilium de cinco revisores** ni el puente MCP a Codex: Azure
  Foundry ya nos da linaje OpenAI dentro del catálogo cerrado del ADR 0021.
