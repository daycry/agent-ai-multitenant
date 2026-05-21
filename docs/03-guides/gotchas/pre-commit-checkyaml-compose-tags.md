---
title: `check-yaml` rechaza los tags custom de docker-compose (`!reset`, `!override`)
area: pre-commit
encountered: 2026-05-21
stack: pre-commit-hooks check-yaml, PyYAML
---

## Síntoma

```
check yaml...............................................................Failed
- hook id: check-yaml
- exit code: 1

could not determine a constructor for the tag '!reset'
  in "docker/docker-compose.dev.yml", line 52, column 14
```

El commit no llega: el hook revienta antes.

## Causa raíz

`pre-commit-hooks` usa el loader **safe** de PyYAML, que no conoce
los tags custom que docker-compose añadió (`!reset`, `!override`).
Esos tags son válidos para Compose pero no para PyYAML estándar.

## Fix

Pasa `--unsafe` al hook (permite tags custom; aún valida que el YAML
es sintácticamente correcto, solo no falla por constructores
desconocidos):

```yaml
- repo: https://github.com/pre-commit/pre-commit-hooks
  hooks:
    - id: check-yaml
      args: [--allow-multiple-documents, --unsafe]
```

## Cómo verificar el fix

```bash
.venv/Scripts/pre-commit run check-yaml --all-files
# All passed, incluyendo docker-compose.dev.yml con `!reset`.
```
