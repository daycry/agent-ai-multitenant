---
title: Primeros pasos
docs_language: es
audience: nuevo desarrollador, operador
updated: 2026-05-29
---

# 02-getting-started — Primeros pasos

Tutorial orientado al aprendizaje: los pasos prácticos para que alguien
que acaba de clonar el repo levante el stack en local y llegue a su
primer login como System Admin.

| Documento                                  | Para qué                                                            |
| ------------------------------------------ | ------------------------------------------------------------------- |
| [01-installation.md](./01-installation.md) | Prerequisitos, bootstrap del entorno y arranque del stack Docker    |
| [03-first-run.md](./03-first-run.md)       | Primer arranque: `up.ps1`/`up.sh`, registrar admin, crear un tenant |

> **El hueco `02-*` no es un fichero perdido.** La numeración salta de `01` a
> `03`, y comprobado en el historial completo de git
> (`git log --all --name-only -- docs/02-getting-started/`) **nunca existió un
> `02-*`**: no se borró nada, el `03` se creó ya con ese número. Los índices
> numeran **orden de lectura**, no continuidad, y renumerar `03-first-run.md`
> rompería los enlaces que lo citan sin aportar nada. Anotado en prod-15
> (`task_gov_cabeceras_07`) para que nadie más lo busque.

## Orden recomendado

1. Lee [01-overview](../01-overview/) para entender qué es el sistema.
2. Sigue [01-installation.md](./01-installation.md) para preparar el
   entorno (Python `.venv`, dependencias Node, stack Docker).
3. Sigue [03-first-run.md](./03-first-run.md) para arrancar y entrar
   al panel de System Admin.

## Si algo falla

Antes de inventar una solución para un error de infraestructura,
**busca primero** en [`docs/03-guides/gotchas/`](../03-guides/gotchas/):
trampas conocidas del toolchain (puertos, RLS, asyncpg, mypy, OTEL,
Windows…) con síntoma, causa raíz y fix.
