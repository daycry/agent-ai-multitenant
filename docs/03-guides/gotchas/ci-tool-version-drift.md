---
title: CI debe correr pre-commit, no las herramientas sueltas — drift de versiones
area: ci
encountered: 2026-05-21
stack: GitHub Actions, pre-commit, black, ruff, mypy, prettier
---

## Síntoma

Localmente `black --check .` pasa sin problemas. En CI:

```
would reformat /home/runner/.../tests/integration/conftest.py
Oh no! 💥 💔 💥
1 file would be reformatted, 44 files would be left unchanged.
Error: Process completed with exit code 1.
```

El archivo está formateado correctamente; lo que difiere es la
versión del formateador.

## Causa raíz

El workflow CI instalaba las herramientas sueltas:

```yaml
- run: python -m pip install --upgrade pip black ruff mypy
- run: black --check .
```

`pip install black` (sin pin) resuelve a la **última versión** del
índice. Si la latest es `black 25.x` y el `.pre-commit-config.yaml`
local pinea `black 24.4.2`, ambos disienten en algún detalle
(comas finales, ruptura de líneas, comillas largas, etc.) y el
check del CI falla.

Mismo problema con `ruff`, `mypy`, `prettier`: cada uno avanza con
sus reglas y rompe el equilibrio local-vs-CI.

## Fix

**Reusar el mismo `pre-commit` en CI** que el dev usa localmente.
`.pre-commit-config.yaml` pin las versiones exactas de cada hook,
así que ambos lados se comportan igual:

```yaml
# .github/workflows/ci.yml — job lint-python
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
    cache: pip

- name: Install editable apps so mypy resolves local imports
  run: |
    python -m pip install --upgrade pip
    python -m pip install -e "apps/api-server[dev]"
    python -m pip install -e "apps/watchdog[dev]"
    python -m pip install pre-commit

- name: Run pre-commit on all files
  run: pre-commit run --all-files --show-diff-on-failure
```

`pre-commit run --all-files` corre todos los hooks (black, ruff,
ruff-format, mypy, prettier, end-of-file-fixer, etc.) con las
versiones pinneadas. `--show-diff-on-failure` muestra el diff en
el log del run para debug inmediato sin clonar el repo.

## Cómo verificar el fix

Tras el push, los pasos `black --check`, `ruff check`, `mypy` del
workflow viejo desaparecen y queda **un solo paso** "Run pre-commit
on all files" que reporta cada hook individualmente. Si pasa
localmente, debería pasar en CI.

## Consecuencias positivas

- Una sola fuente de verdad para las versiones de las herramientas.
- Cambiar la versión de black en el `.pre-commit-config.yaml` aplica
  inmediatamente tanto al hook local como al check del CI.
- Menos pasos en el workflow.
- Logs uniformes (un dev sabe exactamente qué buscar — el mismo
  output que el hook local).

## Notas

- Si añades un linter NUEVO en el CI (e.g. `mypy-typed-tests`), añádelo
  primero a `.pre-commit-config.yaml`, no como step suelto.
- Mantén el `cache: pip` de `setup-python`; `pre-commit` cachea los
  envs de hooks bajo `~/.cache/pre-commit/`, así que el primer run
  sigue siendo el más lento pero los siguientes son rápidos.
- `pre-commit` también acepta `--from-ref / --to-ref` para correr
  solo los hooks contra los archivos cambiados de un PR, útil si
  el suite se vuelve grande.
