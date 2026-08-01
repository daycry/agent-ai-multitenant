---
title: Aprobaciones atascadas
docs_language: es
audience: operador, system admin, tenant admin
updated: 2026-08-01
---

# Runbook — Aprobaciones atascadas

Un run que se detiene esperando un humano y **no vuelve a arrancar**. Este
runbook cubre el diagnóstico y las cuatro causas que lo producen, en el orden en
que conviene descartarlas.

Contexto y contrato: [`04-reference/validacion-humana.md`](../04-reference/validacion-humana.md).

## Síntoma

Una tarea lleva horas en `awaiting_human_approval` y su ejecución no progresa.
En la bandeja de aprobaciones puede que la solicitud aparezca… o no, y esa
diferencia es el primer bifurcador del diagnóstico.

## 0. Antes de tocar nada: mirar

```sql
-- ¿Qué hay pendiente, y desde cuándo?
SELECT ar.id, ar.tenant_id, ar.category, ar.status,
       ar.requested_at, now() - ar.requested_at AS esperando,
       e.status  AS execution_status,
       t.status  AS task_status,
       t.title
FROM approval_requests ar
JOIN executions e ON e.id = ar.execution_id
JOIN tasks t      ON t.id = ar.task_id
WHERE ar.status = 'pending'
ORDER BY ar.requested_at;
```

Y la comprobación que más veces resuelve el caso, porque el barrido tiene un
interruptor vivo:

```sql
SELECT key, value FROM platform_settings
WHERE key IN ('approval_expiry_enabled', 'approval.timeout_hours');
```

Sin fila = valores por defecto (`true`, `24`). Si `approval_expiry_enabled` es
`false`, **nada caduca nunca**: las solicitudes se acumulan y las ejecuciones se
quedan colgadas para siempre. Es exactamente lo que el barrido existe para
evitar.

## 1. La ejecución está parada y NO hay solicitud

La tarea está en `awaiting_human_approval` y no existe fila en
`approval_requests`. Significa que el run paró pero el worker no llegó a crear
la solicitud (el envelope se perdió, el worker murió entre medias).

```sql
SELECT t.id, t.status, e.id AS execution_id, e.status
FROM tasks t
LEFT JOIN executions e ON e.task_id = t.id
LEFT JOIN approval_requests ar ON ar.execution_id = e.id AND ar.status = 'pending'
WHERE t.status = 'awaiting_human_approval' AND ar.id IS NULL;
```

**Qué hacer**: la tarea no puede desbloquearse desde la bandeja porque no hay
nada que aprobar. Se relanza como cualquier otra tarea bloqueada, **con el
operador delante** — nunca automáticamente (orden permanente: no desbloquear sin
verificación).

## 2. Hay solicitud y nadie la atiende

Es el caso normal, y para eso está el barrido. Verificar que el job **existe y
corre**:

```bash
docker compose logs workers --since 30m | grep approval_expiry
```

Tres salidas posibles y qué significan:

| Log                       | Significado                                       |
| ------------------------- | ------------------------------------------------- |
| `approval_expiry.done`    | el barrido corrió; mira `tenants` y `expired`     |
| `approval_expiry.skipped` | `approval_expiry_enabled = false` (ver paso 0)    |
| _nada_                    | beat no lo encola o el worker no registra la task |

Si no aparece nada, la trampa a descartar es la de
[`beat-entry-whose-task-nobody-imports.md`](../03-guides/gotchas/beat-entry-whose-task-nobody-imports.md):
una entrada de beat cuya task **ningún worker importa** se encola y se rechaza
con `NotRegistered`, **sin ruido**. Comprobación:

```bash
docker compose exec workers python -c \
  "from workers.celery_app import app; print('workers.expire_stale_approvals' in app.tasks)"
```

**Bajar la ventana temporalmente** (efecto inmediato, se lee en cada pasada):

```sql
INSERT INTO platform_settings (key, value) VALUES ('approval.timeout_hours', '1'::jsonb)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

> Ojo: hay una **caché Redis de 30 s** sobre los platform settings. Escribir la
> fila a pelo (como arriba) **no la invalida** — el camino correcto es la API de
> System Admin, que invalida dos veces. Si la escribes por SQL, o esperas 30 s o
> purgas la clave: `DEL psetting:approval.timeout_hours`.

El valor se **clampa** a `[0.25 h, 720 h]`, y un valor no numérico cae a 24 h.
Un `0` mal puesto no puede convertir el barrido en «caduca todo».

## 3. Aprobar no desbloquea: vuelve a pedir permiso

La solicitud se aprueba, el run se relanza… y se para en el mismo sitio. Antes
del ADR 0135 esto era el comportamiento por diseño (aprobar devolvía la tarea al
backlog y el agente volvía a proponer la acción). Hoy no debería pasar, y si
pasa la causa está en la **huella**:

```sql
SELECT id, category, action->>'tool' AS tool, status,
       left(action->>'args_hash', 12) AS huella
FROM approval_requests
WHERE task_id = '<task_id>' ORDER BY requested_at;
```

- **Huellas distintas** entre la aprobada y la nueva: el agente regeneró los
  args con alguna diferencia. Es el comportamiento correcto —se aprueba la
  acción exacta, no un permiso por tool—, y la nueva solicitud enseña el delta
  al revisor. Se aprueba la nueva.
- **Misma huella y vuelve a aparcar**: el canje es **por run** (`consumed_at` no
  está persistido, deuda conocida). Si la task se re-ejecuta desde cero varias
  veces, cada run canjea una vez. Revisa `prior_approvals` en la solicitud: si
  el número sube, estás en un bucle y el contador de reintentos lo cortará.

Cuando se agotan los reintentos la tarea acaba en `blocked` con el evento
`approval_retry_capped`. Eso **no es un fallo**: es el tope diseñado para que el
bucle no sea infinito. Lo que toca entonces es mirar por qué el agente insiste.

## 4. Dos revisores a la vez

`POST /approvals/{id}/resolve` es atómico: si dos sesiones resuelven la misma
solicitud, una recibe 200 y la otra **409**, y solo la que ganó aplica las
transiciones de Execution/Task. Un 409 aquí **no es un error que arreglar**: es
la carrera resolviéndose bien. Recargar la bandeja y ver el estado final.

## Lo que NO hay que hacer

- **Editar `approval_requests.status` a mano.** Las transiciones de la ejecución
  y de la tarea van con la resolución; cambiar la fila deja el trío
  inconsistente y la tarea sigue igual de atascada, ahora sin rastro de por qué.
- **Apagar `approval_expiry_enabled` «hasta que se calme».** Es lo que produce
  el paso 2. Si la cola es el problema, la palanca correcta es la política del
  proyecto, no el barrido.
- **Relanzar en masa.** Cada relanzamiento es un run nuevo con su coste. La
  orden permanente es no desbloquear sin verificación.
