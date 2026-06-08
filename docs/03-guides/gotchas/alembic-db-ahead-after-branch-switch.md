---
title: `Can't locate revision identified by 'XXXX'` tras cambiar de branch
area: postgres
encountered: 2026-06-08
stack: Alembic 1.x, asyncpg, PostgreSQL 16, scripts/dev/up.ps1 en Windows
---

## Síntoma

`scripts/dev/up.ps1` (o un `alembic upgrade head` manual) falla en el paso
de migraciones. El wrapper de PowerShell solo muestra:

```
alembic upgrade failed
En ...\scripts\dev\up.ps1: 104 Carácter: 32
+     if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed" }
```

El error **real** de alembic (que se imprimió por encima del `throw`) es:

```
ERROR [alembic.util.messaging] Can't locate revision identified by '0078_skills_category_check'
FAILED: Can't locate revision identified by '0078_skills_category_check'
```

## Causa raíz

La tabla `alembic_version` de la BD apunta a una revisión que **el branch
actual no contiene**:

```sql
SELECT version_num FROM alembic_version;  -- 0078_skills_category_check
```

…pero el branch actual (`feat/personal-assistant-ui`) solo tiene migraciones
hasta `0076_sso_global`. La BD se migró estando en **otro branch**
(`plan/06.18-tools-overhaul`, que sí tiene 0077 y 0078); al volver a este
branch esos ficheros `migrations/versions/*0077*.py` / `*0078*.py` ya no
existen, así que alembic no puede calcular la ruta desde la revisión actual
de la BD hasta `head`.

El volumen `postgres_data` es persistente y **no** se resetea al cambiar de
branch: el esquema de la BD queda "por delante" de lo que el branch conoce.
Por eso un `UPDATE alembic_version` a pelo NO sirve — dejaría en la BD los
objetos de 0077/0078 (CHECKs/UNIQUE de `tools` y `skills`) que alembic ya
no sabría revertir, y volvería a romper al cambiar de branch otra vez.

## Fix

Revertir de verdad las migraciones sobrantes (downgrade), trayendo
temporalmente sus ficheros desde el branch que las define. Las migraciones
son aditivas (solo `drop_constraint`/`drop_index` en el downgrade), así que
es seguro y no destructivo:

```powershell
$repo = "C:\laragon\python\agent-ai-multitenant"
$venvPython = "$repo\.venv\Scripts\python.exe"
$src = "plan/06.18-tools-overhaul"   # branch que define 0077/0078
$f77 = "apps/api-server/migrations/versions/20260603_0077_tools_dedup_taxonomy.py"
$f78 = "apps/api-server/migrations/versions/20260604_0078_skills_category_check.py"

# 1. Traer los ficheros de migración que la BD tiene aplicados pero este branch no
git -C $repo checkout $src -- $f77 $f78

# 2. Downgrade hasta el head REAL de este branch
$env:DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
Push-Location "$repo\apps\api-server"
& $venvPython -m alembic downgrade 0076_sso_global   # ← head del branch actual
Pop-Location

# 3. Limpiar los ficheros temporales y dejar el working tree limpio
git -C $repo reset -q HEAD -- $f77 $f78
Remove-Item "$repo\$f77","$repo\$f78" -Force
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
```

Para descubrir qué branch define la revisión huérfana:

```powershell
git -C $repo branch -a --contains `
  $(git -C $repo log --all --format="%H" -1 -- "apps/api-server/migrations/versions/*0078*")
```

**Alternativa (si NO necesitas conservar los datos de dev):** borra el
volumen y deja que `up.ps1` re-migre desde cero. Más simple pero pierdes lo
sembrado:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down
docker volume rm $(docker volume ls -q | Select-String postgres_data)
.\scripts\dev\up.ps1   # init scripts + alembic upgrade head reconstruyen a 0076
```

## Cómo verificar el fix

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
Push-Location apps\api-server
.venv\..\..\.venv\Scripts\python.exe -m alembic current   # → 0076_sso_global (head)
.venv\..\..\.venv\Scripts\python.exe -m alembic upgrade head  # → exit 0, no-op
Pop-Location
```

`alembic current` debe coincidir con el `head` del branch actual y
`upgrade head` salir con código 0. Tras eso `up.ps1` arranca limpio.

## Prevención

- **Antes de cambiar a un branch con menos migraciones**, haz
  `alembic downgrade <head-del-branch-destino>` mientras todavía estás en el
  branch que tiene los ficheros de las migraciones a revertir. Es mucho más
  fácil que recuperarlos después con `git checkout`.
- Es el mismo patrón que el cache podrido de `.next/` al cambiar de branch
  (ver [nextjs-stale-next-cache-after-branch-switch.md](./nextjs-stale-next-cache-after-branch-switch.md)):
  el estado persistente (volumen / cache) no viaja con `git checkout`.
