---
title: "Un run muerto tarda ~7 h en re-entregarse: el `visibility_timeout` está POR ENCIMA del hard-limit a propósito"
area: celery, redis, runs
encountered: 2026-07-02
stack: Celery 5 + Redis 7 (broker), workers
---

## Síntoma

Se recrea el contenedor de workers (`docker compose up -d --force-recreate workers`)
con un run en vuelo. El run muere con el contenedor, y **el mensaje no se
re-entrega al reiniciar**: la fila de `executions` queda `running` para siempre
—zombi— y su tarea no vuelve a despacharse durante horas.

## Causa raíz

No es un bug: es el diseño. El `visibility_timeout` del broker Redis está fijado
**por encima del hard-limit de 6 h** de un run (prod-06 `zombi_03`). Si estuviera
por debajo, Celery re-entregaría el mensaje de un run **largo y sano** que todavía
está trabajando, y acabarían corriendo DOS runs de la misma tarea sobre el mismo
worktree — exactamente lo que el run-lock existe para impedir.

El precio de esa garantía es la ventana: un run que muere de verdad no se
re-entrega hasta que expira la visibilidad (~7 h con el margen), o hasta que pasa
el sweeper de tareas obsoletas.

## Fix

Ninguno en el código: bajar el timeout cambiaría un problema raro (zombi que
tarda) por uno grave (ejecución duplicada). Lo que sí aplica es operativa:

- **no recrear los workers con runs en vuelo** si se puede esperar;
- si hay que hacerlo, asumir la ventana o cancelar antes los runs desde la UI;
- el sweeper (`workers.maintenance.stale_sweeper`) es la red que acaba cerrando
  los zombis.

Relacionado: `engine-restart-mata-runs-en-vuelo.md`, que describe el mismo efecto
cuando quien muere es el engine entero.

## Cómo verificar el fix

`docker exec agentic-platform-workers-1 python -c "from workers.celery_app import app; print(app.conf.broker_transport_options)"`
— el `visibility_timeout` debe salir mayor que el hard-limit de run (6 h = 21600 s).
