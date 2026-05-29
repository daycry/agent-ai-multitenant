---
plan_id: 08-sso-empresarial
title: SSO Empresarial y Auth Avanzada
status: pending_approval
blocking_plan: [00-fundaciones]
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 40-55
estimated_cost_human_eur: 16.000 € – 22.000 €
estimated_cost_ai_eur: 60 € – 100 €
created_by: system_architect
spec_sections_referenced: [20]
docs_language: es
---

# Plan 08 — SSO Empresarial y Auth Avanzada

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `08-sso-empresarial`                      |
| **Estado**                         | `pending_approval`                        |
| **Bloqueado por**                  | `00-fundaciones`                          |
| **Tiempo estimado (calendario)**   | 2-3 semanas                               |
| **Tiempo estimado (persona-días)** | 40-55                                     |
| **Previsión de coste — humano**    | 16.000 € – 22.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 60 € – 100 €                              |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/08-sso-empresarial`                 |
| **Secciones del .docx**            | [20]                                      |

---

## Descripción Detallada

### Resumen Ejecutivo

Integración OIDC (Azure AD, Google, Okta, Auth0, GitHub, GitLab, Apple, Facebook), SAML 2.0, LDAP opcional, MFA (TOTP + WebAuthn), JIT provisioning + SCIM. Configurable por tenant.

### Contexto

El auth básica de Fase 0 (user+password local) es suficiente para arrancar. Esta fase abre el sistema a organizaciones que requieren SSO empresarial.

### Alcance

**Entra en este plan**:

- Integración OIDC genérica + plantillas para Azure AD, Google Workspace, Okta, Auth0, GitHub, GitLab, Apple, Facebook.
- Integración SAML 2.0 (SP-initiated y IdP-initiated, firma y cifrado).
- LDAP opcional (sincronización periódica de usuarios).
- JIT (Just-In-Time) provisioning al primer login SSO.
- SCIM 2.0 para provisionamiento bidireccional con IdPs que lo soporten.
- MFA con TOTP (Google Authenticator, Authy) y WebAuthn (passkeys, YubiKey).
- Mapeo de grupos del IdP a roles del tenant.
- UI de configuración SSO por tenant (Tenant Admin).
- Email lookup / subdominio para descubrimiento de tenant en login.

**Queda fuera (otras fases)**:

- Federación cross-tenant (cada tenant configura su propio SSO).
- Passkey local sin servidor (queda para iteración posterior).

### Decisiones Clave

- Una sesión server-side en Redis sigue siendo el modelo, independientemente del SSO (no JWT stateless tras login OIDC).
- MFA opcional por tenant pero recomendado para Tenant Admin obligatorio.
- JIT provisioning crea usuario en primer login con rol default 'tenant_user', el Tenant Admin promueve después.

### Riesgos Identificados

| Riesgo                                        | Probabilidad | Impacto | Mitigación                                                                                             |
| --------------------------------------------- | ------------ | ------- | ------------------------------------------------------------------------------------------------------ |
| Cada IdP tiene quirks: implementación frágil  | Media        | Medio   | Usar authlib (Python) y python3-saml en lugar de implementar desde cero. Plantillas testeadas por IdP. |
| SCIM mal implementado deja usuarios huérfanos | Media        | Alto    | Reconciliación periódica + tests con suite SCIM oficial.                                               |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — OIDC Genérico y Plantillas

#### `task_08_01` — Integración OIDC con authlib

- [x] **Título**: Integración OIDC con authlib
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_08_01_a
    description: "Integración OIDC con authlib"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_oidc_generic.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_02` — Plantillas por IdP: Azure AD, Google Workspace, Okta, Auth0, GitHub, GitLab, Apple, Facebook

- [x] **Título**: Plantillas por IdP: Azure AD, Google Workspace, Okta, Auth0, GitHub, GitLab, Apple, Facebook
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_08_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_02_a
    description: "Plantillas por IdP: Azure AD, Google Workspace, Okta, Auth0, GitHub, GitLab, Apple, Facebook"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_oidc_templates.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_03` — UI configuración OIDC por tenant

- [x] **Título**: UI configuración OIDC por tenant
<!-- e2e (e2e/sso-oidc-config.spec.ts) escrito pero NO ejecutado: PENDING HUMAN VERIFICATION.
     Verde: typecheck + lint + build del admin-panel, CRUD backend (15 tests integración OIDC config). -->

- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_08_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_03_a
    description: "UI configuración OIDC por tenant"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/sso-oidc-config.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase B — SAML 2.0

#### `task_08_04` — Integración SAML con python3-saml (SP-initiated y IdP-initiated)

- [x] **Título**: Integración SAML con python3-saml (SP-initiated y IdP-initiated)
<!-- python3-saml + xmlsec 1.3.17 instalado OK en este host (wheel Windows);
     flujo SAML COMPLETO implementado y verde, incluida la validación de firma
     XML del assertion (no bloqueado-por-xmlsec en este entorno). El import de
     python3-saml es perezoso dentro del flujo: en un nodo SIN el backend nativo
     xmlsec los endpoints devuelven 501 (guard testeado) y el resto de auth
     (login local + OIDC) sigue funcionando. 13 tests en
     tests/integration/test_saml.py (SP-initiated, IdP-initiated/unsolicited,
     JIT, tampered/garbage assertion, config ausente/deshabilitada, aislamiento
     cross-tenant, import-guard). Migración 0033 reversible (up/down/up) con
     CHECK por-provider. OIDC + login local intactos (24 tests verdes). -->

- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_08_04_a
    description: "Integración SAML con python3-saml (SP-initiated y IdP-initiated)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_saml.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_05` — Configuración de firma y cifrado XML

- [ ] **Título**: Configuración de firma y cifrado XML
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_08_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_05_a
    description: "Configuración de firma y cifrado XML"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_saml_crypto.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_06` — UI de configuración SAML por tenant con upload de metadata IdP

- [ ] **Título**: UI de configuración SAML por tenant con upload de metadata IdP
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_08_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_06_a
    description: "UI de configuración SAML por tenant con upload de metadata IdP"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/sso-saml-config.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Provisioning y MFA

#### `task_08_07` — JIT provisioning al primer login SSO

- [ ] **Título**: JIT provisioning al primer login SSO
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_08_07_a
    description: "JIT provisioning al primer login SSO"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_jit_provisioning.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_08` — SCIM 2.0 endpoints para creación/actualización/eliminación de usuarios desde IdP

- [ ] **Título**: SCIM 2.0 endpoints para creación/actualización/eliminación de usuarios desde IdP
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_08_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_08_a
    description: "SCIM 2.0 endpoints para creación/actualización/eliminación de usuarios desde IdP"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_scim.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_09` — MFA TOTP con pyotp (setup, QR, verificación)

- [ ] **Título**: MFA TOTP con pyotp (setup, QR, verificación)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_08_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_09_a
    description: "MFA TOTP con pyotp (setup, QR, verificación)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mfa_totp.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_10` — MFA WebAuthn con py_webauthn (registro, autenticación)

- [ ] **Título**: MFA WebAuthn con py_webauthn (registro, autenticación)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_08_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_10_a
    description: "MFA WebAuthn con py_webauthn (registro, autenticación)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mfa_webauthn.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_11` — Mapeo grupos IdP → roles tenant

- [ ] **Título**: Mapeo grupos IdP → roles tenant
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_08_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_11_a
    description: "Mapeo grupos IdP → roles tenant"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_group_mapping.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_12` — Login discovery: email → tenant

- [ ] **Título**: Login discovery: email → tenant
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_08_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_12_a
    description: "Login discovery: email → tenant"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_login_discovery.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_08_13` — Documentación, ADRs, changelog

- [ ] **Título**: Documentación, ADRs, changelog
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_08_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_08_13_a
    description: "Documentación, ADRs, changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/08-sso-empresarial.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_08_01
  description: "Login OIDC con un IdP real"
  hint: "Configurar Azure AD y hacer login"
  checklist:
    - "El flujo OIDC redirige y vuelve correctamente"
    - "El usuario se crea en JIT si es su primer login"
    - "Si el IdP envía grupos, se mapean a roles del tenant"
    - "Tras logout local, no queda sesión local viva"

- id: human_08_02
  description: "MFA TOTP funciona"
  hint: "Activar TOTP en una cuenta"
  checklist:
    - "El QR se escanea correctamente con Authenticator"
    - "El código de 6 dígitos verifica"
    - "Sin TOTP el login no pasa tras la contraseña"
    - "Códigos de recovery funcionan en caso de pérdida del dispositivo"

- id: human_08_03
  description: "SCIM provisiona y deprovisiona"
  hint: "IdP crea, actualiza y deshabilita usuarios via SCIM"
  checklist:
    - "Los usuarios aparecen en el sistema al instante de la creación"
    - "Las actualizaciones de atributos del IdP se reflejan"
    - "Cuando el IdP marca usuario como suspended, se le revoca acceso inmediatamente"
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 09** (`09-marketplace.md`).
