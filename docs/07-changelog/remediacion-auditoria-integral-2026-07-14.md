---
plan_id: remediacion-auditoria-integral-2026-07-14
title: Remediación delta de auditoría integral — seguridad, contratos y rendimiento
completed_at: null
docs_language: es
---

# Plan remediacion-auditoria-integral-2026-07-14 — Remediación delta

## Resumen

Diez tareas nacidas de la auditoría integral del 2026-07-14. Seis se cerraron
entre julio y el 2026-08-01; las **cuatro últimas** (`02`, `03`, `04`, `05`) más
`09` y `10` se entregaron el **2026-08-19**.

Lo que une a casi todas es el patrón que este repo tiene documentado como
dominante: **mecanismo entregado, cero llamantes**. Un `/readyz` correcto que no
consultaba nadie. Un campo de modelo de embeddings que la pantalla enseñaba y que
no gobernaba ninguna llamada. Cinco tablas del córtex con el aislamiento confiado
a que cada query recordase su filtro. En los tres casos el código «existía»; lo
que faltaba era el último tramo de cable.

> **Estado: `pending_human_validation`.** Todas las casillas están marcadas y
> todo lo automático está en verde, pero cerrar el plan exige tests humanos y el
> PR mergeado, que no son de un agente.

## Cambios por tarea

### `task_audit14_02` + `task_audit14_03` — el córtex tenía un eje sin defender

**ADR 0156** (`accepted`) y **migración `0140_cortex_owner_rls`**.

De las seis tablas del córtex, sólo `cortex_conversations` llevaba RLS (se la puso
la `0125`). Las otras cinco —incluida `cortex_turns`, donde vive el **texto
literal de las conversaciones del System Owner**, más su identidad, su estado
afectivo y lo que investiga— no tenían defensa estructural, mientras `app_user`
(NOBYPASSRLS, el rol del tráfico normal) tiene DML sobre ellas por los _default
privileges_.

La inferencia que falló, y que el ADR corrige: «el córtex no es un recurso de
tenant ⇒ no lleva RLS». De que el eje no sea el tenant no se sigue que no haya
eje, sino que hay que defender **el que sí es**: `owner_user_id = app.user_id`,
con `ENABLE` + `FORCE` + policy en las cinco.

- Los filtros `owner_user_id` de aplicación **se conservan**: siguen siendo la
  única capa que actúa con roles BYPASSRLS.
- El invariante nº 5 de `test_rls_invariant.py` convierte cinco justificaciones de
  allowlist en aserciones.
- **Dos tests había que corregir, y no relajándolos**: `test_cortex_identity.py` y
  `test_cortex_affect_store.py` afirmaban `relrowsecurity is False` sobre tablas
  que el ADR acaba de proteger — tests que fijaban el defecto, heredados del ADR 0074. Ahora piden más: RLS + FORCE + que la policy cuelgue de `app.user_id`.
- Premisa que queda vigilada por escrito: todo esto se apoya en que los roles de
  servicio son BYPASSRLS. El día que dejen de serlo, la salida es cablear el GUC
  en `get_admin_sessionmaker`, **no** relajar las policies.

### `task_audit14_04` + `task_audit14_05` — la pantalla de KBs enseñaba un modelo que nunca se usó

**ADR 0155** (`accepted`, opción A: un modelo por plataforma) y migración
**`0141_kb_embedding_canonical`**.

Medido contra la base de datos del stack en marcha: 14 KBs selladas
`nomic-embed-text-v1.5`, **0 documentos y 0 chunks**, y los dos servicios
mandando `nomic-embed-text` a Ollama. La etiqueta que enseñaba la ficha **no es
un tag válido del registro**, así que jamás se envió a ningún sitio; y el 409 de
inmutabilidad protegía un campo que no gobernaba nada.

El peligro real no es la dimensión —eso falla ruidosamente— sino **dos modelos de
768 dimensiones distintos**: mismo tamaño, otro espacio semántico, un `<=>`
perfectamente válido y sin sentido, cero errores y recall peor.

Cinco reglas, todas cableadas:

1. `ingestion/embedding_contract.py` es el **único** sitio que compara strings de
   modelo (canoniza, enumera grafías, resuelve el activo).
2. La API sella el modelo activo y devuelve **422** ante cualquier otro; el 409 se
   conserva sólo donde significa algo: re-sellar una KB **con** chunks.
3. La respuesta devuelve el sello canonizado + `platform_embedding_model` +
   `embedding_model_stale`: la pantalla dejó de mentir sin esperar a la migración.
4. `ingest_document` compara sello vs activo **antes** de bajar bytes, escanear o
   embeber; si divergen, documento `failed` con los dos modelos en el mensaje.
5. El camino vectorial filtra por espacio de embeddings; **BM25 sigue viendo esos
   chunks**, así que la KB no se vuelve invisible.

La migración alinea el valor **almacenado** (lo que ve quien abre la tabla con
`psql` o restaura un backup). Su `downgrade` repone el default anterior pero **no
deshace el `UPDATE`**: revertirlo repondría una etiqueta que no identifica a
ningún modelo servible.

### `task_audit14_07` — el cliente lento colgaba el pump, y con él la re-validación

`_pump` de `routers/ws.py` enviaba sin deadline. Ahora cada envío pasa por
`asyncio.wait_for` (`API_SERVER_WS_SEND_TIMEOUT_SECONDS`, 10 s; 0 lo desactiva),
un consumidor lento se cierra con **1013 «Try Again Later»** —código elegido
porque invita a reconectar, y el cliente ya reconecta con backoff— y `reader` /
`xread` se cancelan **y se esperan** en todos los caminos de salida.

Lo más grave del hallazgo no estaba en el enunciado: mientras el pump se colgaba
en el `send` **no volvía al principio del bucle**, o sea que dejaba de re-validar
la credencial. La garantía de prod-09 —«el logout cierra los sockets abiertos»— se
caía justo para el cliente que peor se comporta. `cortex_ws` hereda el arreglo
porque importa `_pump`.

El cierre lleva el **mismo** deadline: el frame CLOSE viaja por el socket
atascado, así que un `close` sin tope reproducía el cuelgue una línea más abajo.

### `task_audit14_08` — `/readyz` estaba bien y no lo consultaba nadie

El endpoint ya tenía checks críticos, deadline por check, 503 estructurado sin
secretos y recuperación sin reinicio. **No se reimplementó nada.** Lo que faltaba
era el consumidor: `grep readyz` no daba un solo hit en `docker/`, ni en el
generador de compose, ni en el proxy.

Se cableó al **proxy** (`health_uri /readyz` en el Caddyfile generado y en el de
manuales) y el healthcheck del **contenedor** se queda en `/healthz` a propósito:
Docker admite uno solo y el watchdog reinicia lo `unhealthy`, así que apuntarlo a
readiness convertiría «se cayó PostgreSQL» en «la api-server se reinicia en
bucle». El test afirma **las dos** mitades, para que la «mejora obvia» no pase en
verde.

### `task_audit14_09` — los ocho warnings eran el mismo defecto cinco veces

`const xs = query.data ?? []` devuelve un array nuevo en cada render mientras la
consulta no ha respondido: con esa variable en las dependencias, el `useMemo` no
memoiza y el `useEffect` corre de más.

Además, matriz **fijada** en las dos apps Next (`next` 15.5.23 ·
`eslint-config-next` 15.5.23 · `eslint` 8.57.1 · `typescript` 5.9.3, con el
lockfile coincidiendo) y `npm run lint` convertido en **gate**
(`--max-warnings=0`): sin esa bandera `next lint` sale con código 0 aunque emita
warnings, que es exactamente cómo estos ocho vivieron meses en verde.

### `task_audit14_10` — referencias y runbooks sincronizados

Actualizadas las referencias de multi-tenancy, KB/embeddings, healthchecks y
workers; `stack-services.md` gana el **watchdog**, que ya era servicio de compose
y no aparecía. El gotcha del naming de embeddings **afirmaba lo contrario** de lo
decidido —institucionalizaba la divergencia—, y se reescribió.

El punto MCP de `analisis-diferidos-2026-07-12.md` se marcó resuelto con su
evidencia, **dejando el texto original intacto**: es un informe fechado y
reescribirlo por dentro borraría que el defecto existió.

## Verificación

- `pytest tests/unit` → **4564 passed**, 1 skipped.
- `pytest tests/integration` (los 16 ficheros de la ola) + `tests/migrations` →
  **143 passed** en pasada serial, más los 4 casos de
  `test_kb_embedding_canonical_migration.py`.
- `scripts/mypy_gate.py` → 707 ficheros sin incidencias · `ruff check` limpio.
- `pytest tests/docs tests/unit/test_docs_governance.py` → 297 passed.
- Panel: `next lint` sin warnings, `tsc --noEmit` exit 0, 135 tests de las áreas
  tocadas.

## Lo que queda, y es de un humano

- **Tests humanos del plan** y **PR mergeado**: sin eso no se pasa a `completed`.
- **La migración `0140` y la `0141` no están aplicadas a ningún stack**: nadie ha
  desplegado.
- El botón «probar conectividad» de backup **nunca se ha ejercitado con un destino
  remoto con credencial** — ni antes ni después del cambio. Es la verificación que
  de verdad cierra `api-9`.

## Trampa documentada de camino

[`cuatro-shards-y-cinco-agentes-tumban-postgres.md`](../03-guides/gotchas/cuatro-shards-y-cinco-agentes-tumban-postgres.md)
— la primera pasada de la suite corrió en 4 shards mientras cinco agentes escribían
en el árbol, y tumbó PostgreSQL por memoria del anfitrión. De sus ocho rojos,
cinco pasaron al repetirlos en serie: eran contaminación. Un veredicto sobre un
árbol en movimiento no distingue un defecto de una edición a medias.
