---
title: El hook mypy de pre-commit no ve los paquetes editables `apps/<x>`
area: pre-commit
encountered: 2026-05-20
stack: pre-commit mirrors-mypy, mypy 1.10+
---

## Síntoma

```
apps/watchdog/src/watchdog/__main__.py:60: error:
  Cannot find implementation or library stub for module named "api_server.logging"
  [import-not-found]
```

mypy local (`.venv/bin/mypy`) pasa sin problemas; el hook del commit
falla.

## Causa raíz

El hook de mypy corre en su **propio virtualenv aislado** (creado
por pre-commit en `~/.cache/pre-commit/`). Ahí solo están instalados
los paquetes listados en `additional_dependencies:`, **no** los
`apps/<x>` editables del repo. Importar `from api_server.logging
import ...` desde `apps/watchdog/...` falla porque api_server no
existe en ese venv.

## Fix

Dos opciones, según el caso:

**(a)** Si la dep es un paquete público (sqlalchemy, pydantic, jose,
opentelemetry-\*), añádelo a `additional_dependencies:`:

```yaml
- id: mypy
  additional_dependencies:
    - "sqlalchemy[asyncio]>=2.0,<3"
    - "types-python-jose>=3.3,<4"
    # ...
```

**(b)** Si la dep es un paquete local (`api_server`, `watchdog`),
**excluye** ese subpath del scope del hook:

```yaml
- id: mypy
  files: ^(apps|packages)/.*\.py$
  exclude: ^apps/(([^/]+/migrations/.*)|watchdog/.*)\.py$
```

## Cómo verificar el fix

```bash
.venv/Scripts/pre-commit run mypy --all-files
# Sale 0; el archivo problemático aparece como "Passed" o no se chequea.
```

Y `mypy` "real" en el venv sigue chequeando todo:

```bash
.venv/Scripts/python -m mypy apps packages
```
