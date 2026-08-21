# Primer arranque

Cómo arrancar el sistema vacío y registrar un primer System Admin.

## Vía rápida — `up.ps1` / `up.sh`

Si ya hiciste el bootstrap (Python + `npm install` en `apps/admin-panel`),
un solo comando levanta todo, deja los servicios corriendo en background
y te imprime las URLs:

```powershell
.\scripts\dev\up.ps1                       # Windows
```

```bash
./scripts/dev/up.sh                        # Linux / macOS
```

Esto hace, en orden: arranca Docker, espera a postgres `healthy`, aplica
migraciones Alembic, lanza `uvicorn` en `:8001` (detached, log en
`.dev/api-server.log`), espera `/healthz`, lanza `next dev` en `:3000`
(detached, log en `.dev/admin-panel.log`) con `NEXT_PUBLIC_API_URL`
apuntado al api correcto, y espera a que el SPA compile. Puedes cerrar
la terminal: los procesos siguen vivos (PIDs en `.dev/*.pid`).

Para parar:

```powershell
.\scripts\dev\down.ps1                     # mata api + admin
.\scripts\dev\down.ps1 -Docker             # además baja el stack Docker
```

```bash
./scripts/dev/down.sh
./scripts/dev/down.sh --docker
```

Parámetros opcionales: `-ApiPort` / `-AdminPort` (ps1) y `--api-port` /
`--admin-port` (sh) si los defaults chocan con algo. Si lanzas `up`
dos veces sin `down`, aborta con "already running (pid X)" para no
apilar huérfanos.

### URLs y credenciales por defecto

| URL                           | Servicio        | Credenciales                                   |
| ----------------------------- | --------------- | ---------------------------------------------- |
| http://localhost:3000/login   | Admin panel     | `root@example.com` / `longenoughpw` (paso 4–5) |
| http://localhost:8001/docs    | API (Swagger)   | —                                              |
| http://localhost:8001/healthz | API healthcheck | —                                              |
| http://localhost:9001         | MinIO console   | `minioadmin` / `changeme-dev-only`             |
| http://localhost:8200/ui      | Vault UI        | token: `dev-root-token`                        |
| postgres `localhost:15432`    | PostgreSQL      | `postgres` / `changeme-dev-only`               |
| redis `localhost:6379`        | Redis           | sin auth en dev                                |

Las credenciales vienen de `docker/.env.example`; puedes sobreescribirlas
con un `.env` propio en `docker/`.

---

## Paso a paso manual (si quieres entender qué hace `up.ps1`)

Lo siguiente es lo que `up.ps1` automatiza. Útil para depurar o para la
primera vez que quieras ver el flujo completo.

### 1. Verifica que el stack está sano

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  ps
```

Los cinco servicios deben aparecer como `Up X (healthy)`.

### 2. Aplica las migraciones

```bash
cd apps/api-server
DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform" \
  .venv/Scripts/python -m alembic upgrade head     # Windows
```

(Linux/macOS: `.venv/bin/python` en lugar de `.venv/Scripts/python`.)

Esto crea las cinco tablas (`organizations`, `users`,
`user_org_memberships`, `sessions`, `audit_log`) con sus índices
y activa las **policies RLS**.

### 3. Levanta el api-server

```bash
cd apps/api-server
.venv/Scripts/uvicorn api_server.main:app --reload --port 8001
```

→ http://localhost:8001/docs (OpenAPI interactivo).

### 4. Registra el primer usuario

`POST /auth/register` está **cerrado al público** (ADR 0134): solo da de alta a
quien presenta una invitación válida… **con una excepción, que es justo ésta**:
mientras la tabla `users` esté vacía el registro se permite sin invitación. Es
la puerta de arranque de una instalación nueva; sin ella, un despliegue recién
levantado quedaría inaccesible para siempre, porque no habría nadie que pudiera
emitir la primera invitación.

```bash
curl -X POST http://localhost:8001/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"root@example.com","password":"longenoughpw","full_name":"Root"}'
```

### 5. (Ya eres System Admin y System Owner)

No hace falta ningún `UPDATE` a mano: ese primer usuario sale de fábrica con
`is_system_admin` **y** `is_system_owner` (ADR 0074 / ADR 0134). El segundo
—System Owner— es el que abre el córtex, y hasta el 2026-07-31 no lo fijaba
nadie salvo este registro, de modo que las instalaciones hechas con el
instalador se quedaban sin propietario. Hoy lo fija también
`api_server.seeds.init_tenant`, que es lo que ejecuta el instalador.

Compruébalo:

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  exec postgres \
  psql -U postgres -d agentic_platform \
       -c "SELECT email, is_system_admin, is_system_owner FROM users"
```

### 5-bis. Dar de alta a cualquier OTRA persona

A partir del segundo usuario el alta es por invitación:

1. Como System Admin, entra en **Plataforma → Invitaciones**
   (`/admin/invitations`), elige email, espacio de trabajo y rol, y emite.
2. Copia el enlace que aparece (`/accept-invite?token=…`) — **el código solo se
   muestra una vez**: en la base de datos únicamente vive su hash, así que si lo
   pierdes hay que revocar la invitación y emitir otra.
3. La persona invitada abre ese enlace, elige contraseña y entra ya con la
   membresía del espacio y el rol que llevaba la invitación.

Una invitación caduca (7 días por defecto), sirve **una sola vez** y se puede
revocar desde la misma pantalla. Por API:

```bash
curl -X POST http://localhost:8001/admin/invitations \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"email":"ana@example.com","tenant_id":"<uuid>","role":"tenant_user"}'
```

### 6. Login y dashboard

Ve a http://localhost:3000/login, entra con
`root@example.com / longenoughpw`. Aterrizas en
`/admin/dashboard` con la salud de los servicios.

O por API:

```bash
curl -X POST http://localhost:8001/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"root@example.com","password":"longenoughpw"}'
```

Obtienes un JWT con el claim `sys: true`.

### 7. Crea tu primer tenant

```bash
TOKEN="<el access_token del paso anterior>"

curl -X POST http://localhost:8001/admin/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Mi Empresa","slug":"mi-empresa"}'
```

A partir de aquí, los planes 01+ añadirán endpoints para crear
proyectos, equipos, miembros, planes con tareas...

## Inicializar Vault (opcional en dev)

En dev Vault va en modo `-dev` (auto-unsealed, root token conocido).
Si quieres practicar el flujo de **producción** con Shamir 3-of-5:

```powershell
.\scripts\init-vault.ps1                   # Windows
```

```bash
./scripts/init-vault.sh                    # Linux / macOS
```

Genera 5 unseal keys + root token bajo `./vault-init-output/`
(gitignored, ACL/`chmod 600`). La lógica completa está en la
sección 4.x del documento maestro.

## Correr los tests E2E (Playwright)

El flujo manual de "4 terminales" (docker / api-server / admin-panel /
playwright) es tedioso. Hay un script que lo automatiza:

```powershell
.\scripts\dev\run-e2e.ps1
```

Hace en orden: levanta el stack, espera healthy, aplica migraciones,
lanza `uvicorn` en `:8001` en background, espera `/healthz`, asegura
que existe el user admin (registro + promoción a `is_system_admin`),
corre Playwright (que arranca su propio `npm run dev` vía
`webServer:` del config) y mata el uvicorn al terminar — incluso si
los tests fallan.

Parámetros opcionales:

```powershell
.\scripts\dev\run-e2e.ps1 -ApiPort 8002 -AdminEmail other@x.test
```

Para verlos correr en vivo en un Chromium real (útil para revisión visual):

```powershell
# Todos los specs en headed con 800 ms entre acciones para poder leer.
.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 800

# Solo un spec en concreto:
.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 800 -Spec e2e/project-wizard.spec.ts

# Modo interactivo (Playwright UI): time-travel, pasos manuales,
# re-run con hot reload.
.\scripts\dev\run-e2e.ps1 -Ui
```

Guía completa con las 4 maneras de ver / depurar los E2E, atajos,
y resolución de problemas: [docs/03-guides/watching-e2e-tests.md](../03-guides/watching-e2e-tests.md).

Pre-requisitos (una sola vez):

```powershell
.\scripts\dev\bootstrap.ps1               # crea .venv + deps Python
cd apps\admin-panel; npm install          # deps Node
npm run e2e:install                       # descarga Chromium (~300 MB)
```

## Si algo falla

[`docs/03-guides/gotchas/`](../03-guides/gotchas/) cubre los issues
más comunes del setup. Si la trampa no está documentada y la has
resuelto, añade una nota nueva siguiendo el formato del README.
