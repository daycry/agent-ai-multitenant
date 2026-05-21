---
adr: "0002"
title: Sesiones server-side en Redis (no JWT stateless)
status: accepted
date: 2026-05-20
deciders: System Architect
phase: 00-fundaciones
---

# ADR 0002 — Sesiones server-side en Redis (no JWT stateless)

## Contexto

Los tokens JWT puramente stateless (firmados, no persistidos) son
populares por su simplicidad, pero tienen una propiedad indeseable
en entornos donde **revocar acceso inmediatamente** es crítico:

- Si un atacante captura un JWT válido, sigue funcionando hasta su
  expiración por mucho que el operador "deslogue" al usuario.
- Cambiar la `jwt_secret` invalida **todos** los tokens en circulación
  —demasiado nuclear para un incidente puntual.

Los escenarios donde necesitamos revocación inmediata:

- Empleado deja la empresa → su sesión debe morir hoy, no mañana.
- Token comprometido → cerrar la sesión sin afectar a los demás.
- Logout explícito → expectativa razonable del usuario.

## Decisión

Emitir JWT **firmados** pero **vinculados a una sesión Redis** vía
un claim `sid` (session id). El servidor mantiene la sesión:

- En login, generar un `sid` UUID v7 y un JWT con `sub`, `sid`, `iat`,
  `exp`, opcionalmente `tid` y `sys`.
- Persistir el `sid` en Redis (`SET session:<sid> '{...}' EX <ttl>`).
- En cada request autenticada, `get_principal` decodifica el JWT y
  comprueba que el `sid` sigue en Redis. Si no, `401`.
- En logout, `DEL session:<sid>`. La siguiente request con el mismo
  JWT falla al instante.

## Alternativas descartadas

1. **JWT stateless + lista de revocados.** Mantener una blacklist
   tiene casi el mismo coste operacional que las sesiones, pero
   añade una asimetría (la lista crece linealmente con incidentes).
2. **Cookies firmadas con sesión completa en el lado servidor (sin
   JWT).** Más simple para web, pero peor para APIs y clientes
   no-browser (mobile, CLI...). El JWT está aquí más por compatibilidad
   con clientes que por sus propiedades de seguridad.
3. **JWT con TTL muy corto (5 min) + refresh.** El refresh-token aún
   necesita revocación, así que volvemos al mismo problema.

## Consecuencias

Positivas:

- Logout funciona en `O(1)` desde el momento del `DEL`.
- Auditoría centralizada (`sessions` table) registra creación,
  uso, revocación.
- Permite invalidar todas las sesiones de un user (revocar membership)
  sin tocar `jwt_secret`.

Negativas / cuidados:

- Una lectura extra a Redis por request autenticada (≈0.5 ms).
- Si Redis cae, toda la auth cae con él. Mitigado por:
  - Redis con persistencia (AOF + RDB).
  - Watchdog reinicia Redis automáticamente.
  - Operativamente, Redis es parte del stack core; perderlo es un
    incidente mayor por más motivos que la auth.
- El JWT lleva información (claim `sys` para System Admin) que NO
  se revalida en cada request. Si un user pierde el rol admin, el
  token seguirá teniéndolo hasta `exp` o `logout`. Aceptado en Fase
  0 — la mitigación llega en Fase 1+ (revalidación por rol).

## Referencias

- `apps/api-server/src/api_server/auth/sessions.py` — `SessionStore`.
- `apps/api-server/src/api_server/auth/deps.py` — `get_principal`
  verifica el `sid`.
- Sección 17.3 del documento maestro.
