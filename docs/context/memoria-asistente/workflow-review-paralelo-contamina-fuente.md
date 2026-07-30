---
name: workflow-review-paralelo-contamina-fuente
description: Lentes de revisión en paralelo que mutan la fuente para verificar se contaminan entre sí (working tree compartido).
metadata:
  node_type: memory
  type: feedback
  originSessionId: f7e54214-9978-4552-9197-70ecc3f15b3d
---

> **DOCUMENTADO EN EL REPO (2026-07-26)**: `docs/03-guides/gotchas/workflow-parallel-review-source-contamination.md`. La fuente de verdad es esa; esta nota queda como puntero.

En un Workflow, las lentes de revisión adversarial lanzadas con `parallel()` comparten el MISMO working tree (no hay aislamiento salvo `isolation: 'worktree'`). Si una lente hace una **mutación efímera de la fuente** para comprobar que un test "falla ante divergencia" (p. ej. añadir `git_commit` a `RUNTIME_WIRED_TOOL_NAMES` y revertir), esa edición es visible para las otras lentes mientras está aplicada → la hermana ve un fallo fantasma y reporta el test como "flaky".

Ocurrió en 06.18: `test_tool_catalog_contract` salió "flaky" (1 fallo + 2 verdes) con "Extra items: git_commit"; al re-correr en serie sobre el árbol limpio dio 74 passed × 3 determinístico. No era bug de aislamiento del test (ningún test commiteado muta ese frozenset), sino contaminación entre revisores paralelos.

**Why:** un test flaky en CI es inaceptable, pero perseguir un falso flaky quema tiempo; hay que distinguir artefacto-de-workflow de defecto-real.

**How to apply:** (1) las lentes que verifiquen con mutación de fuente deben ir en **serie**, o con `isolation: 'worktree'`, o instruirlas a verificar por razonamiento sin tocar ficheros; (2) ante un "flaky" reportado por un revisor, re-correr en serie sobre el árbol limpio commiteado antes de creérselo; (3) `grep` de quién muta el símbolo: si nadie lo muta en código commiteado, es contaminación, no bug. Relacionado con [[estado-trabajo-en-curso]].
