---
title: "Un «flaky» reportado por revisores en paralelo puede ser contaminación entre ellos, no un test defectuoso"
area: workflows, tests
encountered: 2026-06-30
stack: Workflow `parallel()`, pytest
---

## Síntoma

Una revisión adversarial en paralelo reporta un test como **flaky**: 1 fallo y 2
verdes sobre el mismo commit. El fallo es concreto y creíble, por ejemplo
`Extra items in the set: git_commit` en `test_tool_catalog_contract`.

Al re-correrlo a mano, 74 passed × 3 veces, determinista.

## Causa raíz

Las lentes lanzadas con `parallel()` **comparten el mismo working tree** (no hay
aislamiento salvo `isolation: 'worktree'`). Si una lente muta la fuente de forma
efímera para comprobar algo —añadir un símbolo a un `frozenset` y revertirlo, para
verificar que el test detecta la divergencia—, esa edición **es visible para las
demás mientras está aplicada**. La lente hermana corre el test en ese instante, lo
ve rojo, y lo reporta como flaky.

## Fix

Tres cosas, por orden de utilidad:

1. Las lentes que verifiquen **mutando la fuente** van en serie, o con
   `isolation: 'worktree'`, o se les instruye a verificar por razonamiento sin
   tocar ficheros.
2. Ante un «flaky» reportado por un revisor, **re-correr en serie sobre el árbol
   limpio commiteado** antes de creérselo.
3. `grep` de quién muta el símbolo: si nadie lo hace en código commiteado, es
   contaminación, no defecto.

Perseguir un falso flaky quema mucho tiempo, y aceptarlo como real es peor: se
acaba «arreglando» un test que estaba bien.

## Cómo verificar el fix

`git status --short` limpio + el test 3 veces seguidas en serie. Si pasa siempre,
era contaminación.
