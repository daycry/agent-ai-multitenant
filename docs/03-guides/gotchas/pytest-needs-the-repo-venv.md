---
title: "pytest muere en la recolección con ModuleNotFoundError: shared_domain"
status: published
created: 2026-07-27
docs_language: es
---

# `ModuleNotFoundError: No module named 'shared_domain'` al correr los tests

## Síntoma

```
$ python -m pytest tests/unit/test_seed_tools_runtime_wired.py -q
E   ModuleNotFoundError: No module named 'shared_domain'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

Desconcierta por dos motivos: **falla en la recolección**, así que ni un solo
test llega a correr; y falla en un fichero que _no has tocado_, lo que invita a
pensar que has roto algo del import.

Peor todavía: `import api_server` **sí funciona** con ese mismo intérprete
(`pythonpath = ["."]` de `pyproject.toml` alcanza para los paquetes bajo `apps/`),
así que el fallo parece selectivo y arbitrario.

## Causa raíz

Los paquetes de `packages/` (`shared-domain`, `shared-db`, `shared-llm`, …) están
instalados en **editable** en el venv del repo (`.venv/`), no en el Python global.
En Windows/Laragon, un `python` sin activar el venv resuelve al intérprete del
sistema (`C:\laragon\bin\python\python-3.13\python.exe`), que no los tiene.

`CONTINUE_HERE.md` documenta los comandos como `python -m pytest …` dando por
supuesto un venv activado. Si tu shell no lo activa —y la del agente no lo
hace—, todos esos comandos fallan igual.

## Fix

Invoca el intérprete del venv explícitamente, sin depender de la activación:

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ -q
.venv/Scripts/python.exe -m mypy apps/ packages/
```

Y para la suite del agent-runtime, que **no está en `testpaths`** y se corre desde
su propio directorio:

```bash
cd docker/agent-runtimes/agent-runtime
../../../.venv/Scripts/python.exe -m pytest tests/ -q
```

## Cómo distinguirlo de un import roto de verdad

Un segundo antes de ponerte a depurar el fichero:

```bash
.venv/Scripts/python.exe -c "import shared_domain, api_server; print('ok')"
```

Si eso imprime `ok`, no hay nada roto en el código: estabas usando el intérprete
equivocado.
