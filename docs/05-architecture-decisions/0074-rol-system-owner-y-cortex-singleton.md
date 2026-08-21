---
adr_id: "0074"
title: "Rol system_owner y Córtex: identidad global singleton, tablas tenant-less sobre BYPASSRLS"
status: accepted-f0
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0021", "0064", "0069", "0075", "0076"]
supersedes: []
---

# ADR 0074 — Rol `system_owner` y Córtex: identidad global singleton sobre BYPASSRLS

> **Estado: `accepted-f0` + IMPLEMENTADO — F0 el 2026-06-23, F1-F5 entre el 2026-06-24 y el
> 2026-07-06.** El operador aprobó primero el **cimiento F0** (rol `system_owner`:
> `users.is_system_owner` singleton, claim JWT `own`,
> `require_system_owner`/`require_admin_or_owner` DB-authoritative, bootstrap del primer usuario,
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
> **Sobre el valor `accepted-f0` del frontmatter:** es el único del repo (los otros 132 ADR usan
> `accepted`) y se conserva a propósito, como registro de que este ADR se aprobó **en dos tiempos**
> —cimiento primero, excepción a RLS después— y no de una vez. No se ha inventado un `accepted-f5`:
> el corpus no usa estados por fase, y las fases se trazan por su plan y su changelog, no por el
> `status` del ADR.

## Contexto

El diseño del [Córtex del Owner](../roadmap/cortex-system-owner.md) introduce un asistente "mente sintética" para el dueño del despliegue, distinto del asistente de tenant. Hoy solo existe `is_system_admin` (claim `sys`, `require_system_admin`, `get_admin_session` que eleva RLS). No hay rol de "dueño", ni superficie owner-scoped, ni tablas singleton de plataforma.

## Decisión

1. **Rol como columna booleana global**, no valor del enum `UserRole` (que es por-membership y rompería RLS/SSO): `users.is_system_owner` (NOT NULL, `server_default false`) con **UNIQUE parcial `WHERE is_system_owner`** (invariante singleton).
2. **Cadena de auth** moldeada sobre `is_system_admin`: claim `own` en `encode_jwt`/`get_principal`; `AuthPrincipal.is_system_owner`; bootstrap del primer usuario; propagación login/MFA/SSO; **guardrail SSO** (no grantable por grupo); `is_system_owner` en `/me`.
3. **No redefinir `require_system_admin`** in-place a "admin OR owner" (sobrecarga un primitivo usado en todo endpoint admin). Crear **`require_admin_or_owner`** (compuesta) y `require_system_owner` (córtex).
4. **Revocación estricta:** las dependencias del córtex **verifican `is_system_owner` contra BD por request** (no solo el claim).
5. **Tablas del córtex tenant-less** (`cortex_*`): se acceden vía `get_admin_sessionmaker` (BYPASSRLS) con **aislamiento por `owner_user_id` explícito en SQL** como defensa en profundidad. Es una **excepción consciente al Principio 1** y exige **test cross-owner**.

## Consecuencias

- ✅ Cambio de menor radio de impacto sobre la authz existente; singleton garantizado por constraint.
- ⚠️ Introduce tablas sin RLS — el aislamiento depende del filtro explícito + tests. Debe auditarse como punto crítico.
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
   policy `owner_user_id = app.user_id`) en las seis tablas del córtex. El aislamiento
   es hoy de **dos capas**, no de una, y la consecuencia ⚠️ de arriba —«el aislamiento
   depende del filtro explícito + tests»— queda atenuada por esa segunda capa.
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
