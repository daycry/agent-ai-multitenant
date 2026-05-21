# Primer arranque

Cómo arrancar el sistema vacío y registrar un primer System Admin.

## 1. Verifica que el stack está sano

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  ps
```

Los cinco servicios deben aparecer como `Up X (healthy)`.

## 2. Aplica las migraciones

```bash
cd apps/api-server
DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform" \
  .venv/Scripts/python -m alembic upgrade head     # Windows
```

(Linux/macOS: `.venv/bin/python` en lugar de `.venv/Scripts/python`.)

Esto crea las cinco tablas (`organizations`, `users`,
`user_org_memberships`, `sessions`, `audit_log`) con sus índices
y activa las **policies RLS**.

## 3. Levanta el api-server

```bash
cd apps/api-server
.venv/Scripts/uvicorn api_server.main:app --reload --port 8001
```

→ http://localhost:8001/docs (OpenAPI interactivo).

## 4. Registra el primer usuario

Desde el admin-panel (http://localhost:3000/login → enlace de
registro próximamente; por ahora vía API):

```bash
curl -X POST http://localhost:8001/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"root@example.com","password":"longenoughpw","full_name":"Root"}'
```

## 5. Promueve a System Admin

En esta fase no hay endpoint de bootstrap del primer admin: lo
haces directamente en la base de datos. Después de Fase 15 (el
instalador) esto será un wizard.

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  exec postgres \
  psql -U postgres -d agentic_platform \
       -c "UPDATE users SET is_system_admin = true WHERE email = 'root@example.com'"
```

## 6. Login y dashboard

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

## 7. Crea tu primer tenant

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

```bash
./scripts/init-vault.sh
```

Genera 5 unseal keys + root token bajo `./vault-init-output/`
(gitignored, modo 600). La nota está en `scripts/init-vault.sh` y
la lógica completa en la sección 4.x del documento maestro.

## Si algo falla

[`docs/03-guides/gotchas/`](../03-guides/gotchas/) cubre los issues
más comunes del setup. Si la trampa no está documentada y la has
resuelto, añade una nota nueva siguiendo el formato del README.
