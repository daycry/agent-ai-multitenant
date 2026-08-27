---
adr_id: "0074"
title: "Rol system_owner y Córtex: identidad global singleton, tablas tenant-less sobre BYPASSRLS"
status: accepted
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0021", "0064", "0069", "0075", "0076", "0156", "0157"]
supersedes: []
---

# ADR 0074 — Rol `system_owner` y Córtex: identidad global singleton sobre BYPASSRLS

> **Estado: `accepted` + IMPLEMENTADO — F0 el 2026-06-23, F1-F5 entre el 2026-06-24 y el
> 2026-07-06.** El operador aprobó primero el **cimiento F0** (rol `system_owner`:
> `users.is_system_owner` singleton, claim JWT `own`, `require_system_owner` DB-authoritative,
> bootstrap del primer usuario,
> `/me`), que **NO** crea tablas BYPASSRLS ni bucles autónomos; y dio después luz verde a **F1-F5**
> (memoria cognitiva, afecto, identidad, autonomía, voz), que sí introducen la excepción al
> Principio 1 y egress/coste autónomos.
>
> **Banner corregido el 2026-07-30: decía «F1-F5 `proposed` (gated)» y «siguen requiriendo
> aprobación por fase antes de implementar» con las cinco fases desplegadas.** Era el mismo
> defecto que la auditoría del córtex describe como «documentos que afirman que algo no existe
> mientras el código está desplegado». Lo implementado, fase por fase, con sus divergencias
> declaradas: [índice de fases](../roadmap/cortex-fases.md) y las cinco entradas de changelog
> ([F1](../07-changelog/cortex-f1-memoria-cognitiva.md),
> [F2](../07-changelog/cortex-f2-afectivo.md),
> [F3](../07-changelog/cortex-f3-identidad.md),
> [F4](../07-changelog/cortex-f4-autonomia.md),
> [F5](../07-changelog/cortex-f5-voz-avatar.md)).
>
> Que las fases estén implementadas **no las declara cerradas**: F2-F5 conservan casillas abiertas
> con hueco identificado en [gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md), y la
> más relevante para la seguridad de este ADR es que **F4 salió sin owner-approval gate ni tope de
> gasto en USD** — razón por la que `cortex.autonomy_enabled` sigue OFF.
>
> **Sobre el `status`: era `accepted-f0` y pasó a `accepted` el 2026-08-27.** Ese valor fue
> el único del repo —los otros 132 ADR usan `accepted`— y se conservaba como registro de que
> este ADR se aprobó **en dos tiempos**: cimiento primero, excepción a RLS después. Se
> normaliza porque no era un estado, era una nota histórica escrita en el campo equivocado:
> `accepted-f0` no pertenece al vocabulario de estados del repo y no tenía **ni un solo
> consumidor** (`AdrMeta.status` es texto libre; ningún `.py`/`.yml`/`.sh`/`.ts` lo lee), así
> que no gateaba nada — sólo obligaba a diez documentos a explicar por qué existía. La traza
> de la aprobación en dos tiempos no se pierde: vive en este banner, que la cuenta con fecha
> y con más detalle del que cabe en un frontmatter. Tampoco se inventa un `accepted-f5`: el
> corpus no usa estados por fase, y las fases se trazan por su plan y su changelog.

## Contexto

El diseño del [Córtex del Owner](../roadmap/cortex-system-owner.md) introduce un asistente "mente sintética" para el dueño del despliegue, distinto del asistente de tenant. Hoy solo existe `is_system_admin` (claim `sys`, `require_system_admin`, `get_admin_session` que eleva RLS). No hay rol de "dueño", ni superficie owner-scoped, ni tablas singleton de plataforma.

## Decisión

1. **Rol como columna booleana global**, no valor del enum `UserRole` (que es por-membership y rompería RLS/SSO): `users.is_system_owner` (NOT NULL, `server_default false`) con **UNIQUE parcial `WHERE is_system_owner`** (invariante singleton).
2. **Cadena de auth** moldeada sobre `is_system_admin`: claim `own` en `encode_jwt`/`get_principal`; `AuthPrincipal.is_system_owner`; bootstrap del primer usuario; propagación login/MFA/SSO; **guardrail SSO** (no grantable por grupo); `is_system_owner` en `/me`.
3. **No redefinir `require_system_admin`** in-place a "admin OR owner": sobrecargar un primitivo
   que usa todo endpoint admin cambiaría en silencio quién entra en los 78 `Depends(...)` que hoy
   lo invocan (medido, no estimado). De aquí sale
   **`require_system_owner`** (la puerta del córtex), que es lo vigente de este punto.

   > **La otra mitad de este punto está RETIRADA — no la implementes (2026-07-30).** Este punto
   > prescribía además una compuesta **`require_admin_or_owner`**, y hoy escribirla pone **CI en
   > rojo**: la guarda
   > [`tests/unit/test_no_dead_authorization_gates.py`](../../tests/unit/test_no_dead_authorization_gates.py)
   > (`test_the_retired_composite_gate_is_gone`) falla en cuanto reaparece un
   > `def require_admin_or_owner(` en `auth/deps.py`.
   >
   > **Por qué se retiró.** Existió desde junio de 2026 con **cero** `Depends(...)` en todo
   > `apps/`: sólo la citaban su propia definición, una línea de test y esta prosa. Código muerto
   > en la superficie de **autorización** es el peor sitio donde tenerlo — venía con docstring
   > convincente y test verde, así que el siguiente que necesitara «admin o owner» la habría
   > cableado creyendo que estaba en uso y probada en producción, cuando no la había ejercitado
   > jamás una request real. Y no hacía falta: el bootstrap del primer usuario pone
   > `is_system_admin` **e** `is_system_owner` a la vez (`routers/auth.py`, con índice único
   > parcial que hace singleton al owner), así que el owner ya pasa por `require_system_admin`;
   > el único caso que la compuesta cubría —un owner que no sea admin— sólo se alcanza con un
   > `UPDATE` a mano en la base de datos.
   >
   > **Si algún día hace falta de verdad**, se reconstruye en cuatro líneas sobre
   > `_is_db_system_admin` / `_is_db_system_owner` —que siguen en `auth/deps.py` y sí tienen
   > llamantes— y se repone **junto con el endpoint que la usa**, actualizando esa aserción en el
   > mismo cambio. Lo que no se repone es una puerta sin endpoint.

4. **Revocación estricta:** las dependencias del córtex **verifican `is_system_owner` contra BD por request** (no solo el claim).
5. **Tablas del córtex tenant-less** (`cortex_*`): no llevan `tenant_id`, se acceden vía
   `get_admin_sessionmaker` (BYPASSRLS) y aíslan por **`owner_user_id` explícito en SQL**. Es una
   **excepción consciente al Principio 1** y exige **test cross-owner**.

   > **Corregido el 2026-08-19 por el [ADR 0156](0156-aislamiento-estructural-del-cortex.md):
   > este punto decía «tablas SIN RLS», y eso ya no describe el sistema.** El error de fondo era
   > una inferencia que parecía razonable y no lo era: _el córtex no es un recurso de tenant ⇒ no
   > lleva RLS_. De «el eje no es el tenant» no se sigue «no hay eje», sino **«el eje es otro»**.
   > Las migraciones `0125` (para `cortex_conversations`) y `0140_cortex_owner_rls` (para las
   > cinco restantes) pusieron `ENABLE` + `FORCE` + policy `owner_user_id = app.user_id` en las
   > **seis** tablas del subsistema. El filtro explícito en SQL **sigue siendo obligatorio** —es
   > el que de verdad aísla hoy: ver la consecuencia ⚠️ de abajo, que explica por qué esa policy
   > todavía no protege al tráfico real—, y el test cross-owner también.

## Consecuencias

- ✅ Cambio de menor radio de impacto sobre la authz existente; singleton garantizado por constraint.
- ⚠️ **El aislamiento efectivo lo sostiene el filtro `owner_user_id` explícito + los tests, no la
  RLS.** La consecuencia original decía «introduce tablas sin RLS»; desde el ADR 0156 las seis
  tablas tienen policy de eje owner, pero **para los caminos reales de hoy esa policy es inerte**,
  y conviene decirlo así en vez de anunciar dos capas que el tráfico actual no tiene: los 27
  accesos de `routers/cortex.py` y `routers/cortex_voice.py` abren `get_admin_sessionmaker()`
  (`migrations_user`), los workers del córtex van por `WORKERS_DATABASE_URL`
  (`service_user`/`migrations_user`) y el `pg_dump` del backup igual — y **BYPASSRLS se salta la
  RLS incluso con `FORCE`** (medido contra este PostgreSQL 16, no leído de la documentación). Lo
  que la policy cierra es el camino `app_user`: hasta entonces completamente abierto —`02-roles.sh`
  le concede CRUD sobre toda tabla que cree Alembic por `ALTER DEFAULT PRIVILEGES`— y hoy sin usar
  para estas tablas, pasa a **fail-closed**. Es decir: una red de seguridad para el endpoint futuro
  (o la inyección) que se conecte con la sesión normal, no una segunda capa bajo el tráfico actual.
  **Sigue siendo punto crítico de auditoría**, y lo que hay que auditar es el filtro explícito.
- ⚠️ Un segundo "owner" es imposible por constraint (decisión deliberada: el córtex es del dueño del despliegue).

## F3 — identidad evolutiva (anotado el 2026-08-19)

**Anotado desde la casilla F3.7 del plan [cortex-f3-identidad](../roadmap/cortex-f3-identidad.md).**
La fase que materializa el «córtex singleton» de este ADR es F3: `cortex_identity`
(una fila por owner, `uq_cortex_identity_owner`) + `cortex_identity_history`
(versionado append-only con `diff`), migración `0094_cortex_identity`. Tres cosas de
este ADR que F3 concreta, y una que lo corrige:

1. **El guardrail de auto-modificación existe y es determinista**: `clamp_traits` /
   `clamp_baseline` / `bounded_update` con `BASELINE_MAX_DELTA_PER_REFLECTION = 0.05`
   por ciclo (`cortex/identity.py`). Un ciclo de reflexión no puede derivar la
   identidad de golpe, y el `diff` de cada versión deja auditable qué movió.
2. **El punto 5 («aislamiento por `owner_user_id` explícito», test cross-owner
   obligatorio) se cumple** y tiene sus tests
   (`tests/integration/test_cortex_f3_identity_endpoints.py`,
   `test_cortex_identity.py`).
3. **Pero la mitad «tablas sin RLS» de ese punto 5 ya NO describe el sistema**: el
   [ADR 0156](0156-aislamiento-estructural-del-cortex.md) + la migración
   `0140_cortex_owner_rls` (2026-08-19) pusieron RLS de eje owner (`ENABLE` + `FORCE` +
   policy `owner_user_id = app.user_id`) en las seis tablas del córtex. **La corrección
   está aplicada arriba, en el punto 5 y en su consecuencia ⚠️, que es donde se lee.**

   Y con una rebaja respecto a como se anotó en agosto: aquí llegó a decirse que «el
   aislamiento es hoy de dos capas, no de una». Para la ruta principal eso es **falso**, y
   creérselo es exactamente el modo de fallo que esta reparación persigue —confianza
   injustificada en una defensa que no está actuando—. El córtex accede a todo por
   `get_admin_sessionmaker()`, que es **BYPASSRLS**, y el propio docstring de la migración
   que creó la policy lo admite: «para los consumidores de hoy esta policy es inerte». La
   segunda capa existe **estructuralmente** y protege el camino `app_user`, hoy sin usar;
   bajo el tráfico real sigue habiendo **una sola** capa, el filtro `owner_user_id` explícito.

4. **Qué puede tocar el owner a mano, dicho con precisión**: el
   [ADR 0157](0157-quien-reescribe-la-narrativa-del-cortex.md) resolvió la
   contradicción que F3 arrastraba desde junio. La frontera **no** es «lo
   autobiográfico» sino **lo acotado**: el owner co-diseña la prosa
   (`name`/`core_values`/`narrative`/`language`/`learning_goals`) y no escribe a mano
   el estado derivado numérico (`traits`, `mood_baseline`, `relationship_model`,
   `affect_params`) — 422, porque un número escrito a mano rompería en silencio la
   cota del punto 1 y convertiría el histórico en un registro falso de cómo evolucionó.

Detalle de lo entregado y de las divergencias:
[changelog de F3](../07-changelog/cortex-f3-identidad.md).
