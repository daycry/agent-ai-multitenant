# Plan 09 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 09 (Marketplace de
Skills y Tools). Validan el ciclo de seguridad del marketplace que los
tests automáticos solo cubren con Docker/firmas mockeados: la
**instalación con consentimiento granular**, el **bloqueo por análisis
estático**, **Playwright end-to-end** (tool destacada + agente QA E2E
Automator), y el **compartir cross-tenant auditado**.

> **Estado del plan**: `pending_human_validation`. Las 19 tareas y sus
> tests automáticos están en verde (modelos + RLS, niveles de confianza,
> análisis estático Bandit/semgrep, sandbox de ejecución, consentimiento
> por permiso, revocación + audit, formatos SKILL.md / tool YAML, install
> orchestrator con firma Ed25519, versionado semver, Playwright + QA E2E
> Automator, marketplace privado + cross-tenant, UI del Tenant Admin).
> Varios e2e (`permission-consent`, `playwright-*`, `private-marketplace`,
> `marketplace-admin`) y el contenedor real del sandbox quedan pendientes
> de navegador/imagen runtime — estos 4 tests humanos son el último paso
> antes de pasar a `completed`.

## TL;DR

No hay `setup_demo_09.py` ni launcher dedicado para este plan. El setup
es manual. El catálogo de arranque (Playwright + skills oficiales) lo
**siembra el Plan 09.1**; ejecuta el seed para no encontrarte el
marketplace vacío:

```powershell
.\scripts\dev\up.ps1                          # api-server :8001 + admin-panel :3000 + postgres + redis
.\.venv\Scripts\python.exe -m api_server.seeds # built-ins + listings oficiales (idempotente)
```

La gestión del marketplace se hace desde el admin-panel (Tenant Admin):

```
http://localhost:3000/admin/marketplace                                  # catálogo + instaladas + compartir
http://localhost:3000/admin/marketplace/private                          # publicar skills/tools privadas
http://localhost:3000/admin/marketplace/installations/{id}/permissions   # consentimiento granular por permiso
http://localhost:3000/admin/marketplace/listings/{id}/playwright-config  # config guiada de Playwright
```

## Pre-requisitos

| Requisito                                         | Por qué                                                                    |
| ------------------------------------------------- | -------------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                       | api-server + admin-panel + postgres + redis                                |
| Seeds aplicados (`python -m api_server.seeds`)    | El catálogo oficial (Playwright + skills) nace de aquí (Plan 09.1)         |
| Un usuario `tenant_admin`                         | Instalar, consentir, revocar y compartir son operaciones de Tenant Admin   |
| Un listing community/experimental con permisos    | `human_09_01` necesita una tool que pida `allowed_domains` etc.            |
| Un artefacto con `eval()`/`subprocess shell=True` | `human_09_02` necesita código sospechoso que el análisis estático rechace  |
| Imagen runtime de Playwright disponible           | `human_09_03` ejecuta Playwright de verdad (browsers, screenshots, traces) |
| Dos tenants (A y B)                               | `human_09_04` valida el compartir cross-tenant con badge + audit           |

---

## `human_09_01` — Instalación con consentimiento granular

**Qué prueba**: al instalar una tool community que pide permisos (p.ej.
`allowed_domains: [api.x.com]`), la UI muestra el permiso de forma
legible, el project_owner aprueba/rechaza **por permiso individual**,
rechazar cancela la instalación, y el audit_log refleja quién aprobó
qué.

**Precondiciones**:

- Un listing de nivel **community** (o experimental) que declara permisos
  en su manifest (`allowed_domains`, `allowed_paths`, `network_policy`).
- Login como `tenant_admin` (rol project_owner de facto en este repo).

**Pasos**:

1. En `/admin/marketplace`, instala la tool community. La instalación
   nace **DISABLED** (consent-gated).
2. Abre `/admin/marketplace/installations/{id}/permissions`: la UI lista
   cada permiso solicitado de forma **legible** (p.ej. "Acceso de red a
   `api.x.com`").
3. **Aprueba** cada permiso requerido uno por uno → cuando todos están
   concedidos, la instalación pasa a **ENABLED**.
4. Repite con otra instalación community y **rechaza** uno de los
   permisos → la instalación **no** se habilita (sigue disabled) y se
   registra `consent_denied`.
5. Inspecciona el audit_log de la instalación: debe reflejar **quién
   aprobó/rechazó qué permiso**.

**Resultado esperado**: permisos legibles, aprobación/rechazo por
permiso individual, rechazo cancela la habilitación, audit_log con la
traza de consentimiento.

**Checklist**:

- [ ] La UI muestra el permiso solicitado de forma legible.
- [ ] El project_owner aprueba/rechaza por permiso individual.
- [ ] Si se rechaza un permiso, la instalación se cancela (no se
      habilita).
- [ ] Tras instalar, el audit_log refleja quién aprobó qué permiso.

**Pitfalls conocidos**:

- Los listings **verified** instalan ENABLED directamente (sin gate de
  consentimiento) — usa un listing **community/experimental** para este
  test.
- Si no ves la pantalla de permisos, comprueba que el listing realmente
  declara permisos en su manifest (un manifest sin `permissions` no
  dispara el consent gate).

---

## `human_09_02` — El análisis estático bloquea código sospechoso

**Qué prueba**: intentar instalar una tool cuyo código contiene patrones
peligrosos (`eval()`, `subprocess(..., shell=True)`) es detectado por
Bandit/semgrep, la instalación se bloquea con mensaje claro y el intento
queda en el audit_log.

**Precondiciones**:

- Un artefacto de tool/skill cuyo código incluya un patrón sospechoso
  (p.ej. una llamada a `eval()` o `subprocess.run(..., shell=True)`).

**Pasos**:

1. Intenta instalar ese artefacto desde el marketplace.
2. El pipeline de instalación corre el **análisis estático** (Bandit para
   Python, semgrep para patrones genéricos) como una de sus puertas
   (fail-closed).
3. Observa el resultado: la instalación debe **bloquearse** con un
   mensaje claro que indica el patrón detectado.
4. Inspecciona el audit_log: debe reflejar el **intento bloqueado**.

**Resultado esperado**: Bandit/semgrep detecta el patrón, la instalación
se bloquea con mensaje claro, el audit_log registra el intento.

**Checklist**:

- [ ] Bandit/semgrep detecta el patrón.
- [ ] La instalación se bloquea con mensaje claro.
- [ ] El audit log refleja el intento.

**Pitfalls conocidos**:

- El bloqueo depende del **umbral de severidad** del nivel de confianza
  (`max_allowed_severity`): un patrón por debajo del umbral de ese nivel
  puede no abortar. Usa un patrón claramente peligroso (HIGH) para una
  prueba inequívoca.
- semgrep es opcional/lazy en algunos entornos; si no está instalado,
  Bandit cubre el camino Python. Comprueba en los logs qué analizador
  corrió.

---

## `human_09_03` — Playwright funciona end-to-end

**Qué prueba**: instalar Playwright desde el marketplace con su
configuración guiada deja los browsers descargados, y el agente plantilla
QA E2E Automator usa la tool produciendo screenshots y traces que quedan
persistidos como outputs de la tarea.

**Precondiciones**:

- El listing **Playwright** sembrado (catálogo oficial, Plan 09.1).
- La imagen runtime que ejecuta Playwright disponible.
- Un proyecto donde crear una tarea para el agente QA E2E Automator.

**Pasos**:

1. Crea un proyecto e instala **Playwright** desde `/admin/marketplace`.
2. Abre la configuración guiada
   `/admin/marketplace/listings/{id}/playwright-config`: elige browsers
   (chromium/firefox/webkit), headless, modo de screenshots y traces.
   Guarda → los **browsers quedan descargados**.
3. Asigna el agente plantilla **QA E2E Automator** a una tarea del
   proyecto que ejecute un spec Playwright (p.ej. un flujo de login).
4. Lanza la tarea: el agente usa la tool Playwright y **produce
   screenshots y traces**.
5. Comprueba que esos artefactos quedan **persistidos como outputs de la
   tarea**.

**Resultado esperado**: configuración guiada deja browsers descargados;
el agente QA E2E Automator usa Playwright y genera screenshots/traces;
los artefactos quedan persistidos como outputs de la tarea.

**Checklist**:

- [ ] Configuración guiada deja browsers descargados.
- [ ] Agente QA E2E Automator usa la tool y produce screenshots y traces.
- [ ] Los artefactos quedan persistidos como outputs de la tarea.

**Pitfalls conocidos**:

- Playwright se ejecuta en el runtime `node-playwright`: si la tarea no
  arranca, comprueba que la imagen runtime con el navegador está
  disponible (no se ejecuta en el worker, sino en un contenedor
  efímero).
- La tool Playwright es un **listing global VERIFIED** (`tenant_id NULL`,
  modelo híbrido) — todos los tenants la ven. Si no aparece, re-corre el
  seed (`python -m api_server.seeds`).
- Las `allowed_domains` de la config limitan los sitios bajo prueba; si
  el spec navega a un dominio no permitido, fallará por la network policy
  `restricted`.

---

## `human_09_04` — Compartir entre tenants requiere audit

**Qué prueba**: Tenant A comparte una skill custom con Tenant B solo con
opt-in explícito; con el opt-in, el System Admin ve el evento en el
audit_log y Tenant B ve la skill etiquetada como "compartida por Tenant
A".

**Precondiciones**:

- Dos tenants: A (owner de una skill privada) y B (target).
- Tenant A tiene una **skill privada** publicada
  (`/admin/marketplace/private`).
- Un usuario `system_admin` para auditar.

**Pasos**:

1. Como `tenant_admin` de A, intenta que B vea la skill **sin** compartir
   explícitamente → B **no** la ve (default = nada compartido).
2. Crea un share explícito de A → B desde
   `/admin/marketplace` (pestaña Compartir): el picker solo ofrece tus
   **listings privados** (un listing global ya es visible para todos,
   nada que compartir). Selecciona el listing + el tenant target B.
3. Como `system_admin`, abre la enumeración de shares
   (`/admin/marketplace/shares` admin) o el audit_log: el **evento de
   compartir** debe aparecer (`cross_tenant_share`).
4. Como usuario de Tenant B, abre `/admin/marketplace`: la skill aparece
   como **"compartida por Tenant A"** con su badge y es instalable.
5. (Opcional) Revoca el share desde A → la skill **desaparece** de
   inmediato de la vista de B y se audita `cross_tenant_share_revoke`.

**Resultado esperado**: sin opt-in explícito no hay visibilidad; con
opt-in el System Admin ve el evento en audit_log y Tenant B ve la skill
con badge "compartida por Tenant A".

**Checklist**:

- [ ] Sin opt-in explícito de ambos, no se puede compartir.
- [ ] Con opt-in, el System Admin ve el evento en audit_log.
- [ ] Tenant B ve la skill como "compartida por Tenant A" con badge.

**Pitfalls conocidos**:

- Compartir es un **grant explícito y auditado**, nunca un bypass
  implícito de RLS: B ve el listing solo mientras el share esté **vivo**
  (sin `revoked_at` ni `deleted_at`). Revocar quita la visibilidad al
  instante.
- Solo se pueden compartir listings **privados propios** del owner; no se
  puede compartir un listing global (ya visible) ni el privado de otro
  tenant (404).
- La enumeración de **todos** los shares es operación de `system_admin`
  (sesión BYPASSRLS); un `tenant_admin` solo ve los grants de su propio
  tenant.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/09-marketplace.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/09-marketplace.md`](../../07-changelog/) y la
   referencia [`docs/04-reference/marketplace.md`](../../04-reference/).
3. Verifica que el PR `plan/09-marketplace` está mergeado a `master`.

## Troubleshooting

| Síntoma                                       | Causa probable                                                        | Fix                                                                       |
| --------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `/admin/marketplace` aparece vacío            | El catálogo oficial no está sembrado                                  | `.\.venv\Scripts\python.exe -m api_server.seeds` (Plan 09.1, idempotente) |
| No sale la pantalla de consentimiento         | El listing no declara permisos, o es verified (instala ENABLED)       | Usa un listing community/experimental con `permissions` en su manifest    |
| El análisis estático no bloquea               | Patrón por debajo del `max_allowed_severity` del nivel, o semgrep off | Usa un patrón HIGH; revisa en logs qué analizador corrió (Bandit/semgrep) |
| Playwright no aparece en el catálogo          | El listing global no fue sembrado                                     | Re-corre `python -m api_server.seeds`                                     |
| Tenant B ve la skill sin que A la comparta    | (No debería) revisa que no haya un share vivo previo                  | Lista los shares como system_admin; revoca el grant residual              |
| Revocar un share no quita la visibilidad en B | Cache de TanStack en la UI de B                                       | F5 en `/admin/marketplace` de B; el grant revocado deja de ser visible    |

Errores transversales viven en `docs/03-guides/gotchas/`.
