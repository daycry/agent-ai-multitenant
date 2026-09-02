---
title: "`git clean -fdx` sale con rc=1 si no puede borrar UNA entrada, y tumba la provisión"
area: git
encountered: 2026-09-01
stack: git 2.x, workers (sync_to_head), agent-runtime (file_tools)
---

# `git clean` falla entero si no puede borrar una entrada

## Síntoma

Una tarea queda `workspace_unavailable` en **cada** reintento. En el log del
worker, la provisión muere en `sync_to_head` con:

```text
git clean -fdx -e vendor -e node_modules ... failed (rc=1):
warning: failed to remove .agent-runtime-tmp.vendor.0/pkg/a.php: Permission denied
```

## Causa raíz

`git clean` no es best-effort: si **cualquier** entrada no se puede borrar,
avisa y sale con código 1 (`builtin/clean.c`: `return !!errors`). `_run_git`
convierte ese rc en `GitCommandError`, y la provisión aborta.

La entrada imborrable la deja el propio sistema. Las tools destructivas del
runtime apartan antes de destruir (ADR 0164): renombran el árbol a
`.agent-runtime-tmp.<nombre>.<n>` y lo descartan después. Cuando el descarte no
puede —un fichero de sólo lectura, un directorio sin permiso de escritura que
dejó el toolchain en otro contenedor— el residuo se queda. **Antes** ese mismo
contenido vivía dentro de `vendor/`, preservado del `clean` por `-e vendor`, y
nadie lo tocaba; el patrón de apartar lo movió a un nombre que NO se preserva.

Verificado en Windows con un fichero abierto por otro proceso (el equivalente
al `EACCES` de Linux): rc=1.

## Fix

Dos capas, las dos en el repo desde el 2026-09-01:

1. **El runtime no se rinde a la primera.** `WorkspaceFiles._descartar` da
   permiso de escritura (`chmod` al elemento y a su padre) y reintenta antes de
   dejar el residuo: resuelve los dos motivos reales.
2. **El worker barre antes de limpiar.** `WorktreeManager.sync_to_head` llama a
   `barrer_residuos_del_runtime` ANTES del `git clean`, con `rmtree_forzado`
   (misma estrategia de `chmod` + reintento). Lo que ni así se puede borrar se
   **preserva del `clean`** (`-e <nombre>`) y se registra
   (`worktree.runtime_residue_not_removable`): un residuo huérfano es un fallo
   menor, una tarea que no vuelve a arrancar no lo es.

## Cómo reconocerlo la próxima vez

Un `GitCommandError` de `git clean` con `warning: failed to remove` en el
mensaje. Si la ruta empieza por `.agent-runtime-tmp.`, es esto. Si no, es otro
fichero imborrable en el worktree, y la pregunta es quién lo dejó con esos
permisos (normalmente un contenedor de `stack_exec` corriendo con otro uid).
