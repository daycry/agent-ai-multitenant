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

**Resolución definitiva (2026-07-08, «mypy-total»):** el hook dejó de usar
`mirrors-mypy` y su venv aislado. Ahora es un hook `repo: local` +
`language: system` que ejecuta `scripts/mypy_gate.py`: corre mypy con el
**entorno del proyecto** (el intérprete actual si tiene mypy — el caso de CI,
que instala todo el workspace editable — o el `.venv` del repo como fallback),
así que TODOS los paquetes hermanos resuelven y los excludes por path del hook
desaparecieron (solo quedan migraciones y el SDK generado, en `pyproject.toml`).
Chequea el árbol completo en cada commit con caché incremental.

Las dos opciones históricas siguen documentadas por si un entorno nuevo
reproduce el síntoma con otro runner aislado:

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

## Variante con varios agentes en el mismo árbol: el caché miente (2026-08-13)

El gate `scripts/mypy_gate.py` corre con **caché incremental** (`.mypy_cache/`).
Si otro carril está editando ficheros del mismo árbol MIENTRAS tú corres el
gate, el caché puede quedar a medias y denunciar un módulo que existe:

```text
apps\workers\src\workers\git_remote_sweep.py:58: error:
  Module "api_server.db" has no attribute "platform_settings"  [attr-defined]
Found 1 error in 1 file (checked 703 source files)
```

`api_server/db/platform_settings.py` está ahí, no está en `exclude`, y el mismo
gate había dicho **Passed** veinte minutos antes. No busques el bug en el
código: **tira el caché y repite**.

```bash
rm -rf .mypy_cache && .venv/Scripts/python.exe scripts/mypy_gate.py
# Success: no issues found in 703 source files
```

Antes de perseguir un `attr-defined` sobre un submódulo que existe, comprueba
`git status`: si hay ficheros ajenos modificados, la pasada en frío es la única
que cuenta. En CI no pasa —cada job arranca sin caché—, así que un error que
sólo aparece en local y desaparece en frío es exactamente esto.
