---
adr: "0032"
title: Marketplace — niveles de confianza, catálogo híbrido global/privado y pipeline de instalación gated
status: accepted
date: 2026-05-30
deciders: System Architect, Security
phase: 09-marketplace
---

# ADR 0032 — Marketplace: niveles de confianza, catálogo híbrido y pipeline de instalación gated

> **Estado: `accepted`.** Recoge tres decisiones arquitectónicas tomadas
> durante el Plan 09 que no estaban registradas en un ADR previo: cómo el
> **nivel de confianza** de un listing determina los guardrails (no la
> disponibilidad); el modelo de **catálogo híbrido global/privado** y cómo
> se comparte un recurso entre tenants sin romper RLS; y el **pipeline de
> instalación gated** (análisis estático + sandbox + consentimiento por
> permiso) por el que pasa todo install.

## Contexto

El Plan 09 hace descubribles, instalables y compartibles las skills, tools y
MCP servers que tras la Fase 5 solo se añadían a mano. Tres cuestiones de
diseño no quedaban cerradas por ADRs previos:

1. **¿Qué significa el "nivel de confianza" de un listing?** Un marketplace
   con catálogo curado y aportes de terceros necesita distinguir lo
   revisado de lo no vetado. La pregunta es si el nivel debe **restringir la
   disponibilidad** (solo puedes instalar lo verificado) o **modular la
   seguridad** (puedes instalar cualquier cosa, pero con más o menos
   puertas).

2. **¿Cómo conviven el catálogo público de plataforma y los recursos
   internos de un tenant, y cómo se comparte entre tenants?** El sistema es
   multi-tenant con RLS desde el día uno (ADR 0001). Un marketplace introduce
   tanto un catálogo global (visible a todos) como recursos privados de cada
   tenant, y el requisito de compartir un recurso privado con otro tenant.
   Compartir cruza deliberadamente la frontera de tenant, así que había que
   decidir cómo hacerlo **sin** que se convierta en un bypass implícito de
   RLS.

3. **¿Qué garantías aplica una instalación antes de confiar en código de
   terceros?** Una tool del marketplace ejecuta código que la plataforma no
   escribió. Había que definir el orden y la obligatoriedad de las
   comprobaciones previas (firma, análisis estático, sandbox) y del
   consentimiento del project owner sobre los permisos solicitados.

## Decisión

### 1. El nivel de confianza gobierna los guardrails, NO la disponibilidad

Los tres niveles `verified` / `community` / `experimental` **no limitan qué
se puede instalar** — todo listing se puede navegar e instalar. El nivel solo
decide **cuánta fricción y cuántas puertas** impone el flujo de instalación.
Cada nivel resuelve, vía `marketplace/trust.py`, a una `TrustPolicy` inmutable
(`frozen` + `slots`) con cinco perillas:

- `signature_required` — la firma desprendida del listing debe verificar
  contra la clave del equipo de plataforma. Solo `verified` va firmado.
- `per_permission_consent_required` — el project owner aprueba CADA permiso
  solicitado uno a uno. `community` y `experimental` SIEMPRE lo exigen;
  `verified` no (fricción mínima).
- `static_analysis_required` — corre el scan Bandit/semgrep previo.
- `sandbox_required` — corre el probe en el contenedor efímero endurecido.
- `max_allowed_severity` — la finding más alta tolerada (`verified` tolera
  hasta MEDIUM; `community` hasta LOW; `experimental` NONE: cualquier finding
  bloquea).

`trust.py` es la **fuente única de verdad**: el resto del plan (análisis
estático, sandbox, consentimiento, install) lee un resolver en vez de
esparcir `if trust_level == ...` por el código.

### 2. Catálogo híbrido global/privado; compartir = grant explícito y auditado

`marketplace_listings.tenant_id` es **NULLABLE** (modelo híbrido):

- **NULL** → listing **global** del catálogo público, visible a todo tenant
  (política RLS `FOR SELECT` `marketplace_listings_global_read`). Las
  escrituras de filas globales quedan reservadas a roles `BYPASSRLS` (System
  Admin / publisher del catálogo); una sesión de tenant no puede publicar una
  fila global (la `WITH CHECK` la rechaza).
- **no-NULL** → listing **privado** del tenant, aislado por la política
  `FOR ALL` `marketplace_listings_tenant_isolation`. Un tenant NUNCA ve los
  privados de otro.

**Compartir un recurso entre tenants es opt-in, explícito y auditado — nunca
un bypass implícito de RLS.** Un tenant OWNER comparte uno de sus listings
PRIVADOS con un único tenant TARGET creando una fila `marketplace_shares`
(migración 0044). La visibilidad se concede mediante una política RLS
**aditiva** `FOR SELECT` `marketplace_listings_shared_read` que expone el
listing al tenant actual SOLO si existe un share **vivo**
(`deleted_at IS NULL AND revoked_at IS NULL`) que se lo concede. El target
ve/instala el listing solo a través del grant; revocar elimina la visibilidad
de inmediato; el target nunca obtiene ruta de escritura. La tabla
`marketplace_shares` tiene RLS dual-scope (el OWNER gestiona sus grants; el
TARGET solo los LEE). El System Admin (sesión BYPASSRLS) ve TODOS los shares
para audit (`GET /admin/marketplace/shares`). Default = nada compartido.

### 3. Instalación gated, fail-closed, con auditoría append-only

Todo install pasa por `InstallOrchestrator` (`marketplace/install.py`), que
encadena las puertas que la `TrustPolicy` implica en orden fijo y
**fail-closed**:

1. **FETCH** del artefacto (tras un `ArtifactFetcher` Protocol).
2. **PARSE** del SKILL.md / tool manifest (antes de escanear o ejecutar
   nada).
3. **VERIFY SIGNATURE** Ed25519 (`cryptography`) cuando
   `signature_required` — un artefacto manipulado o sin firmar es RECHAZADO;
   la firma nunca se devuelve al caller.
4. **STATIC ANALYSIS** — BLOQUEA cuando una finding supera
   `max_allowed_severity`.
5. **SANDBOX SMOKE TEST** en el contenedor endurecido cuando
   `sandbox_required`; un launch error falla cerrado.
6. **CONSENT** por permiso cuando `per_permission_consent_required`: el
   install se PERSISTE pero nace `disabled` y solo pasa a `enabled` cuando
   TODOS los permisos están concedidos vía `POST .../consent`.
7. **PERSIST** install + audit entry.

Un _fallo de puerta_ (firma mala, análisis bloqueante, sandbox fallido) es un
**hard abort**: `InstallError` tipado + una fila de audit que registra el
porqué (COMMITeada antes de propagar el error, para que el registro inmutable
sobreviva) + ningún install habilitado. "Esperando consentimiento" NO es un
fallo: el install se crea `disabled`. La auditoría del marketplace es
**append-only a nivel de base de datos** (migración 0043: políticas
`FOR SELECT` + `FOR INSERT`, sin UPDATE ni DELETE para el rol de la app).

## Alternativas consideradas

- **El nivel de confianza restringe la disponibilidad** (solo instalar lo
  verificado). Descartada: ahogaría el caso de uso departamental (skills
  internas, experimentación) y empujaría a la gente fuera del marketplace; la
  seguridad se obtiene mejor con guardrails proporcionales que con una lista
  blanca rígida.
- **Una tabla separada de listings globales vs. privados.** Descartada: un
  `tenant_id` nullable + RLS reutiliza el patrón híbrido ya probado para
  skills/tools/agents builtin (migración 0004), sin duplicar la superficie de
  consulta ni el código de browse.
- **Compartir copiando el listing al tenant destino** o **relajando RLS para
  el recurso compartido.** Descartadas: una copia diverge y oculta la
  procedencia; relajar RLS sería exactamente el bypass implícito que el plan
  prohíbe. El grant explícito + política aditiva mantiene la fila bajo su
  owner, hace la visibilidad revocable al instante y deja toda la actividad
  auditada para el System Admin.
- **Confiar en el análisis estático y saltarse el sandbox / el
  consentimiento para community.** Descartada: el análisis estático tiene
  falsos negativos; el sandbox (defensa en profundidad) y el consentimiento
  por permiso (el project owner decide qué red/paths concede) son la garantía
  de que código no vetado no actúa fuera de lo aprobado.

## Consecuencias

### Positivas

- Un único resolver de política (`trust.py`) decide todos los guardrails; no
  hay literales de nivel dispersos.
- El catálogo híbrido reutiliza el patrón RLS existente; los privados de un
  tenant quedan aislados y compartir es revocable, explícito y auditado.
- El pipeline fail-closed deja un rastro inmutable de cada install/abort; las
  firmas y secretos nunca salen del servidor.

### Negativas / cuidados

- El camino real del sandbox solo se ejerce en nodos con la imagen runtime +
  Docker; los tests mockean el cliente y la ejecución real queda pendiente de
  la imagen (igual que el camino cripto de SAML en ADR 0031).
- `semgrep` y `docker` son dependencias **opcionales/lazy** (semgrep choca con
  el stack OTel/protobuf si se pinea); donde faltan, su camino degrada limpio
  — pero conviene garantizarlas en CI/prod donde el marketplace esté en uso.
- Cablear vivo el `InstallOrchestrator` en el camino de aborto del endpoint
  install/update queda como follow-up del runtime de catálogo (artefactos en
  disco por listing).

## Referencias

- `apps/api-server/src/api_server/marketplace/trust.py` — `TrustPolicy`,
  `trust_policy`, `PERMISSION_KEYS`, `NetworkPolicy`.
- `apps/api-server/src/api_server/marketplace/install.py` —
  `InstallOrchestrator` (las 7 puertas).
- `apps/api-server/src/api_server/marketplace/static_analysis.py`,
  `sandbox.py`, `consent.py` — las puertas individuales.
- `apps/api-server/src/api_server/routers/marketplace.py` — endpoints
  (browse / install / consent / private / shares / admin audit).
- `apps/api-server/migrations/versions/20260530_0041_marketplace.py` (RLS
  híbrido), `…_0043_marketplace_audit_append_only.py` (append-only),
  `…_0044_marketplace_shares.py` (grant cross-tenant + política aditiva).
- ADR 0001 — PostgreSQL RLS desde el día uno (base que este ADR aplica al
  marketplace).
- ADR 0031 — precedente de dependencia nativa opcional con degradación limpia
  (xmlsec), análogo a semgrep/docker aquí.
- `docs/04-reference/marketplace.md` — referencia de endpoints.
- `docs/07-changelog/09-marketplace.md` — changelog del plan.
