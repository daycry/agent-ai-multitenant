---
title: Pin `python3.12` en `.pre-commit-config.yaml` rompe en host 3.13
area: pre-commit
encountered: 2026-05-20
stack: pre-commit 3.8, Python 3.13
---

## Síntoma

```
An unexpected error has occurred: CalledProcessError:
  ... -p python3.12
RuntimeError: failed to find interpreter for Builtin discover of
  python_spec='python3.12'
```

`pre-commit run --all-files` muere al inicializar el venv del hook.

## Causa raíz

`default_language_version.python: python3.12` (o `language_version:
python3.12` por hook) le pide a `virtualenv` un binario llamado
exactamente `python3.12` en el sistema. Si el host tiene 3.13 o
3.11, no lo encuentra.

## Fix

Quita el pin. Los formatters / linters no necesitan el python target
para funcionar; el output sí está pinned a 3.12 vía
`target-version` en `pyproject.toml` (black + ruff) y `python_version`
(mypy), que es lo que de verdad importa.

```yaml
# .pre-commit-config.yaml
# NO:
#   default_language_version:
#     python: python3.12
# SÍ: dejar que pre-commit use el python disponible.
```

## Cómo verificar el fix

```bash
.venv/Scripts/pre-commit run --all-files
# Termina sin "failed to find interpreter".
```

Y el output (formato) sigue siendo el mismo porque las herramientas
generan código compatible con py312 por `target-version`.
