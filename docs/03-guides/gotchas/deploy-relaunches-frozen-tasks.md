---
title: "Desplegar relanza las tareas congeladas: el reconciler rescata a los 90 s del `up -d`"
area: deploy, reconciler, orchestrator
encountered: 2026-07-28
stack: Docker Compose, Celery beat, orchestrator
---

## Síntoma

Despliegas —imágenes nuevas, migraciones, `up -d`— sin intención de ejecutar
nada, y **al minuto y medio hay runs de agente en marcha**. Se han lanzado tareas
que llevaban semanas paradas, con su gasto de tokens y su escritura en el
worktree.

Lo desconcertante es que el despliegue en sí fue impecable: nada en las colas, 0
`executions` en `running`, contenedores healthy. La comprobación previa de
«¿queda trabajo en vuelo?» sale **limpia** y aun así el despliegue arranca
trabajo.

## Causa raíz

Una tarea puede quedarse en `in_progress` **sin fila viva en `executions`**: el
dispatch la reclamó (`ready`→`in_progress`, claim atómico) y su ejecución nunca
llegó a crearse. Es un limbo: ni corre ni está disponible.

`_revert_orphan_claim` (reconciler, V-1) existe justo para eso — devuelve a
`ready` toda reclamación huérfana **de más de
`_RECONCILE_ORPHAN_CLAIM_MIN_AGE = 30 minutos`**. Corre en el beat cada 90 s.

Y ahí está la trampa: una tarea congelada **hace semanas** cumple el umbral con
holgura desde el primer tick. En cuanto el beat arranca:

1. el reconciler la pasa a `ready`;
2. el orchestrator, que también acabas de arrancar, la ve despachable;
3. sale un run.

**La comprobación de «trabajo en vuelo» mira el estado equivocado.** Buscar
`executions` en `running` no ve nada porque precisamente lo que define a estas
tareas es que **no tienen** ejecución. Son invisibles al chequeo que uno hace por
instinto antes de recrear contenedores.

Caso real (2026-07-28): 2 tareas `in_progress` desde el 2026-07-18, sin
ejecución. `up -d` a las 12:14:03; ejecuciones creadas a las **12:15:43**. ~165 k
tokens en dos runs de ~90 s. Acabaron `needs_human_review` y `aborted`, y las
tareas quedaron `blocked`.

## Fix

**Antes de recrear contenedores**, cuenta las reclamaciones huérfanas — no las
ejecuciones vivas:

```sql
SELECT count(*) FROM tasks t
 WHERE t.status = 'in_progress'
   AND NOT EXISTS (SELECT 1 FROM executions e
                    WHERE e.task_id = t.id
                      AND e.status IN ('running','pending','queued'));
```

Si devuelve `> 0` y **no** quieres que se ejecuten, despliega **sin arrancar el
orchestrator** y arráncalo después, cuando lo hayas decidido:

```bash
docker compose <-f …> up -d --no-build --scale orchestrator=0
# … decides qué hacer con las tareas …
docker compose <-f …> up -d --no-build orchestrator
```

O, si ya estaba arriba, párala antes del `up -d`:

```bash
docker stop agentic-platform-orchestrator-1
```

Parar el **orchestrator** es suficiente y es lo menos invasivo: el reconciler
solo escribe `ready`, es el orchestrator quien despacha. No hay flag para apagar
el reconciler, y tocar la fila de la tarea sería escribir en datos para evitar un
efecto de despliegue — peor remedio que la enfermedad.

## Cómo verificar el fix

Tras el `up -d`, con el orchestrator parado:

```sql
SELECT count(*) FROM executions WHERE created_at > now() - interval '10 minutes';
```

Debe ser `0`. Y antes de arrancar el orchestrator, que no queden despachables:

```sql
SELECT count(*) FROM tasks WHERE status IN ('ready','queued');
```

## Por qué esto no es un bug del reconciler

El rescate hace exactamente lo que debe: una tarea en ese limbo no sale de él
sola, y dejarla ahí es peor. El problema es de **secuencia del despliegue**, no de
diseño: recrear contenedores es lo que le da al reconciler su primera oportunidad
de actuar sobre un limbo acumulado durante semanas.

De hecho el efecto colateral fue útil —las dos tareas pasaron de un
`in_progress` mudo a un `blocked` con diagnóstico—, pero eso no lo convierte en
correcto: **no era una decisión que le tocara tomar al despliegue.**

Relacionado: [`../verificar-antes-de-implementar.md`](../verificar-antes-de-implementar.md)
§ sobre guardas que pasan vacías. Ésta es la misma familia: una comprobación que
sale verde porque está mirando donde no hay nada.
