---
plan_id: remediacion-gestion-proyectos-2026-07-25
title: Remediación del workflow de gestión de proyectos — cableado del último tramo
completed_at: null
docs_language: es
---

# Plan remediacion-gestion-proyectos-2026-07-25 — Cableado del último tramo

## Resumen

Remediación completa de los 38 hallazgos verificados de la auditoría
[auditoria-gestion-proyectos-2026-07-25](../roadmap/auditoria-gestion-proyectos-2026-07-25.md),
que barrió de punta a punta el dominio «gestión de proyectos»: agentes, tools,
MCP, contenedores de ejecución y de revisión, git, prompts, generación de planes
y modos de chat.

La tesis de la auditoría —confirmada por el trabajo— es que **no falla el
diseño, falla el cableado del último tramo**. Una y otra vez el mecanismo estaba
construido entero y no lo llamaba nadie, o producía un dato que ninguna pantalla
leía. El patrón se repitió en el subsistema de evals (7 módulos, 7 tablas, 18
endpoints, dashboard… y las siete tablas vacías), en `record_shadow_eval` (sin
un solo llamante desde el Plan 14), en `EvalRun.subject_prompt_version` (nadie
lo poblaba, así que el dashboard agrupaba toda la calidad bajo «(sin versión)»),
en `compute_plan_progress`, en `pr_url`, y en las filas `eval_results`, que se
escribían y ninguna ruta las leía.

56 tareas en 8 olas. Todas cerradas con test.

## Lo entregado, por ola

- **Ola −1/0 — Bugs que rompían el producto hoy.** El chat de planning cargaba
  los 50 mensajes **más antiguos** (el feed se congelaba y «Generar Plan»
  desaparecía para siempre); el `summary` del plan generado por chat viajaba
  como `str` contra un esquema `dict`, así que cualquier `PUT` posterior daba
  422; la bandeja del humano enlazaba a una ruta 404.
- **Ola 1 — Los agentes no veían lo que podían usar.** Las tools MCP del
  proyecto (ADR 0128) quedaban permitidas pero **invisibles** para el modelo; un
  agente sin `agent_tools` no veía ni `read_file` ni `write_file` aunque el
  registry no le restringiera nada.
- **Ola 2 — Infra de ejecución.** `HOME=/workspace` en el test-runtime (las
  cachés aterrizaban en el worktree y `git add -A` se las llevaba); el run-lock
  caducaba 180 s **antes** que el hard kill, ventana en la que una redelivery
  podía hacer `reset --hard` sobre el worktree que el lock existe para proteger.
- **Ola 3 — Ceguera operativa.** WebSocket de planes (`events:plans`), progreso
  del plan, PR visible, coste real vs estimado, informe de reutilización de
  caché de prompt.
- **Ola 4 — Workflow del humano.** Editor del spec antes de aprobar,
  replanificación en caliente (**ADR 0132**), acciones sobre tareas bloqueadas,
  gobierno del proyecto, y la guía a un run **en marcha** — hasta ahora la única
  intervención posible era matarlo, tirando todo el trabajo hecho.
- **Ola 5 — Convergencia.** Los cuatro hooks de guardrails (el `pre_llm`, que
  ve contenido de fichero y salida de MCP, no se escaneaba); versionado de
  prompts por AST; digest de la imagen de runtime; el reviewer con el **diff**
  de la tarea.
- **Ola 6/7 — Deuda y medida.** 2 223 líneas de código muerto, incluida la única
  ruta que tocaba el socket Docker; y el encendido de los evals.

## Decisiones que conviene recordar

- **Un eval que siempre pasa es peor que no tener eval.** El item dorado trae
  `expected_output` —la referencia—, y pasarla como salida del sujeto haría que
  el juez la comparase consigo misma: 100 % de aciertos, siempre, midiendo nada.
  El sujeto produce de verdad, y hay un test dedicado que lo fija.
- **El grifo de los shadow evals nace cerrado.** Hacen falta tres condiciones
  deliberadas y visibles (tasa > 0, `EVAL_JUDGE_MODEL`, y un dataset `shadow`
  con items). Instalar esto no enciende gasto.
- **Un juez corre a temperatura 0; el sujeto no.** Un juez creativo puntúa
  distinto el mismo par dos veces y hace inservible comparar releases, que es el
  punto de medir. Al sujeto se le mide produciendo como produce de verdad.
- **La reconciliación de una replanificación es a tres bandas** (editable /
  en vuelo / terminal), ADR 0132: reescribir en bloque un plan con tareas ya
  corriendo tiraría trabajo hecho.
- **Los tokens se calibran con el histórico; las horas humanas no.** Lo único
  que hay medido es wall-clock de MÁQUINA, y calibrar horas-PERSONA con eso
  daría un número que parece medido y no lo es — peor que uno que se sabe
  estimado.

## ADR

- **ADR 0132** — Replanificación en caliente (`accepted`).

## Verificación

| Suite            | Resultado               |
| ---------------- | ----------------------- |
| unit             | 2 766 ✅                |
| agent-runtime    | 465 ✅                  |
| frontend (admin) | 387 ✅                  |
| security         | 73 ✅                   |
| mypy (strict)    | 581 ficheros, limpio ✅ |

Integración: ejecutada por bloques contra el Postgres de docker-compose (los
tests nuevos de cada ola). CI sigue caída por facturación de la cuenta, así que
las suites se han corrido en local.

## Lo que queda fuera de este changelog

- **Despliegue**: no realizado. Sigue en pie la orden del operador de no
  relanzar ni desbloquear nada hasta dar el sistema por verificado.
- **Tests humanos del plan**: pendientes. Por eso el plan está en
  `pending_human_validation` y no en `completed`.
- **Siembra del dataset dorado**: es curaduría humana — elegir qué tareas
  cerradas son «buenas» no lo decide el sistema. El mecanismo
  (`POST /tasks/{id}/promote-to-dataset`), el productor, el lector y el
  muestreador están puestos y probados.
- **Prueba en navegador del OAuth de MCP** (ADR 0127) y **smoke del perfil
  seccomp estricto en dev**: requieren un humano delante.
