---
adr: "0042"
title: Hardening del panel admin solo en producción — MFA obligatorio, IP allowlist por CIDR y sesiones cortas de 15 minutos, sin romper dev ni a usuarios no-admin
status: accepted
date: 2026-05-31
deciders: System Architect, Security, Backend Dev
phase: 15-instalador-produccion
---

# ADR 0042 — Hardening del panel admin (solo prod): MFA obligatorio + IP allowlist + sesiones de 15 min

> **Estado: `accepted`.** Recoge la decisión tomada durante el Plan 15 (Fase C —
> endurecimiento de seguridad, `task_15_18`) de endurecer la superficie de
> **System Admin (`/admin/*`)** con tres controles **activos solo en
> staging/prod**: **MFA obligatorio** (forced-enrollment gate), **IP allowlist
> por CIDR** y **sesiones cortas (15 min por defecto)**. Profundiza el patrón
> cross-tenant de System Admin (**ADR 0010**), reusa las sesiones server-side de
> Redis (**ADR 0002**), el reto MFA del SSO empresarial (**ADR 0031**) y la
> semántica CIDR de los api-tokens (**ADR 0037**).

## Contexto

La superficie `/admin/*` es el objetivo de mayor valor de la plataforma: un
System Admin opera cross-tenant sobre la sesión BYPASSRLS (ADR 0010). Hasta el
Plan 15 esa superficie usaba el mismo TTL de sesión y el mismo gate de auth que
el resto. El hardening de producción exige controles adicionales **sin romper
el dev local ni el login de un usuario no-admin**.

Cuestiones de diseño no cerradas por ADRs previos:

1. **¿Se exige MFA siempre, o solo en producción?** Forzar MFA en dev local
   rompería el flujo de desarrollo.
2. **¿Cómo se restringe el origen del acceso admin** sin reinventar el parsing
   de CIDR?
3. **¿Cómo se acota la ventana de una sesión admin secuestrada?**

## Decisión

Tres controles, **enforced ONLY en staging/prod** (dev se queda usable),
implementados en `api_server.auth.admin_hardening` como predicados puros + una
dependencia FastAPI `require_hardened_system_admin`:

1. **MFA obligatorio (`admin_hardening_enforced`).** Un admin sin un segundo
   factor inscrito + confirmado queda **bloqueado** de la superficie admin
   (forced-enrollment gate). Reusa la maquinaria MFA de la ADR 0031.
2. **IP allowlist (`admin_ip_allowed`).** El acceso admin se restringe a una
   **allowlist de CIDR configurable**, reusando la semántica CIDR de los
   api-tokens (ADR 0037).
3. **Sesiones cortas (`admin_session_expired`).** Una sesión admin más vieja que
   el TTL corto (**15 min** por defecto) se rechaza, forzando reautenticación.
   Reusa las sesiones server-side de Redis (ADR 0002).

Los tres se gobiernan por `Settings` y se **apagan en dev**: un `Settings` de dev
no arma ninguno de los tres, de modo que el desarrollo local sigue funcionando.
La dependencia **nunca atrapa a un usuario no-admin** (un `tenant_admin` /
`tenant_user` no entra en la superficie `/admin/*` y por tanto no ve el gate de
hardening). Los predicados son puros y unit-testeables; la dependencia se
ejercita end-to-end con un `SessionStore` en memoria y un lookup MFA mockeado
(sin Redis ni DB vivos).

## Alternativas consideradas

- **Enforce siempre (también en dev).** Rechazada: rompe el flujo de desarrollo
  local (cada arranque exigiría enrolar MFA + estar en la allowlist).
- **MFA opcional para admin.** Rechazada: la superficie de mayor valor es
  precisamente la que más necesita el segundo factor obligatorio.
- **Mismo TTL de sesión que el resto.** Rechazada: una sesión admin secuestrada
  con TTL largo es una ventana de ataque inaceptablemente amplia; 15 min acota la
  exposición.
- **Reimplementar parsing de CIDR.** Rechazada: la semántica CIDR de los
  api-tokens (ADR 0037) ya está probada; se reusa.

## Consecuencias

- **Positivas.** La superficie admin de producción exige segundo factor, origen
  de red conocido y reautenticación frecuente; dev local intacto; ningún usuario
  no-admin se ve afectado. Un retroceso (quitar un control, sobre-aplicar en dev,
  atrapar a un no-admin) hace fallar la suite de seguridad antes del merge.
- **Negativas / asunciones.** El enforcement real con Redis + MFA vivos es un
  **test humano / de stack** (`human_15_03`); CI valida los predicados + la
  dependencia con fakes deterministas. La allowlist de CIDR es responsabilidad
  operativa de configurarla correctamente (una allowlist vacía o mal puesta puede
  bloquear al propio operador — documentado en troubleshooting).

## Verificación

- `tests/security/test_admin_hardening.py` — predicados puros (`admin_hardening_enforced`,
  `admin_ip_allowed`, `admin_session_expired`) + la dependencia
  `require_hardened_system_admin` end-to-end con `SessionStore` en memoria + MFA
  mockeado; enforce solo en staging/prod; dev usable; no atrapa a no-admins.
