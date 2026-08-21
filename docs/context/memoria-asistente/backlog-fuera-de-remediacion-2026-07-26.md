---
name: backlog-fuera-de-remediacion-2026-07-26
description: "Barrido del backlog fuera de la remediación (2026-07-26) — 3 planes in_progress cerrados, 3 ADR aceptados; queda el 0117 y ~16 planes en pending_approval."
metadata:
  node_type: memory
  type: project
  originSessionId: c24b547e-58f5-4ecf-a67d-6507fb095bad
  modified: 2026-07-26T21:13:33.499Z
---

Tras cerrar [[remediacion-workflow-proyectos-en-curso]], barrido de todo lo que
quedaba **fuera** de ese plan. Empujado hasta `2de324dd` en
`plan/runs-visor-trabajo`. **Cero fases `in_progress`** (había cuatro, violando
el protocolo) y **cero ADR `proposed`** salvo el 0117.

Cerrado:

- **`mejoras-2026-06-chat-coste-cortex`** → `pending_human_validation`. Las 3
  tareas de código ya estaban; faltaba el test. El inventario de familias de
  stream que escribí encontró a la primera una fuga que el plan no listaba:
  `exec:{id}` sin limpieza ni TTL — una clave de Redis por run y para siempre.
  Arreglado con TTL deslizante de 7 días.
- **`plan-unificacion-provider-id`** → `pending_human_validation`. Se comparte la
  REGLA (`lib/model-selection.ts`), no el widget: el selector del córtex está
  tras `require_system_owner` y es tenant-less (ADR 0074). `GET
/agents/model-options` **retirado** (67 líneas, cero llamantes).
- **`guardas-research-por-novedad`** → `blocked`. La mayoría de G1-G13 ya las
  había resuelto el **ADR 0103**; G1 está RECHAZADA y G9 no se implementa, por
  recomendación del propio ADR. Cerradas hoy G6 y G11. Solo quedan F2 y G13, que
  son e2e y exigen desplegar.
- **ADR 0128, 0110 y 0076** → `accepted` (ver [[adr-pendientes-implementar-autonomo]]).
- **ADR 0117** → `accepted`. El operador eligió las dos recomendadas: (b) retirar
  del principio 7 de CLAUDE.md la promesa de `task.human_validation_required`
  —flag que nunca tuvo columna ni código— y documentar las vías reales
  (políticas de aprobación por categoría + `ask_human`, ADR 0114); (c)
  consolidar: `admin-panel` es el frontend ÚNICO y `apps/web-app` borrada.

  **Y (c) destapó un fallo de DR serio.** `Settings.restore_app_services`
  incluía `web-app`, servicio que no existe en ningún compose.
  `_stop_app_stack` hace `docker compose stop` y **eleva si el rc != 0**, que es
  lo que devuelve compose ante un servicio desconocido: la restauración completa
  **abortaba en el paso 3, antes de restaurar nada**, y solo se manifiesta
  ejecutándola de verdad. Test que compara la lista contra `CORE_SERVICES` del
  generador del instalador.

  Anotado: `memorizer`, `personal-assistant` y `webhook-dispatcher` TAMBIÉN son
  solo `.gitkeep`; no se borran (su lógica vive embebida y la carpeta puede ser
  destino de extracción) pero CLAUDE.md ya las marca RESERVADAS.

**Why:** el patrón dominante fue el mismo de la remediación — los checkboxes y
los `status` mentían. De las 21 tareas «pendientes» de los tres planes, la
mayoría estaban hechas o explícitamente rechazadas por un ADR posterior.
Verificar cada premisa contra el código antes de implementar ahorró casi todo el
trabajo aparente.

**How to apply:** lo que queda es del operador —

1. **~16 planes en `pending_approval`** (prod-03/04/05/07/08/09/10/11/13/14/15/16,
   `cadena-pr-plan`, `tools-y-cierre-plan-fixes`,
   `remediacion-auditoria-integral-2026-07-14`). El protocolo prohíbe arrancar
   una fase sin aprobación.
2. **`registry-egress-followups`** (`open`): F1/F3/F4/F5 siguen abiertos. F6 se
   cerró al verificarlo — `slugify` nunca devuelve vacío y la migración 0114 es
   el backstop.

Gotchas de esta tanda:

- **Un test puede fijar una vulnerabilidad.** Cuatro afirmaban que la credencial
  de claude_sdk aterrizaba en `os.environ` y pasaban en verde mientras la fuga
  existía. Un test que documenta lo observado sin preguntarse si es correcto
  convierte un fallo en contrato.
- **Un plan puede equivocarse en su propia solución.** G6b proponía sugerir
  `head -n N | tail` como alternativa, y `stack_exec` no admite tuberías: habría
  mandado al agente a un segundo fallo.
- **Comprobar la premisa de la prudencia también.** Iba a dejar
  `/agents/model-options` deprecado «para no romper un SDK»; los SDK se generan
  solo del OpenAPI v1 y esa ruta vive fuera de `/api/v1`.
- **Una divergencia «solo documental» puede tener aristas ejecutables.** La
  opción (c) del ADR decía «coste: cero código» y escondía un restore roto: la
  lista de servicios de un runbook ES código, aunque parezca prosa.
- **Un test de inventario paga.** El que enumera familias de stream encontró una
  fuga real en su primera ejecución. Hacerlos no-vacuos (assert de que el
  descubrimiento encontró algo) es lo que impide que envejezcan en verde.
