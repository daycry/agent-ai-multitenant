---
title: `commit_to_branch` (helper de tests git) no es re-entrante en Windows
area: windows
encountered: 2026-07-30
stack: Git for Windows 2.4x, Python 3.13 (shutil.rmtree), pytest tmp_path
---

## Síntoma

Un test de integración que llama DOS veces a
`tests/integration/_git_helpers.commit_to_branch()` sobre **la misma rama** del
mismo bare falla en la segunda llamada con:

```
subprocess.CalledProcessError: Command '['git', 'clone',
 '...\\repos\\backend.git', '...\\repos\\backend.git.tmp-plan\\abcd1234-cierre']'
 returned non-zero exit status 128
```

La primera llamada pasa; solo revienta la segunda, y solo en Windows.

## Causa raíz

`commit_to_branch` clona el bare en un scratch **cuyo nombre deriva de la rama**
(`{bare}.tmp-{branch}`) y al final hace `shutil.rmtree(scratch, ignore_errors=True)`.

En Windows los objetos de `.git` se crean **read-only**, así que `rmtree` lanza
`PermissionError` en cada uno… y `ignore_errors=True` los **silencia**: el
directorio sobrevive con contenido. La segunda llamada intenta clonar en un
destino que ya existe y no está vacío → `git clone` sale con 128.

(Nota adicional: como la rama lleva `/`, el scratch es un directorio anidado —
`backend.git.tmp-plan/abcd1234-cierre`— así que ni siquiera se ve como basura al
lado del bare.)

## Fix

En los tests, para **avanzar la punta de una rama** que ya existe no se clona: se
usa plumbing dentro del propio bare, que no crea directorios temporales:

```python
tree = _run_git("rev-parse", f"refs/heads/{branch}^{{tree}}", cwd=bare).strip()
parent = _run_git("rev-parse", f"refs/heads/{branch}", cwd=bare).strip()
sha = _run_git("commit-tree", tree, "-p", parent, "-m", msg, cwd=bare,
               env_extra=identity).strip()
_run_git("update-ref", f"refs/heads/{branch}", sha, cwd=bare)
```

Ver `_advance_branch` en `tests/integration/test_plan_close_pushes_branch.py`.
`commit_to_branch` se sigue usando para **crear** la rama (una vez por rama).

## Cómo verificar el fix

```powershell
$env:TEST_PG_DB_NAME="agentic_platform_test_cadena_pr"
.venv\Scripts\python.exe -m pytest tests/integration/test_plan_close_pushes_branch.py -q
```

Los tres casos pasan; el que avanza la punta dos veces sobre la misma rama es
`test_close_pushes_the_closure_commit_in_incremental_mode`.
