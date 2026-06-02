# Plan 00 — tests humanos

Esta guía cubre los **5 tests humanos** del Plan 00 (Fundaciones del
Sistema). Validan que los cimientos funcionan en una máquina nueva:
arranque con un solo comando, multi-tenancy real, self-healing del
watchdog, observabilidad útil y documentación inicial navegable.

> **Estado del plan**: `completed` (mergeado a `master`,
> `completed_at: 2026-05-21`). Esta guía es el **registro histórico**
> de los tests humanos con los que se cerró el plan; queda para
> regresión cuando se toquen el docker-compose base, el middleware
> multi-tenant, el watchdog o el logging.

## TL;DR

El Plan 00 **no tiene** `scripts/setup_demo_00.py` ni launcher
dedicado — los tests son de infraestructura (arranque limpio, kill de
servicios, inspección de logs). Setup manual:

```powershell
.\scripts\dev\up.ps1                 # postgres + redis + minio + vault + clamav + api-server + admin-panel
# luego: abre http://localhost:3000/login  (pantalla de login del System Admin)
```

> Para `human_00_01` el escenario fiel es **máquina nueva**: clonar el
> repo en una máquina limpia con Docker 24+ y Compose v2+, copiar el
> `.env`, y correr `docker compose -f docker/docker-compose.yml up -d`.
> En tu máquina de dev, `up.ps1` levanta el mismo stack y sirve para
> el resto de tests.

## Pre-requisitos

| Requisito                         | Por qué                                                        |
| --------------------------------- | -------------------------------------------------------------- |
| Docker 24+ y Compose v2+          | El stack es Docker Compose en una sola máquina                 |
| Stack dev arriba (`up.ps1`)       | postgres + redis + minio + vault + clamav + api-server + panel |
| `curl` o Postman                  | Para los tests de aislamiento cross-tenant (tokens cruzados)   |
| Acceso a los logs (`docker logs`) | Para inspeccionar logs JSON, trace_id y enmascarado de PII     |

---

## `human_00_01` — el sistema arranca con un solo comando en una máquina nueva

**Qué prueba**: en una máquina limpia, `docker compose up -d` deja
todos los servicios `healthy` en menos de 2 minutos y el panel admin
sirve la pantalla de login sin errores.

**Precondiciones**:

- Máquina con Docker 24+ y Compose v2+, repo clonado, `.env` presente.
- Ningún volumen previo del stack (arranque verdaderamente fresco).

**Pasos**:

1. En la máquina nueva, desde la raíz del repo, ejecuta
   `docker compose -f docker/docker-compose.yml up -d`. En dev:
   `.\scripts\dev\up.ps1`.
2. Espera ~2 minutos. Comprueba el estado:
   `docker compose -f docker/docker-compose.yml ps`.
3. Abre `http://localhost:3000/login` (o el puerto configurado) en el
   navegador.
4. Revisa los logs de arranque:
   `docker compose -f docker/docker-compose.yml logs --since 3m`.

**Resultado esperado**:

- `up -d` termina sin errores.
- Todos los servicios figuran `healthy` (no `starting` ni `unhealthy`)
  en menos de 2 minutos.
- El panel responde con la **pantalla de login** del System Admin.
- Los logs **no** muestran tracebacks ni errores rojos en el arranque.

**Checklist**:

- [ ] `docker compose up -d` termina sin errores en máquina nueva.
- [ ] Todos los servicios pasan healthcheck en menos de 2 minutos.
- [ ] El panel admin responde con la pantalla de login.
- [ ] Los logs no muestran tracebacks ni errores rojos en el arranque.

**Pitfalls conocidos**:

- Si un servicio queda en `starting` indefinidamente, mira su
  healthcheck con `docker inspect <container> --format '{{json .State.Health}}'`.
- Vault arranca `sealed` si no se inicializó: en dev `up.ps1` lo deja
  unsealed; en producción corre `scripts/init-vault.ps1` primero.

---

## `human_00_02` — multi-tenancy es real, no decorativa

**Qué prueba**: cruzar tokens entre dos tenants devuelve 403/404, y
los logs muestran que el filtro de `tenant_id` se aplica en todas las
queries (RLS + middleware).

**Precondiciones**:

- Stack arriba. Sesión de System Admin (el primer registro se promueve
  a `system_admin` automáticamente).

**Pasos**:

1. Crea el **tenant A** con su Tenant Admin (desde `/admin/tenants` o
   `POST /admin/tenants`).
2. Crea el **tenant B** con un Tenant Admin **distinto**.
3. Loguéate como `admin_A` y captura su JWT.
4. Con el token de `admin_A`, intenta listar usuarios de tenant B:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" \
     http://localhost:8001/admin/users?tenant_id=<B> \
     -H "Authorization: Bearer $TOKEN_A"
   # → 403 (o 404)
   ```
5. Con el token de `admin_A`, haz `GET /admin/users/{user_id_de_B}`.
6. Revisa los logs de la API durante esas requests:
   `docker compose logs api-server --since 2m`.

**Resultado esperado**:

- Listar usuarios de tenant B con el token de A → **403/404**.
- `GET` del user de B con el token de A → **404**.
- Los logs muestran el filtro de `tenant_id` aplicándose en las
  queries (RLS `SET LOCAL app.tenant_id`).

**Checklist**:

- [ ] Tenant A creado con su Tenant Admin.
- [ ] Tenant B creado con un Tenant Admin distinto.
- [ ] Con el token de admin_A, listar usuarios de tenant_B → 403/404.
- [ ] Con el token de admin_A, `GET /admin/users/{user_id_de_B}` → 404.
- [ ] Los logs muestran el filtro de tenant_id en todas las queries.

**Pitfalls conocidos**:

- Si recibes 200 con datos del otro tenant, falla el RLS o el
  middleware — es un bloqueante crítico, no un falso negativo.
- Un `system_admin` SÍ ve cross-tenant por diseño: para este test usa
  los Tenant Admin (no el superadmin).

---

## `human_00_03` — self-healing funciona en escenario realista

**Qué prueba**: matar manualmente un servicio crítico provoca su
reinicio por el watchdog en menos de 60 s, y tras 5 fallos seguidos el
watchdog alerta y deja de reintentar.

**Precondiciones**: stack arriba, watchdog corriendo.

**Pasos**:

1. Mata el api-server:
   `docker compose -f docker/docker-compose.yml kill api-server`.
   Cronometra hasta que vuelve a `healthy`.
2. Mata postgres: `docker compose kill postgres`. Observa que el
   watchdog lo reinicia y que los demás servicios se reconectan sin
   intervención manual.
3. Fuerza 5 fallos consecutivos del mismo servicio (mata, deja que
   reinicie, vuelve a matar, etc.) y observa los logs del watchdog.

**Resultado esperado**:

- `kill api-server` → reiniciado en **< 60 s**.
- `kill postgres` → reiniciado; los demás servicios se reconectan
  solos.
- Tras 5 fallos consecutivos del mismo servicio, el watchdog **alerta**
  (log estructurado en stderr) y **deja de reintentar**.

**Checklist**:

- [ ] `docker compose kill api-server` → watchdog reinicia en < 60 s.
- [ ] `docker compose kill postgres` → watchdog reinicia y los demás
      se reconectan sin intervención humana.
- [ ] Tras 5 fallos consecutivos del mismo servicio, el watchdog
      alerta y deja de reintentar.

**Pitfalls conocidos**:

- En esta fase la "alerta" es solo un log estructurado en stderr (las
  notificaciones reales llegan en Fase 10) — busca el evento en
  `docker compose logs watchdog`, no esperes un email.
- El backoff es 10s, 30s, 90s… máx 5 intentos: no esperes reinicio
  inmediato tras varios fallos seguidos.

---

## `human_00_04` — observabilidad es útil, no solo verbosa

**Qué prueba**: los logs JSON llevan los campos estándar, un `trace_id`
recorre todos los servicios de una request, y la PII (email, tokens)
sale enmascarada.

**Precondiciones**: stack arriba.

**Pasos**:

1. Haz un par de operaciones contra la API (login con un email, una o
   dos requests autenticadas).
2. Inspecciona los logs: `docker compose logs api-server --since 2m`.
   Verifica los campos de cada línea JSON.
3. Toma un `trace_id` de una request y haz grep de ese mismo id en los
   logs de los demás servicios que participaron.
4. Verifica que el email del login aparece enmascarado (p. ej.
   `a***@example.com`) y que un token (si se loguea por error) sale
   redactado.

**Resultado esperado**:

- Cada log JSON tiene `timestamp`, `level`, `service`, `trace_id`,
  `span_id`, `tenant_id`, `user_id`, `project_id` (cuando aplica).
- El mismo `trace_id` aparece en los logs de todos los servicios
  involucrados.
- El email del login aparece **enmascarado**; los tokens, redactados.

**Checklist**:

- [ ] Los logs JSON tienen los campos estándar (timestamp, service,
      trace_id, tenant_id, etc.).
- [ ] Un trace_id de una request HTTP se ve en los logs de todos los
      servicios que participaron.
- [ ] Un email de login aparece enmascarado (p. ej. `a***@example.com`).
- [ ] Un token logueado por error sale enmascarado.

**Pitfalls conocidos**:

- Si los logs salen como texto plano y no JSON, revisa que `structlog`
  esté configurado en ese servicio (no todos los logs de librerías de
  terceros respetan el formato).
- El enmascarado de PII solo cubre los patrones declarados (email,
  tokens, IBAN, DNI): otros campos sensibles no se redactan
  automáticamente.

---

## `human_00_05` — documentación inicial es navegable y suficiente para arrancar

**Qué prueba**: un desarrollador nuevo puede seguir las guías para
arrancar el sistema; los 5 ADRs iniciales están bien justificados y el
Mermaid renderiza.

**Precondiciones**: ninguna (es revisión de docs).

**Pasos**:

1. Lee `docs/01-overview/01-introduction.md` y cronometra: ¿queda claro
   qué es el sistema en menos de 5 minutos?
2. Sigue `docs/02-getting-started/01-installation.md` paso a paso en
   una máquina y verifica que las instrucciones son reproducibles.
3. Lee los 5 ADRs iniciales (`docs/05-architecture-decisions/0001`..`0005`):
   PostgreSQL+RLS, sesiones server-side en Redis, Vault día uno,
   monorepo, Argon2id.
4. Abre los .md con diagramas en GitHub/GitLab y comprueba que el
   Mermaid renderiza.

**Resultado esperado**:

- La introducción explica el sistema en < 5 min de lectura.
- La guía de instalación es reproducible (sin pasos implícitos).
- Los 5 ADRs tienen contexto + decisión + alternativas descartadas.
- Mermaid renderiza correctamente.

**Checklist**:

- [ ] `01-introduction.md` explica el sistema en menos de 5 minutos.
- [ ] `01-installation.md` tiene instrucciones reproducibles.
- [ ] Los 5 ADRs iniciales están bien justificados (contexto,
      decisión, alternativas).
- [ ] Mermaid renderiza correctamente en GitHub/GitLab.

**Pitfalls conocidos**:

- Si un diagrama Mermaid no renderiza, suele ser por una valla de
  código mal cerrada o sintaxis no soportada por el viewer.

---

## Cierre del plan

El plan ya está `completed` (`2026-05-21`). Esta guía es el registro
histórico. Para regresión tras tocar fundaciones, re-corre los 5
escenarios.

## Troubleshooting

Los errores transversales del stack dev (JWT secret mismatch, asyncpg,
Vault sealed, healthchecks que no pasan, problemas Docker en Windows)
viven en `docs/03-guides/gotchas/`.
