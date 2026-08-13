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

## Variante que este gotcha NO cubría: un `rev` que no fija nada (2026-08-13)

Reusar pre-commit en CI resuelve el caso de arriba **si el `rev` fija de
verdad la versión de la herramienta**. Con prettier no la fijaba, y el
síntoma fue idéntico al original pero con la conclusión contraria: mismo
`rev` en los dos lados, `pre-commit run --all-files` **verde en local** y
reescribiendo 16 ficheros en CI (uniones de TypeScript largas partidas de
otra forma, tablas Markdown repadeadas).

La cadena:

```text
.pre-commit-config.yaml  rev: v4.0.0-alpha.8   (pre-commit/mirrors-prettier, ARCHIVADO)
  └── npm: prettier@4.0.0-alpha.8   ← no trae formateador; es una cáscara
        └── "@prettier/cli": "^0.3.0"          ← rango CARET
              └── prettier (peer, SIN fijar)   ← "el último 3.x publicado HOY"
```

El formateador real acababa siendo el último prettier estable **del día en
que se creó el entorno del hook**:

| Entorno                               | Creado     | prettier efectivo |
| ------------------------------------- | ---------- | ----------------- |
| caché local `~/.cache/pre-commit`     | 2026-05-30 | 3.8.3             |
| runner de CI (entorno nuevo cada run) | 2026-08-13 | 3.9.6             |

Y 3.9.x cambió cómo corta `type X = A \| B \| …` cuando cabe en `printWidth`.
Un desarrollador con caché vieja nunca ve el fallo; CI lo ve siempre.

**Cómo detectarlo en 30 segundos** — pregúntale su versión al binario que
instaló el hook, no a la config:

```bash
node ~/.cache/pre-commit/repo<hash>/node_env-default/Scripts/node_modules/prettier/bin/prettier.cjs --version
# 3.8.3   ← con rev "v4.0.0-alpha.8" en el config. Ahí está la mentira.
```

**Fix**: `repo: https://github.com/rbubley/mirrors-prettier` con `rev: v3.9.6`
— el fork mantenido del mirror archivado, cuyo hook declara
`additional_dependencies: ["prettier@3.9.6"]` EXACTO, y prettier 3.x sí trae
su propio CLI (sin la indirección `@prettier/cli`).

La regla general que deja este caso: **un `rev` pineado no garantiza una
herramienta pineada.** Si el paquete que el mirror instala es un proxy sobre
un rango (`^`, `~`, un peer sin declarar), la versión flota igual. Comprueba
`--version` del binario instalado antes de dar por bueno un pin.

## Notas

- Si añades un linter NUEVO en el CI (e.g. `mypy-typed-tests`), añádelo
  primero a `.pre-commit-config.yaml`, no como step suelto.
- Mantén el `cache: pip` de `setup-python`; `pre-commit` cachea los
  envs de hooks bajo `~/.cache/pre-commit/`, así que el primer run
  sigue siendo el más lento pero los siguientes son rápidos.
- `pre-commit` también acepta `--from-ref / --to-ref` para correr
  solo los hooks contra los archivos cambiados de un PR, útil si
  el suite se vuelve grande.
