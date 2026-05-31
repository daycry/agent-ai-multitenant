---
adr: "0031"
title: SSO empresarial — modelo de sesión, dependencia nativa de SAML y token de reto MFA
status: accepted
date: 2026-05-30
deciders: System Architect, Security
phase: 08-sso-empresarial
---

# ADR 0031 — SSO empresarial: modelo de sesión, dependencia nativa de SAML y token de reto MFA

> **Estado: `accepted`.** Recoge tres decisiones arquitectónicas tomadas
> durante el Plan 08 que no estaban registradas en un ADR previo: cómo
> termina un login SSO respecto al modelo de sesión, cómo se maneja la
> dependencia nativa `xmlsec` de SAML, y la naturaleza del token de reto
> de MFA entre el primer y el segundo factor.

## Contexto

El Plan 08 añade OIDC, SAML 2.0, MFA (TOTP + WebAuthn), JIT provisioning,
SCIM y mapeo de grupos **en paralelo** al login local de la Fase 0. Tres
cuestiones de diseño no quedaban cerradas por ADRs previos:

1. **¿Cómo termina un login SSO?** El JWT stateless tras OIDC/SAML es un
   patrón habitual, pero el ADR 0002 ya decidió **sesiones server-side en
   Redis** para el login local (revocación inmediata). Había que decidir
   explícitamente si el SSO seguía ese modelo o introducía un camino
   distinto.
2. **`python3-saml` depende de `xmlsec`**, una extensión nativa C
   (libxml2/libxmlsec1) que no siempre está disponible en todos los nodos
   (p. ej. una imagen mínima, o Windows sin el wheel). Un `import` ansioso
   tumbaría el arranque de toda la api-server —incluido el login local y
   OIDC— solo porque falta una librería de un proveedor que ese despliegue
   quizá ni usa.
3. **MFA introduce un paso intermedio**: tras validar la contraseña (o el
   assertion SSO), si el usuario tiene un segundo factor confirmado, el
   sistema **no** debe emitir todavía una sesión, pero necesita correlacionar
   la verificación del segundo factor con el primer factor ya superado.
   ¿Qué credencial transporta ese estado intermedio?

## Decisión

### 1. El SSO reutiliza el modelo de sesión Redis (no JWT stateless)

Un callback OIDC (`/auth/sso/oidc/callback`), un ACS SAML
(`/auth/sso/{tenant_id}/saml/acs`) y un `verify` de MFA terminan en el
**mismo** helper `_issue_session`: una sesión server-side en Redis
(`SessionStore`) + un JWT (`encode_jwt`), idéntico en forma al login local.

Consecuencia: logout, revocación, el chequeo de `sid` en `get_principal` y
la revocación de sesiones por deprovisioning SCIM funcionan **igual** sea
cual sea el método de autenticación. No existe un camino "JWT stateless tras
OIDC". Esto extiende el ADR 0002 al dominio SSO y es coherente con la
"Decisión Clave" del propio Plan 08.

### 2. `python3-saml` se importa de forma perezosa; sin `xmlsec` → 501

El backend SAML (`auth/sso/saml.py`) importa `python3-saml`/`xmlsec`
**dentro** del flujo, no al cargar el módulo. Un helper `saml_available()`
detecta si el backend nativo está presente:

- **Presente** → el flujo SAML corre completo (firma de AuthnRequest,
  verificación y descifrado del assertion con SHA-256).
- **Ausente** → los endpoints SAML que necesitan cripto nativa devuelven
  **`501 Not Implemented`** (guard `_require_saml_available`), y el resto de
  la auth (login local, OIDC, MFA) **sigue funcionando con normalidad**.

La superficie que **no** necesita `xmlsec` se mantiene operativa en todos
los nodos: el CRUD de configuración SAML, la validación de invariantes
(`validate_saml_security`) y el parseo de metadata del IdP
(`parse_saml_idp_metadata`, lxml endurecido anti-XXE).

Esto evita acoplar la disponibilidad de la api-server a una dependencia
nativa de un proveedor opcional, manteniendo el principio "añadir SSO sin
romper la auth existente".

### 3. El token de reto MFA es un artefacto efímero, no una sesión

Cuando el primer factor (contraseña o SSO) tiene éxito y el usuario tiene un
segundo factor confirmado, el endpoint **no** emite una sesión: devuelve
`{status: "mfa_required", mfa_token: ...}`. Ese `mfa_token`:

- vive en Redis como un registro de reto **single-use** (`GETDEL` atómico,
  como el `state` OIDC y el reto WebAuthn),
- tiene un TTL corto (`mfa_challenge_ttl_seconds`, default 300 s),
- **no concede acceso a nada** — solo correlaciona el primer factor ya
  superado con la verificación del segundo,
- se canjea exactamente una vez en `/auth/mfa/totp/verify` (o el `finish`
  de WebAuthn), que es el ÚNICO punto que acuña la sesión real vía
  `_issue_session`.

Es deliberadamente distinto de un JWT/sesión: un reto robado caduca en
segundos, es de un solo uso y no autoriza ninguna operación por sí mismo.

## Alternativas consideradas

- **JWT stateless tras OIDC/SAML.** Descartada por las mismas razones del
  ADR 0002: imposibilita la revocación inmediata (logout, deprovisioning
  SCIM, empleado que se va) sin rotar el `jwt_secret` global.
- **Importar `python3-saml` al cargar el módulo y exigir `xmlsec` siempre.**
  Descartada: acopla el arranque de toda la auth a una dependencia nativa de
  un proveedor opcional; un despliegue que solo usa OIDC no debería necesitar
  `libxmlsec1`.
- **Reutilizar una sesión "a medias" como token de reto MFA.** Descartada:
  una sesión —aunque marcada— ya es revocable/auditada como acceso y se
  prestaría a saltarse el segundo factor; el reto efímero single-use deja la
  invariante "sin segundo factor no hay sesión" imposible de eludir.

## Consecuencias

### Positivas

- Un único modelo de sesión para local/OIDC/SAML/MFA: logout y revocación
  uniformes, una sola superficie de auditoría.
- La api-server arranca y sirve login local + OIDC aunque falte `xmlsec`;
  SAML degrada limpio a 501 en vez de romper todo.
- El segundo factor es ineludible: el primer factor por sí solo nunca
  produce una sesión.

### Negativas / cuidados

- El camino cripto nativo de SAML solo se ejerce en nodos con `xmlsec`; CI y
  prod deben garantizar el backend nativo donde SAML esté en uso (los tests
  cripto están marcados como bloqueados-por-xmlsec).
- Una lectura/escritura extra a Redis por login con MFA (el reto), aceptable
  frente a la garantía de seguridad.

## Referencias

- `apps/api-server/src/api_server/routers/sso.py` — `_issue_session`,
  `_require_saml_available`.
- `apps/api-server/src/api_server/auth/sso/saml.py` — `saml_available()`,
  import perezoso.
- `apps/api-server/src/api_server/auth/sso/state_store.py` — stores Redis
  single-use (`GETDEL`).
- `apps/api-server/src/api_server/routers/mfa.py` — flujo de reto MFA.
- ADR 0002 — sesiones server-side en Redis (decisión base que este ADR
  extiende al SSO).
- `docs/07-changelog/08-sso-empresarial.md` — changelog del plan.
