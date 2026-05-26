---
title: La Timeline de Execution requiere shape canónico en steps_log
area: admin-panel, postgres
encountered: 2026-05-26
stack: Next.js 14 + Postgres (JSONB)
---

## Síntoma

Abres `/admin/executions/<uuid>` para una Execution que sembraste a
mano (script demo, fixture de test, INSERT manual) y la página dice:

> Esta ejecución todavía no tiene pasos registrados.

El campo `iterations` aparece a 0, `tokens` a 0, coste `—`. La fila
en BD sí existe (`status=done`) y `executions.steps_log` tiene
contenido — pero como dicts cualquiera.

## Causa raíz

La página `/admin/executions/[id]/page.tsx` itera sobre `steps_log`
así:

```ts
const byIndex = new Map<number, Step>();
for (const step of executionQuery.data?.steps_log ?? []) {
  byIndex.set(step.index, step);
}
return [...byIndex.values()].sort((a, b) => a.index - b.index);
```

Necesita **al menos `step.index`**. Sin él, todos los pasos colapsan
en la misma key `undefined` y sólo sobrevive el último — y como su
`index` también es `undefined`, el sort/render falla y aparece la
lista vacía.

El shape canónico está definido en
`docker/agent-runtimes/agent-runtime/agent_runtime/steps.py` y es el
que producen las funciones `node_step` / `model_call_step` /
`tool_call_step`. Mínimo:

```python
{
    "index": int,          # ascendente, 0-based
    "kind": "node" | "model_call" | "tool_call" | "memory_read",
    "node": str,           # nombre del nodo LangGraph (perceive, plan, ...)
    "status": "ok" | "aborted" | "...",
    "summary": str,
    # opcional, según `kind`:
    "started_at": ISO 8601,
    "ended_at":   ISO 8601,
    "model": str, "tokens_in": int, "tokens_out": int, "cost_usd": float,
    "tool":  str, "args": dict, "result": dict,
}
```

## Fix

Cuando siembres `steps_log` en un script demo o un test, usa el shape
canónico — copia el patrón de `scripts/demo_human_04_5_01.py` (5
pasos `perceive → plan → act → observe → finalize`) o llama
directamente a `agent_runtime.steps.{node_step, model_call_step,
tool_call_step}` desde Python.

**No siembres roll-ups a 0** si la Execution lleva pasos:

```python
Execution(
    ...,
    steps_log=steps_log,
    iterations=1,
    total_tokens=148,
    total_cost_usd=Decimal("0.0015"),
    model_call_count=1,
    tool_call_count=1,
    started_at=datetime.now(UTC),
    completed_at=datetime.now(UTC),
)
```

Si no, el badge del header (`iterations 0 · tokens 0 · coste —`) hace
la página inconsistente aunque la Timeline sí cargue.

## Cómo verificar el fix

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_04_5_01.py
# copia el execution_id del Paso 1
```

```bash
curl -H "Authorization: Bearer <token>" \
     http://127.0.0.1:8001/executions/<execution_id>
# El JSON devuelve steps_log con 5 entradas: cada una con `index`,
# `node`, `kind`, `status`, `summary`. La página los pinta como
# Timeline.
```

## Referencias

- `docker/agent-runtimes/agent-runtime/agent_runtime/steps.py` —
  generadores canónicos.
- `apps/admin-panel/app/admin/executions/[id]/page.tsx:117` — código
  de la UI que requiere `step.index`.
- `scripts/demo_human_04_5_01.py:_seed_done_execution` — ejemplo de
  sembrado correcto.

## Migración retroactiva

No hay. Las filas viejas con `steps_log` mal sembrado se quedan
inservibles para la UI. Para "repararlas" sin migración SQL, basta
con re-ejecutar el demo (genera execution_id nuevo) o limpiar la
tabla `executions` y re-correr lo que necesites.
