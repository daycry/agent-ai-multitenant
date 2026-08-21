---
title: "ADR 0158: SkillOpt aplazado con disparador escrito — y el disparador ya saltó"
status: accepted
date: 2026-08-12
deciders: [operador]
relates_to: [0021, 0038, 0124]
plan_referenced: gov-01-precedencia-prompts-y-rigor
task: [task_gov_11]
# `task_gov_11`: las dos redes cuya ausencia justificaba el aplazamiento. La
# guarda `tests/docs/test_adr_deferrals.py` exige que los ids existan, que citen
# de vuelta a este ADR, y que el disparo conste con fecha en cuanto se cumplen.
reopen_when: [task_gov_02, task_gov_05]
reopen_triggered_on: 2026-08-20
docs_language: es
---

# ADR 0158 — SkillOpt: aplazado con disparador, y el disparador ya saltó

> **Estado: `accepted`.** El operador **aplazó** SkillOpt el **2026-08-12**
> (decisión 6 del informe
> [`2026-08-12-analisis-agentic-workflow.md`](../roadmap/2026-08-12-analisis-agentic-workflow.md)),
> **no indefinidamente**: con la condición escrita de que existieran antes las dos
> redes que sus propios frenos presuponen — `task_gov_02` (versionado del prompt
> del agente) y `task_gov_05` (evals que bloquean de verdad).
>
> ⚠️ **Esa condición se cumplió el 2026-08-20.** Las dos casillas están cerradas.
> Este aplazamiento está **vencido**: la decisión de arriba ya no se sostiene sola
> y hay que **reabrirla**. Reabrirla NO es aprobar SkillOpt — ver §«Qué significa
> exactamente que el disparador haya saltado».
>
> **Quién escribió esto y cuándo.** La decisión es del operador (2026-08-12) y
> constaba sólo en prosa dentro de un informe. Este ADR la traslada al corpus el
> **2026-08-20** al cerrar `task_gov_11`, con su condición convertida en dos
> campos de frontmatter que un test comprueba. No añade ni cambia ninguna
> decisión.

## Contexto

**SkillOpt** es el ciclo completo del framework analizado
([`AgentShekel/agentic-workflow`](../roadmap/2026-08-12-analisis-agentic-workflow.md),
§2.6): agrupar señales de rechazo repetidas, que **un modelo de otra familia
proponga** parches acotados a las instrucciones del agente —4-6 por ciclo, ≤10
líneas cada uno—, que **el director los puertee** contra escenarios golden por
dominio, y que los rechazados caigan en una **memoria negativa** para no volver a
proponerlos. Su invariante: «el director juzga, nunca redacta».

Traducido a esta plataforma: un bucle que, al ver que el reviewer rechaza tres
veces lo mismo, propone y aplica un parche al `system_prompt` (o a la skill) del
agente responsable.

Lo que ya teníamos el 2026-08-12, y es más de lo que parecía:

- **Aprendizaje a partir de resultados, a nivel de memoria**: los fracasos se
  memorizan por defecto, el cierre de un plan destila una retrospectiva
  (ADR [0124](0124-retro-automatica-planes.md)) y los tres `review_comment` más
  frescos se reinyectan en el prompt del re-despacho.
- **Golden datasets, promoción desde tareas aprobadas y un merge-gate**
  (ADR [0038](0038-evals-continuos-llm-as-judge-golden-promote-merge-gate-shadow-cross-tenant.md)).

Lo que NO existía: que nada de eso tocara **el prompt, la persona o la skill** del
agente. Ese es el hueco que SkillOpt viene a llenar.

## Decisión

**No ahora. Aplazado con disparador escrito**, y el disparador son las dos redes
sin las cuales el bucle no tiene frenos:

1. **Versionado del prompt del agente** (`task_gov_02`). Sin él, un parche
   automático que empeore un agente **no tiene a dónde volver**: el `PUT
/agents/{id}` reescribía `system_prompt` sin versión, sin auditoría y sin
   diff, y `executions.prompt_version` sólo hasheaba tres módulos del runtime —
   ni un byte del prompt del agente.
2. **Evals que bloqueen de verdad** (`task_gov_05`). Sin ellas, la regresión que
   debería frenar un parche malo no frena nada: el golden-set existía entero pero
   corría siempre en `--dry-run` contra un dataset de ceros.

### Por qué se aplazó — la razón real, que no es «es caro»

El coste (15-20 días) fue lo de menos. La razón es el **orden**, y conviene que
quede escrita porque es la que se olvida:

> Montar el bucle antes que las redes sería **exactamente lo contrario de lo que
> hace el framework que lo inspira**: ellos lo tienen porque **PRIMERO** tienen el
> golden-set.

Copiar la pieza más vistosa de un diseño sin copiar el suelo sobre el que se
apoya no es adoptar el diseño: es adoptar su riesgo sin su seguro. Y aquí el
riesgo no es simétrico al suyo — **su radio de daño es una máquina**; el nuestro
son los runs de un tenant en producción, porque un parche automático a un prompt
se aplica a un agente que está trabajando para alguien.

Es además coherente con las otras cinco respuestas del 2026-08-12: **cinco de las
seis eligen medir antes de construir** (el detector de Goodhart antes que la
pasada ciega, el aviso del Hub antes que exigir familias cruzadas, el disparador
escrito antes que el bucle).

## La condición de reapertura, y su estado

Verificado el **2026-08-20** contra el roadmap, no copiado de la nota anterior:

| Condición     | Qué exigía                     | Estado                 | Qué la cerró                                                                                                                                                                           |
| ------------- | ------------------------------ | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `task_gov_02` | Versionar el prompt del agente | **`[x]` (2026-08-19)** | Migración `0143` (`agent_prompt_versions`, RLS + append-only en la capa) y `db/agent_prompt_version_repo.py`. Con `task_gov_03`, `executions.prompt_version` sella `v{N}:{sha256}`     |
| `task_gov_05` | Evals que bloqueen             | **`[x]` (2026-08-20)** | `PUT /agents/{id}` corre la eval contra el golden set y **rechaza la escritura** bajo preset estricto, nombrando los escenarios; válvula acotada a `inconclusive`, auditada y motivada |

Las dos viven en el mismo plan
[`gov-01`](../roadmap/gov-01-precedencia-prompts-y-rigor.md), fases 1 y 2.

## Qué significa exactamente que el disparador haya saltado

Tres cosas, y la tercera es la que evita malentenderlo:

1. **Este aplazamiento está vencido.** No es que «convenga revisarlo»: la única
   razón escrita para no construir SkillOpt era la ausencia de las dos redes, y
   ya no faltan.
2. **Hay que reabrir la decisión**, y eso se hace con un ADR sucesor que decida
   sí o no con el coste delante — o con una decisión escrita del operador, que en
   la cadena de precedencia de `CLAUDE.md` va por delante de este documento.
3. **Reabrir NO es aprobar.** Mientras nadie decida lo contrario, SkillOpt
   **sigue sin construirse**: un disparador que saltase a «se construye» sería una
   aprobación automática de 15-20 días de trabajo que nadie ha firmado. Lo que el
   disparo obliga es a **volver a mirarlo**, no a hacerlo.

### La letra pequeña, que quien reabra tiene que conocer

La condición se escribió como dos `task_id` y se cumple **como está escrita**.
Pero el gate de evals tiene dos mitades y sólo una está cerrada:

- **La ruta de BD sí bloquea** (`task_gov_05`): es por donde un tenant —o un
  parche automático que usara la API— edita el prompt de un agente vivo. Es la
  ruta que le importa a SkillOpt.
- **La ruta de CI sigue abierta** (`task_gov_04`, `[ ]` a fecha de hoy): el
  workflow `eval-on-prompt-change.yml` sigue tomando la rama `--dry-run` a falta
  de secreto y dataset reales. Un parche a un prompt **del repositorio** (los
  `seeds/`) no lo frena hoy una eval, sólo la review humana del PR.

Quien reabra decide si eso basta. Anotarlo aquí es más barato que descubrirlo
después.

## Si se reabre: lo que ya no hay que construir, y lo que sí

**Ya está hecho** (no estaba el 2026-08-12):

- El **dato agregable** del rechazo: `target` × `class` con vocabulario cerrado
  (`task_gov_10`, `packages/shared-domain/src/shared_domain/reject_taxonomy.py`),
  con su agregado por tenant y proyecto. Era el requisito de cualquier bucle de
  mejora, y se implementó orientado a servir por sí solo. **Ojo**: la reflexión
  de SkillOpt (`skills/X` × `rule_missing`) es un par **aditivo** sobre esos ejes,
  no una redefinición — `code × incorrect` no dice qué regla le falta al reviewer.
- El **rastro** para revertir un parche malo (`task_gov_02` + `task_gov_03`) y el
  **freno** para que no llegue a producción (`task_gov_05`).

**Falta decidir**, y son decisiones, no trabajo:

- **Quién propone.** El diseño original usa un puente MCP a Codex. Aquí eso no
  entra: el catálogo de proveedores está cerrado
  (ADR [0021](0021-shared-llm-layer-catalogo-cerrado.md)) y el linaje OpenAI ya lo
  da Azure AI Foundry dentro del catálogo. La «otra familia» se consigue sin
  proveedor nuevo.
- **Quién aplica el parche.** El invariante del original —«el director juzga,
  nunca redacta»— encaja con lo que esta plataforma ya impone por arquitectura
  (el agente emite el veredicto, el worker lo aplica), pero un parche a un prompt
  de producción es una **acción sensible** y hay 13 categorías con políticas de
  aprobación esperando; decidir en cuál cae es parte de reabrir.
- **La memoria negativa** (parches propuestos y rechazados, para no reproponerlos)
  no existe todavía en ninguna forma.

## Alternativas consideradas

- **Descartar SkillOpt del todo.** Rechazada el 2026-08-12: la idea sobrevive al
  filtro del informe; lo que no sobrevivía era el orden.
- **Construirlo ya.** Rechazada por lo mismo: sin las redes, el bucle es la pieza
  más vistosa y la de menor valor marginal, con el riesgo puesto en los runs de un
  tenant.
- **Aplazar sin condición** («no ahora», a secas). Es lo que se hace por defecto
  y es lo que este ADR existe para no repetir: un «no» indefinido no se revisa
  nunca, porque nadie sabe cuándo tocaría.
- **Dejar la nota sólo en prosa** (en el informe y en el plan, como estaba).
  Rechazada al cerrar `task_gov_11`: la propia condición demuestra el fallo — se
  cumplió el 2026-08-20 y las dos personas que cerraron las casillas no tenían
  cómo saber que estaban venciendo un aplazamiento.

## Cómo se comprueba que esto no envejece

`tests/docs/test_adr_deferrals.py` lee los dos campos del frontmatter y exige
cuatro cosas: que los ids de `reopen_when` **existan** en el roadmap; que cada
casilla nombrada **cite de vuelta** a este ADR (quien la cierra abre el plan, no
el corpus de ADR); que `reopen_triggered_on` **conste** en cuanto todas las
casillas están `[x]`; y que **no conste** mientras alguna siga abierta.

Es el mismo patrón que `CLAUDE.md` §«La excepción al gate» aplica al
`gate_override` del roadmap —una excepción escrita caduca y hay que verlo— y la
mitad mecanizable del `rejects:` que introdujo `task_gov_01`.

## Referencias

- [`docs/roadmap/2026-08-12-analisis-agentic-workflow.md`](../roadmap/2026-08-12-analisis-agentic-workflow.md)
  §2.6 y §4 — el análisis y las seis decisiones del operador.
- [`docs/roadmap/gov-01-precedencia-prompts-y-rigor.md`](../roadmap/gov-01-precedencia-prompts-y-rigor.md)
  — `task_gov_02`, `task_gov_05`, `task_gov_10` y `task_gov_11`.
- ADR [0038](0038-evals-continuos-llm-as-judge-golden-promote-merge-gate-shadow-cross-tenant.md)
  — golden datasets, merge-gate y shadow evals.
- ADR [0021](0021-shared-llm-layer-catalogo-cerrado.md) — catálogo cerrado de
  proveedores.
- ADR [0124](0124-retro-automatica-planes.md) — el aprendizaje que ya existe, a
  nivel de memoria.
