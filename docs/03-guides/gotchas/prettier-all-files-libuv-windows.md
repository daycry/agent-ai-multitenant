---
title: `pre-commit run prettier --all-files` crashea en Windows por libuv
area: pre-commit
encountered: 2026-06-02
stack: pre-commit (prettier mirror), Node 20/22, Windows 11, libuv
---

## Síntoma

En Windows, correr el hook de prettier sobre todo el repo:

```powershell
python -m pre_commit run prettier --all-files
```

aborta a mitad con un assertion de libuv (la capa de I/O async de
Node) y un exit code enorme, en vez de un fallo de formato normal:

```
Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), file src\win\handle-inl.h
```

PowerShell reporta el proceso como:

```
$LASTEXITCODE  →  3221226505   (0xC0000409, STATUS_STACK_BUFFER_OVERRUN)
```

No es un error de prettier (no lista ningún `.md`/`.ts` mal
formateado): el binario de Node muere antes de terminar. El mismo
hook con `--files <unos pocos>` pasa sin problema.

## Causa raíz

El hook de prettier le pasa **toda la lista de ficheros del repo** a
Node de una vez. Con cientos de ficheros, prettier abre/cierra muchos
handles de fichero concurrentes y, en Windows, libuv golpea una
condición de carrera al cerrar un handle que ya está en estado
`UV_HANDLE_CLOSING`. El `assert` aborta el proceso (STATUS de
buffer-overrun). Es un bug del runtime sobre Windows, **no** de
nuestra config ni de los ficheros: en el runner Linux del CI el mismo
`--all-files` corre bien (ver
[ci-tool-version-drift.md](./ci-tool-version-drift.md)).

## Fix

En Windows, correr prettier **siempre _scoped_** a los ficheros que
tocaste, nunca `--all-files`:

```powershell
# Solo los docs/ficheros cambiados:
python -m pre_commit run prettier --files `
  docs/03-guides/gotchas/prettier-all-files-libuv-windows.md `
  docs/03-guides/gotchas/README.md
```

Con una lista corta, prettier no satura los handles y termina limpio.
Esta es además la convención del repo: ver
[`conventions.md`](../../context/conventions.md) ("prettier siempre
_scoped_ a los ficheros tocados").

Si el hook de `pre-commit` **de commit** (no el manual) crashea al
formatear porque arrastra `--all-files` por una config local, re-stage
de los ficheros y reintenta el commit; **nunca** uses `--no-verify`
para saltarte el hook — eso solo esconde el formateo pendiente.

## Cómo verificar el fix

```powershell
python -m pre_commit run prettier --files docs/03-guides/gotchas/README.md
# "Passed" (o "Failed" con un diff real), exit 0/1 — NUNCA 3221226505.
```

## Notas

- En CI (Linux) `--all-files` sigue siendo correcto y es lo que el
  workflow usa; el problema es exclusivo del binario Node sobre
  Windows.
- Otra mitigación posible (no aplicada) es bajar la concurrencia de
  prettier, pero el flag no se expone por el hook; _scoping_ es el
  arreglo simple y suficiente.
- Si el repo crece y necesitas formatear "todo" en Windows, hazlo por
  lotes (`--files` con subconjuntos) en vez de `--all-files`.
