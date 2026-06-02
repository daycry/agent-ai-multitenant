# Plan 10 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 10 (Asistente Personal
y Notificaciones Multicanal). Validan lo que los tests automáticos no
pueden: que una notificación **llega de verdad** a un canal externo
(Telegram), que las **preferencias granulares** filtran por evento y
canal, que los **webhooks salientes firmados** sobreviven al ciclo
HMAC + anti-replay + reintentos + dead-letter, y que el **asistente
personal** responde queries cross-proyecto y aprueba planes desde el
chat.

> **Estado del plan**: `pending_human_validation`. Las 17 tareas y sus
> tests automáticos están en verde (modelos NotificationChannel /
> Preference / Log, dispatcher con colas Celery, plantillas Jinja2,
> mapeo de eventos, canales Telegram / Email / Slack / Teams / Discord /
> WhatsApp / SMS, webhooks salientes con HMAC, reintentos + DLQ,
> asistente conversacional, UI de configuración en 3 capas, inbox
> in-app). Estos 4 tests humanos son el último paso antes de pasar a
> `completed`.

## TL;DR

No hay `setup_demo_10.py` ni launcher dedicado para este plan: los tests
requieren credenciales reales de canales externos (un bot de Telegram, un
endpoint receptor de webhooks) que no se pueden sembrar. El setup es
manual:

```powershell
.\scripts\dev\up.ps1     # api-server :8001 + admin-panel :3000 + postgres + redis + notification-dispatcher
```

El asistente y las notificaciones se gestionan desde el admin-panel
(**solo Tenant Admin** — el asistente personal es accesible únicamente a
`role=admin` del tenant):

```
http://localhost:3000/admin/notifications          # canales + preferencias por evento/canal
http://localhost:3000/admin/notifications/inbox     # histórico in-app de notificaciones
```

El toggle `Organization.personal_assistant_enabled` debe estar **ON** en
el tenant para `human_10_04` (default false): actívalo como System Admin
antes de probar el asistente.

## Pre-requisitos

| Requisito                                        | Por qué                                                               |
| ------------------------------------------------ | --------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                      | api-server + admin-panel + postgres + redis + notification-dispatcher |
| Worker(s) Celery arriba                          | El dispatcher consume las colas dedicadas; sin worker no sale nada    |
| Un usuario `tenant_admin`                        | El asistente y la config de canales son operaciones de Tenant Admin   |
| Un bot de Telegram + tu chat_id                  | `human_10_01` necesita un canal Telegram real (token + chat destino)  |
| Un webhook de Slack + un buzón de email          | `human_10_02` compara budget_alert por Slack (OFF) vs email (ON)      |
| Un endpoint receptor de webhooks (p.ej. ngrok)   | `human_10_03` verifica firma HMAC + anti-replay + reintentos + DLQ    |
| `personal_assistant_enabled = true` en el tenant | `human_10_04` — sin el toggle el asistente no responde a nadie        |

---

## `human_10_01` — Notificaciones por Telegram funcionan

**Qué prueba**: configurar un canal Telegram personal y disparar un
evento `task_blocked` hace que el mensaje llegue al chat correcto en
menos de 30 s, con contexto suficiente y un botón inline "Ver" que abre
la UI en la tarea.

**Precondiciones**:

- Un bot de Telegram creado con @BotFather (tienes su token) y el
  `chat_id` de tu conversación con el bot.
- Login como `tenant_admin`.

**Pasos**:

1. En `/admin/notifications`, añade un canal **Telegram**: pega el token
   del bot + tu `chat_id`. Guarda.
2. Asegúrate de que la preferencia para el evento `task_blocked` por
   Telegram está **activada**.
3. Provoca un evento `task_blocked`: bloquea una tarea de un proyecto
   (p.ej. una tarea que escala a humano, o fuérzala desde la UI/API).
4. Mira tu chat de Telegram: el mensaje debe llegar en **< 30 s**.
5. Verifica que el mensaje contiene **contexto** (nombre de la tarea,
   proyecto, motivo del bloqueo).
6. Pulsa el botón inline **"Ver"** del mensaje → debe abrir el
   admin-panel directamente en la tarea bloqueada.

**Resultado esperado**: el mensaje llega al chat correcto en menos de
30 s, con contexto suficiente, y el botón "Ver" abre la UI en la tarea.

**Checklist**:

- [ ] El mensaje llega al chat correcto en menos de 30 s.
- [ ] Contiene contexto suficiente (tarea, proyecto, motivo).
- [ ] Click en el botón inline "Ver" abre la UI en la tarea.

**Pitfalls conocidos**:

- Si no llega nada, comprueba que el **worker Celery** está vivo: el
  dispatcher usa colas dedicadas y sin consumidor el mensaje se encola
  pero no sale.
- El `chat_id` debe ser el de tu conversación con el bot, no el del bot.
  Un bot no puede iniciar conversación: escríbele tú primero al bot.
- Telegram no requiere verificación Meta (es el canal primario por
  simplicidad); si el token es válido y aun así falla, revisa el log del
  dispatcher por `401 Unauthorized` (token mal pegado).

---

## `human_10_02` — Preferencias granulares funcionan

**Qué prueba**: un usuario puede desactivar `budget_alert` por Slack pero
mantenerlo por email; al disparar el evento, llega por email y **no** por
Slack.

**Precondiciones**:

- Dos canales configurados para el mismo usuario: **Slack** (webhook) y
  **Email** (SMTP).
- Login como `tenant_admin`.

**Pasos**:

1. En `/admin/notifications`, en la matriz de preferencias por
   evento/canal, localiza la fila del evento **`budget_alert`**.
2. **Desactiva** `budget_alert` para el canal **Slack**.
3. **Mantén activado** `budget_alert` para el canal **Email**.
4. Guarda.
5. Dispara un `budget_alert` (cruzar un umbral de budget del proyecto/
   tenant, o forzar el evento si no tienes budgets activos — ver nota).
6. Comprueba tu Slack: el `budget_alert` **NO** llega.
7. Comprueba tu email: el `budget_alert` **SÍ** llega.

**Resultado esperado**: `budget_alert` no llega por Slack pero sí por
email — la preferencia granular por evento+canal se respeta.

**Checklist**:

- [ ] budget_alert NO llega por Slack.
- [ ] budget_alert SÍ llega por email.

**Pitfalls conocidos**:

- El **sistema de Budgets quedó como hueco de alcance del Plan 11** (sin
  tarea numerada, no implementado), así que puede que no tengas un
  `budget_alert` real que disparar de forma natural. Para probar la
  preferencia granular, usa cualquier evento que tengas configurado en
  ambos canales y desactiva uno — la mecánica de filtrado es la misma.
- Si llega por ambos, comprueba que guardaste la preferencia y que no hay
  una preferencia de **capa superior** (tenant/plataforma) forzando el
  canal. Las capas son plataforma → tenant → usuario.

---

## `human_10_03` — Webhooks salientes con firma

**Qué prueba**: un webhook saliente firma con HMAC SHA-256, el receptor
verifica con la clave compartida, el mismo nonce no se acepta dos veces
(anti-replay), un 5xx del receptor dispara reintentos con backoff, y tras
5 reintentos el mensaje va a la dead-letter queue.

**Precondiciones**:

- Un endpoint receptor de prueba accesible desde el stack (p.ej. un
  pequeño servidor que verifique la firma, o un túnel ngrok hacia tu
  máquina) que puedas configurar para devolver 200 o 5xx a voluntad.
- Login como `tenant_admin`.

**Pasos**:

1. En `/admin/notifications`, configura un **webhook saliente**: URL del
   receptor + una **clave compartida** (secret) para la firma HMAC.
2. Dispara un evento que mapee a ese webhook.
3. En el receptor, recalcula el HMAC SHA-256 del cuerpo con la clave
   compartida y compáralo con la cabecera de firma → debe **coincidir**.
4. Reenvía exactamente el mismo payload (mismo **nonce** + timestamp) al
   receptor → tu validador anti-replay debe **rechazarlo** (nonce ya
   visto).
5. Configura el receptor para devolver **5xx**. Dispara otro evento → el
   dispatcher debe **reintentar con backoff exponencial** (observa los
   intentos espaciados en el log del dispatcher).
6. Mantén el 5xx hasta agotar los reintentos: tras **5 reintentos** el
   mensaje debe acabar en la **dead-letter queue**.

**Resultado esperado**: la firma verifica, el nonce repetido se rechaza,
un 5xx provoca reintentos con backoff, y tras 5 reintentos el mensaje
cae a la DLQ.

**Checklist**:

- [ ] La firma HMAC verifica con la clave compartida.
- [ ] Anti-replay funciona: el mismo nonce no se acepta dos veces.
- [ ] Si el receptor devuelve 5xx, reintenta con backoff.
- [ ] Tras 5 reintentos, va a dead-letter queue.

**Pitfalls conocidos**:

- La firma cubre **cuerpo + nonce + timestamp** (anti-replay): si tu
  receptor solo firma el cuerpo, no cuadrará. Replica el esquema exacto
  del dispatcher.
- El reintento manual desde la UI (task_10_13) re-encola un mensaje de la
  DLQ — útil para confirmar el step 6 sin esperar otra ráfaga.
- Si los reintentos no respetan el backoff, comprueba que es el worker
  Celery (no el web) quien procesa la cola — el backoff vive en la tarea.

---

## `human_10_04` — Asistente personal responde queries

**Qué prueba**: el asistente personal conversacional devuelve un listado
consolidado cross-proyecto, puede aprobar planes pendientes desde el chat
y mantiene contexto entre mensajes. Solo accesible para `role=admin` del
tenant.

**Precondiciones**:

- `Organization.personal_assistant_enabled = true` en el tenant (System
  Admin lo activa).
- Login como `tenant_admin` con varias tareas/planes pendientes en
  distintos proyectos.

**Pasos**:

1. Confirma el toggle: como **System Admin**, activa
   `personal_assistant_enabled` para el tenant. (Con un `role=member`,
   verifica de paso que el asistente **no** es accesible.)
2. Como `tenant_admin`, abre el chat del asistente personal.
3. Pregunta: **"¿qué tareas tengo pendientes?"** → debe devolver un
   **listado consolidado cross-proyecto** (no de un solo proyecto),
   respetando el RBAC del admin que pregunta.
4. Pregunta por un plan pendiente de aprobación y pídele **aprobarlo**
   desde el chat → el plan queda aprobado (verifícalo en
   `/admin/plans`).
5. Encadena un segundo mensaje que dependa del primero (p.ej. "¿y de esos,
   cuál vence antes?") → el asistente debe **mantener el contexto** de la
   conversación.

**Resultado esperado**: listado consolidado cross-proyecto, aprobación de
planes desde el chat, y contexto mantenido entre mensajes.

**Checklist**:

- [ ] Devuelve listado consolidado cross-proyecto.
- [ ] Puede aprobar planes pendientes desde el chat con el asistente.
- [ ] Mantiene contexto entre mensajes.

**Pitfalls conocidos**:

- El asistente es **por tenant y solo para Tenant Admins**: un usuario
  `role=member` NO puede interactuar con él (por diseño, ver Resumen del
  plan). Si un member lo ve, es un fallo de RBAC.
- Si el toggle `personal_assistant_enabled` está en false (default),
  **ningún** Tenant Admin del tenant puede usar el asistente — es lo
  primero a comprobar si "no responde".
- El tool `tenant_budget_status` del asistente sigue siendo un **stub
  tipado** (Budgets quedó pendiente en Plan 11): no esperes cifras de
  presupuesto reales en sus respuestas.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/10-asistente-personal.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/10-asistente-personal.md`](../../07-changelog/).
3. Verifica que el PR `plan/10-asistente-personal` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                       | Causa probable                                                  | Fix                                                                  |
| --------------------------------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------- |
| La notificación se encola pero no sale        | El worker Celery / notification-dispatcher no está vivo         | Comprueba el servicio en `up.ps1`; mira la cola del dispatcher       |
| Telegram da 401 en el log del dispatcher      | Token del bot mal pegado o revocado                             | Re-genera el token con @BotFather y reconfigura el canal             |
| El evento llega por el canal que desactivaste | Preferencia de capa superior (tenant/plataforma) forzando canal | Revisa las 3 capas; la del usuario no anula un lock de capa superior |
| El webhook nunca cae a la DLQ                 | El receptor no devuelve 5xx de forma sostenida                  | Fuerza 5xx hasta agotar los 5 reintentos; mira el backoff en el log  |
| El asistente no responde                      | `personal_assistant_enabled = false` o estás como `member`      | Actívalo como System Admin; entra como `tenant_admin`                |

Errores transversales viven en `docs/03-guides/gotchas/`.
