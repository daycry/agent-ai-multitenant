# Plan 11 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 11 (Guardrails
Declarativos y Catálogo de Precios). Validan lo que los tests
automáticos cubren solo con detectores mockeados: que el **PII se
enmascara** antes de logs y de LLMs externos pero el usuario sigue
viendo el original, que el **secret leakage** redacta tokens y alerta al
admin, que el **cost ceiling** aborta ejecuciones caras dejando la tarea
en `blocked`, y que la **sincronización de precios** muestra diff,
exige confirmación si la subida supera el 10 % y deja rastro en el
audit log.

> **Estado del plan**: `pending_human_validation`. Las 23 tareas
> (`task_11_01`..`task_11_23`) están implementadas, commiteadas y con su
> test automático en verde (motor de guardrails declarativo en 4 puntos,
> 12 tipos built-in, config en capas bloqueable, catálogo `model_prices`
> USD-canónico + RLS de lectura global, snapshot de precio por
> ejecución, pantalla Modelos & Precios, sync desde el JSON público de
> LiteLLM con diff + gate >10%, detección new/discontinued, cron
> configurable, audit log de sync, guardrail_events + dashboard, alertas
> configurables, guardrails del chat de planning). Estos 4 tests humanos
> son el último paso antes de pasar a `completed`.

> **Hueco de alcance (lee antes de probar)**: el plan describe un sistema
> de **Budgets / exchange_rates / display_currency** que **no tiene
> tarea numerada y NO se implementó** (ver
> `docs/07-changelog/11-guardrails-precios.md`, sección Pendiente). El
> `cost_ceiling` de `human_11_03` se prueba como guardrail puro (umbral
> en `metadata`), no como un budget de proyecto/tenant completo.

## TL;DR

No hay `setup_demo_11.py` ni launcher dedicado para este plan: los tests
necesitan disparar el motor de guardrails con texto real (PII, tokens,
código) y sincronizar el catálogo de precios. El setup es manual:

```powershell
.\scripts\dev\up.ps1     # api-server :8001 + admin-panel :3000 + postgres + redis
```

Las dos pantallas relevantes en el admin-panel:

```
http://localhost:3000/admin/guardrails      # dashboard de guardrail_events + reglas de alerta (Tenant Admin)
http://localhost:3000/admin/model-prices     # catálogo Modelos & Precios + 'Sincronizar precios' (System Admin)
```

Para `human_11_01` y `human_11_02` necesitas el **chat de planning** de
un proyecto (`/admin/projects/{id}/chat`) o un agente que genere
código — ahí es donde corren los hooks `pre_llm`/`post_llm`.

## Pre-requisitos

| Requisito                                         | Por qué                                                                       |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                       | api-server + admin-panel + postgres + redis                                   |
| Un usuario `tenant_admin`                         | El dashboard de guardrails y las reglas de alerta son operaciones de tenant   |
| Un usuario `system_admin`                         | El catálogo Modelos & Precios y el botón 'Sincronizar precios' son de sistema |
| Un proyecto con chat de planning                  | `human_11_01` dispara PII en `pre_llm`/`post_llm` del chat                    |
| Una regla de alerta de guardrails (severity high) | `human_11_02` espera alerta al admin al detectar secret leakage               |
| Salida a Internet (o feed LiteLLM cacheado)       | `human_11_04` lee el JSON público de precios de LiteLLM (solo data feed)      |

---

## `human_11_01` — PII se enmascara antes de logs y antes de LLMs externos

**Qué prueba**: un mensaje con DNI, email e IBAN en el chat de planning
se enmascara en los logs y en lo que recibe el LLM externo, pero la UI
sigue mostrando el original al usuario, y el audit log refleja la
enmascaración.

**Precondiciones**:

- El guardrail `pii` activo (baseline de plataforma; default `redact` en
  `post_llm`, `block`/redact en `pre_llm`).
- Login como `tenant_admin` en un proyecto con chat de planning.

**Pasos**:

1. Abre `/admin/projects/{id}/chat`.
2. Escribe un mensaje que contenga **PII**: p.ej. un email
   (`alguien@ejemplo.com`), un IBAN (`ES91 2100 0418 4502 0005 1332`) y un
   DNI/número de tarjeta. Envíalo.
3. Mira la **UI**: a ti, como usuario, debe seguir mostrándote el texto
   **original** (no enmascarado).
4. Inspecciona los **logs** del api-server / el detalle del
   `guardrail_event`: la PII debe aparecer **enmascarada** (marcadores
   tipo `[REDACTED:...]`, nunca el valor en crudo).
5. Verifica que lo que recibió el **LLM externo** fue la versión
   enmascarada (en el step del modelo / el snapshot del turno).
6. Abre el dashboard `/admin/guardrails`: el evento `pii` debe aparecer,
   y el **audit/detalle** del evento refleja la enmascaración sin volcar
   la PII.

**Resultado esperado**: logs y LLM externo reciben la versión
enmascarada; la UI muestra el original; el audit log refleja la
enmascaración.

**Checklist**:

- [ ] Logs muestran datos enmascarados.
- [ ] El LLM externo recibe versión enmascarada.
- [ ] La UI sigue mostrando original al usuario.
- [ ] Audit log refleja la enmascaración.

**Pitfalls conocidos**:

- El detector de PII usa **Presidio** como extra opcional
  (`shared-guardrails[pii]`). Si Presidio no está instalado, degrada a un
  **fallback regex** (email/tarjeta-Luhn/teléfono/IBAN/IPv4/SSN): un DNI
  con formato raro puede no detectarse con el fallback. Usa email + IBAN
  para una prueba inequívoca.
- El `guardrail_event` guarda el detalle **enmascarado** por diseño — si
  ves la PII en crudo en la BD/log, es un fallo a reportar.

---

## `human_11_02` — Secret leakage bloquea exposición accidental

**Qué prueba**: cuando un agente genera código con un token hardcodeado
(intencional para el test), el guardrail `secret_leakage` lo detecta en
`post_llm`, la respuesta se redacta sustituyendo el token por un
marcador, y se alerta al admin con severity high.

**Precondiciones**:

- El guardrail `secret_leakage` activo (baseline; hooks `post_llm` +
  `post_tool`; default `redact`).
- Una **regla de alerta** de guardrails configurada en `/admin/guardrails`
  que dispare con `min_severity: high`.
- Login como `tenant_admin`.

**Pasos**:

1. Haz que un agente genere una respuesta con un **token hardcodeado**
   intencional — p.ej. una AWS access key (`AKIA...`), un token de GitHub
   (`ghp_...`) o un bloque PEM de clave privada en un snippet de código.
2. Observa la **respuesta**: el token debe aparecer **redactado**
   (`[REDACTED:aws_access_key]` o similar), nunca en crudo.
3. Abre `/admin/guardrails`: el evento `secret_leakage` aparece con
   **severity high**.
4. Verifica que tu regla de alerta **disparó** una notificación al admin
   (vía el sistema de notificaciones de Plan 10).

**Resultado esperado**: el guardrail `post_llm` detecta el patrón, la
respuesta se redacta con un marcador, y se alerta al admin con severity
high.

**Checklist**:

- [ ] El guardrail post_llm detecta el patrón.
- [ ] La respuesta se redacta sustituyendo el token por marcador.
- [ ] Alerta al admin con severity high.

**Pitfalls conocidos**:

- El detector combina **regex de familias conocidas + entropía Shannon**:
  un secreto "genérico" de baja entropía puede no marcarse. Usa una
  familia reconocida (AWS / GitHub / PEM / JWT) para una prueba clara.
- La **redacción nunca devuelve el secreto** (ni en `redacted_text` ni en
  los `spans`, que solo llevan offsets + familia): si ves el token en
  algún campo de la respuesta, repórtalo.
- La alerta al admin depende de una **regla de alerta** con el umbral y
  ventana correctos (task_11_21) + el dispatcher de Plan 10 vivo. Sin
  regla, el evento se registra pero no se notifica.

---

## `human_11_03` — Cost ceiling aborta ejecuciones caras

**Qué prueba**: una tarea con un techo de coste bajo que intenta gastar
más que su límite provoca que la siguiente llamada al LLM falle con
`budget_exceeded`, con un mensaje claro al equipo, y la tarea queda en
`blocked` con motivo explícito.

**Precondiciones**:

- El guardrail `cost_ceiling` activo con un umbral bajo configurado (el
  precio real lo inyecta el catálogo de Fase C; el umbral por
  llamada/acumulado se lee de `metadata`).
- Login como `tenant_admin`.

**Pasos**:

1. Configura una tarea / ejecución con un **techo de coste** deliberadamente
   bajo (el escenario del plan: "budget 1 € que intenta usar 2 € de
   tokens").
2. Lanza la tarea de modo que el coste acumulado **supere** el techo.
3. Observa la **siguiente llamada al LLM**: debe fallar con
   `budget_exceeded` (acción por defecto `block`).
4. Verifica el **mensaje al equipo**: debe explicar claramente que se
   alcanzó el límite de coste.
5. Comprueba el estado de la tarea: queda en **`blocked`** con el motivo
   explícito (cost ceiling superado).

**Resultado esperado**: la llamada que cruza el techo falla con
`budget_exceeded`, hay mensaje claro al equipo, y la tarea queda en
`blocked` con motivo.

**Checklist**:

- [ ] La siguiente llamada al LLM falla con budget_exceeded.
- [ ] Mensaje claro al equipo sobre el límite.
- [ ] La tarea queda en blocked con motivo explícito.

**Pitfalls conocidos**:

- El **sistema de Budgets de proyecto/tenant** (pausado automático al
  100 %, umbrales `[80, 90, 100]`) **NO se implementó** en este plan
  (hueco de alcance). Lo que sí funciona es el **guardrail
  `cost_ceiling`** con el umbral leído de `metadata`: prueba el guardrail,
  no el budget global.
- El coste por llamada usa el **snapshot de precio** congelado (Fase C):
  si el catálogo no tiene precio para el modelo, el snapshot es `unknown`
  (coste NULL) y el techo no dispara — siembra un precio en
  `/admin/model-prices` para el modelo bajo prueba.

---

## `human_11_04` — Sincronización de precios funciona

**Qué prueba**: tras editar un precio manualmente, pulsar 'Sincronizar
precios' muestra el diff entre el catálogo actual y el upstream, exige
confirmación explícita si alguna subida supera el 10 %, tras aplicar los
nuevos cálculos de coste reflejan los precios actualizados, y el audit log
registra qué cambió, quién y desde dónde.

**Precondiciones**:

- Login como **`system_admin`** (el catálogo y el sync son operaciones de
  sistema; un `tenant_admin`/`member` recibe 403).
- Salida a Internet hacia el JSON público de LiteLLM
  (`model_prices_and_context_window.json`) — usado **solo como fuente de
  datos**, ADR 0021; el sistema no usa LiteLLM como runtime.

**Pasos**:

1. En `/admin/model-prices`, **edita un precio manualmente** (crea/edita
   una fila `source=manual`) para tener un punto de comparación.
2. Pulsa **'Sincronizar precios'** → abre el diálogo de **diff
   (dry-run)**: tabla `old → new` con el % de cambio por modelo y
   contadores por estado (added / updated / unchanged / increased /
   removed). En este paso **no se escribe nada**.
3. Si algún modelo sube **> 10 %**, aparece un **gate de confirmación
   explícito** (checkbox): el botón "Aplicar" queda deshabilitado hasta
   marcarlo.
4. Confirma y **aplica**. Comprueba que los precios se actualizan con
   effective-dating (cierra el periodo viejo, abre uno nuevo); tu fila
   `source=manual` **no** se pisa salvo overwrite explícito.
5. Verifica que un **nuevo cálculo de coste** (snapshot de una ejecución
   posterior) refleja los **precios actualizados**.
6. Abre el **histórico de sync / audit** (`GET .../sync/audit` o el panel
   de la pantalla): debe mostrar **qué cambió, quién lo hizo
   (`user:<uuid>` o `scheduler`) y desde dónde** (trigger manual/scheduled,
   feed_url, contadores).

**Resultado esperado**: diff visible, confirmación obligatoria si alguna
subida supera el 10 %, precios aplicados que alimentan los nuevos
cálculos de coste, y audit log con quién/qué/desde dónde.

**Checklist**:

- [ ] El sistema muestra diff entre catálogo actual y upstream.
- [ ] Si subida >10%, requiere confirmación explícita.
- [ ] Tras aplicar, los nuevos cálculos de coste reflejan precios
      actualizados.
- [ ] Audit log muestra qué cambió, quién lo hizo, desde dónde.

**Pitfalls conocidos**:

- El feed de LiteLLM es **solo un proveedor de datos** (ADR 0021): el
  sistema NO lo usa como runtime. Si el fetch falla, el endpoint devuelve
  **502** — comprueba salida a Internet o configura
  `Settings.litellm_price_feed_url` a una copia local.
- El gate de confirmación **solo aparece si hay una subida > 10 %**: si
  tu diff no tiene ninguna, el "Aplicar" está habilitado directamente —
  eso es correcto, no un fallo.
- El cálculo de coste de **ejecuciones pasadas NO cambia**: el snapshot se
  congela por ejecución (auditoría histórica). Solo las ejecuciones
  **posteriores** al sync usan el precio nuevo.
- Escribir el catálogo es **System Admin** (sesión BYPASSRLS): si entras
  como `tenant_admin` verás el catálogo (lectura global) pero el botón
  'Sincronizar precios' / editar dará 403.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/11-guardrails-precios.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/11-guardrails-precios.md`](../../07-changelog/) y
   las referencias [`docs/04-reference/guardrails.md`](../../04-reference/)
   y [`docs/04-reference/pricing.md`](../../04-reference/).
3. Verifica que el PR `plan/11-guardrails-precios` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                         | Causa probable                                                | Fix                                                                       |
| ----------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------- |
| La PII no se enmascara                          | Presidio ausente y el formato cae fuera del fallback regex    | Instala `shared-guardrails[pii]` o usa email + IBAN (cubiertos por regex) |
| El secret no se redacta                         | Secreto genérico de baja entropía bajo el gate                | Usa una familia conocida (AWS/GitHub/PEM/JWT)                             |
| No llega alerta al admin                        | No hay regla de alerta o el dispatcher de Plan 10 está caído  | Crea una regla en `/admin/guardrails`; comprueba el dispatcher            |
| `cost_ceiling` no dispara                       | El modelo no tiene precio en el catálogo → snapshot `unknown` | Siembra el precio del modelo en `/admin/model-prices`                     |
| 'Sincronizar precios' devuelve 502              | El feed de LiteLLM no es alcanzable                           | Verifica salida a Internet o apunta a una copia local del JSON            |
| El botón 'Aplicar' del diff sigue deshabilitado | Hay una subida >10% y no marcaste el checkbox de confirmación | Marca el gate de confirmación explícito; entonces se habilita             |

Errores transversales viven en `docs/03-guides/gotchas/`.
