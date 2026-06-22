---
adr_id: "0074"
title: "Rol system_owner y Córtex: identidad global singleton, tablas tenant-less sobre BYPASSRLS"
status: proposed
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0021", "0064", "0069", "0075", "0076"]
supersedes: []
---

# ADR 0074 — Rol `system_owner` y Córtex: identidad global singleton sobre BYPASSRLS

> **Estado: `proposed`** — toca el modelo de roles/auth y crea una **excepción consciente al Principio 1 (RLS)**. **Requiere aprobación del operador antes de implementar.**

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
