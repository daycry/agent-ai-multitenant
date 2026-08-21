---
plan_id: cortex-f4-autonomia
title: "Córtex F4 — curiosidad y pensamiento de fondo (bucles cognitivos autónomos)"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex F4 — curiosidad y pensamiento de fondo

## Resumen

Cuando el drive `curiosity` baja, el córtex elige un tema entre las entidades
que el owner ha mencionado, lo investiga, lo destila a una memoria de
aprendizaje, sacia el drive y **abre el tema en el próximo encuentro** — todo
bajo budget en Redis, circuit-breaker y kill-switch. Gobierno:
[ADR 0078](../05-architecture-decisions/0078-bucles-cognitivos-fondo-cortex.md).

**Lo primero que hay que saber de esta fase: el kill-switch
`cortex.autonomy_enabled` está OFF por defecto y nadie lo ha encendido.** Las
tres entradas del beat tickean y salen no-op. Encenderlo es una decisión
explícita del operador, y es lo correcto: esta fase introduce consumo de LLM y
de egress que el owner no ha disparado.

## Cambios

- **Budget y circuit-breaker deterministas** en
  [`cortex/autonomy.py`](../../apps/api-server/src/api_server/cortex/autonomy.py):
  `daily_budget_key` (`cortex:budget:{owner}:{kind}:{yyyymmdd}` con TTL hasta
  medianoche UTC), `check_searches_budget`, `record_searches`, `is_circuit_open`,
  `record_failure`, `record_success`. Puros sobre Redis, testeables sin LLM.
- **Selección de tema y destilado** en `cortex/curiosity.py`:
  `gather_owner_entities` (entidades del owner desde `memory_entries`),
  selección determinista del tema y `persist_learning_memory` idempotente
  (`metadata_.cortex_pursuit_id`).
- **Tabla de auditoría** `cortex_curiosity_pursuits` (migración
  `20260624_0095_cortex_curiosity_pursuits.py`, +
  `20260706_0103_cortex_pursuit_surfaced.py` para el estado `surfaced`),
  tenant-less con `owner_user_id` en todo SQL y CHECK del ciclo de vida.
- **Beat** `sched["cortex-curiosity"]` → `workers.cortex_curiosity_loop`
  (`workers/cortex_curiosity.py`), cadencia `WORKERS_CORTEX_CURIOSITY_CRON`,
  enable/kill-switch leídos **en vivo** al inicio de cada pasada.
- **Endpoints** (`routers/cortex_mind.py`): `GET /owner/cortex/curiosity/pursuits`
  (:313) y el kill-switch `GET/PUT /owner/cortex/autonomy` (:516/:526).
- **Surfacing**: un pursuit `digested` se inyecta en el siguiente turno y pasa a
  `surfaced` en la misma transacción (si hay rollback, sigue pendiente). Ese
  último tramo se cerró en [cortex-identidad-real](cortex-identidad-real.md) —
  hasta el 2026-07-06 el bucle aprendía y no lo contaba nunca.
- **UI**: tarjeta "Lo que está aprendiendo" en el Panel de Mente, con copy
  honesto ("bucle programado, no curiosidad consciente") y su test de render
  (`app/admin/cortex/mind/page.test.tsx`).

## Divergencias respecto al plan (verificadas)

- **La forma del budget difiere**: clave string por día en vez del hash
  `cortex:budget:{owner}`, y vive en `cortex/autonomy.py` en vez de
  `cortex/curiosity/budget.py`. Divergencia de forma, no de fondo.
- **La investigación no usa `claude_sdk`.** El plan la ataba a
  `run_agent(allowed_tools=["WebSearch","WebFetch"], effort=…)` (ADR 0076
  punto 3). No existe `cortex/researcher.py` ni `research_topic(...)`: la vía
  real es la tool web propia del córtex (ADR 0067), coherente con la divergencia
  ya registrada en F1. Consecuencia: la rama "provider sin SDK → `skipped
no_sdk`" no existe porque nunca hay camino SDK.
- **Contratos del destilado**: `persist_learning_memory` (no `persist_learning`),
  en `cortex/curiosity.py` (no `cortex/curiosity/digest_memory.py`), devuelve
  `UUID | None` y exige un `tenant_id` que el plan no contemplaba.
- **La inyección del tema** se compuso dentro de `cortex/self_context.py`, no en
  un `cortex/curiosity/surfacing.py` con las firmas literales del plan.
- **`search_count` es `Numeric(10,0)`** en vez de `Integer`, lo que obliga a un
  `int()` defensivo en el router.

## Lo que sigue abierto

> Punto de partida: la auditoría del [2026-07-27](../roadmap/gaps-cortex-2026-07-27.md).
> Re-comprobado hueco por hueco el **2026-07-30 a las 11:00**, con una remediación **en curso**
> sobre los ficheros de esta fase — de ahí que la tabla de abajo lleve fecha y método en cada fila
> en vez de una lista de ausencias.

### Estado del gobierno, hueco por hueco

Ordenados por lo que importa para decidir si se enciende la autonomía. Cada uno lleva **la fecha y
el método** de su última comprobación, porque tres de los seis cambiaron de estado en 24 horas.

| Hueco del gobierno (ADR 0078)                 | Estado comprobado                                                                                                                                                                                                                      |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Claves de platform settings (3 de 7 faltaban) | **Cerrado** entre el 2026-07-29 y el 2026-07-30: `cortex.curiosity_enabled`, `cortex.curiosity_approval_gate` (default `True`) y `cortex.curiosity_daily_usd_cap` (default `0.50`) están en `db/platform_settings.py` con sus getters. |
| Dimensión USD del budget                      | **Mecanismo presente** (`check_and_reserve`, `record_spend`, `read_budget_usage`, `CURIOSITY_USD_KIND` en `cortex/autonomy.py`); a 2026-07-30 11:00, **sin llamantes** en `workers/cortex_curiosity.py`.                               |
| Owner-approval gate (paso 7, MVP del plan)    | **En movimiento**: la columna `approved` (Boolean nullable) ya está en el modelo ORM y el frontend tiene `pursuitAwaitsApproval` / `decideCortexPursuit` con su vitest. El verbo HTTP y la rama del bucle no los pude certificar.      |
| Métricas OTEL (4 del ADR 0078)                | **Abierto** a 2026-07-30: `grep` de `agentic_cortex_curiosity` en `apps/` → cero.                                                                                                                                                      |
| Test propio de `gather_owner_entities`        | Abierto en la auditoría; cobertura sólo indirecta vía el happy path del bucle. No reutiliza `memorizer/recall.py::query_entity_terms` ni agrega entidades de `cortex_turns`.                                                           |
| Copy honesto en EN                            | Abierto en la auditoría: la API devuelve `note_es` y `note_en`, la página renderizaba sólo `note_es`.                                                                                                                                  |

**Lo que esta entrada NO puede certificar, y por qué.** Los ficheros de esta fase estaban siendo
modificados por otra línea de trabajo mientras se escribía (`cortex/autonomy.py` a las 10:55,
`db/platform_settings.py`, `db/cortex_curiosity.py`, `lib/cortex-curiosity.ts`). Un changelog
escrito sobre un árbol en movimiento puede afirmar con precisión algo que dejó de ser verdad diez
minutos antes; preferimos decirlo a dar una lista exacta y falsa. **La autoridad sobre el estado de
cada casilla son el plan de la fase y sus tests.**

**Lo que sí se sostiene, y es la conclusión operativa:** que las piezas del gobierno existan no
significa que el bucle las respete. La pregunta que hay que responder con un test antes de encender
`cortex.autonomy_enabled` es la de §5 de
[`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md): **¿quién
llama al gate y quién ve el resultado?** Mientras eso no esté fijado por un test que falle si se
borra el cableado, encender la autonomía es encender un bucle autónomo sin freno verificado.

Detalle por casilla, tal como estaba el 2026-07-27, en
[gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md).

## Tests

`test_cortex_autonomy.py`, `test_cortex_curiosity_budget.py`,
`test_cortex_topic_selection.py`, `test_cortex_beat_schedule.py` (unidad);
`test_cortex_autonomy_budget.py`, `test_cortex_autonomy_endpoint.py`,
`test_cortex_autonomy_settings.py`, `test_cortex_curiosity_loop.py`,
`test_cortex_curiosity_entities.py`, `test_cortex_curiosity_migration.py`,
`test_cortex_pursuits_endpoint.py`, `test_cortex_surfacing.py` (integración).

## Estado de cierre

No cerrable, y por una razón de seguridad, no de burocracia: el gate de aprobación del owner y el
tope de gasto en USD son parte del MVP del plan, y a 2026-07-30 sus piezas existían **sin que el
bucle las llamara**. Mientras `cortex.autonomy_enabled` siga OFF nada de esto puede gastar;
encenderlo antes de que un test fije el cableado sería encender un bucle autónomo con el freno
puesto en la caja, no en la rueda.

Quien vaya a cerrar esta fase necesita ver tres cosas en verde, no dos: (1) el bucle consulta el
approval gate y deja el pursuit esperando; (2) el bucle reserva y registra gasto en USD, y el panel
muestra un `cost_usd` distinto de 0; (3) las 4 métricas OTEL del ADR 0078 renderizan.

## PR

- _pendiente_
