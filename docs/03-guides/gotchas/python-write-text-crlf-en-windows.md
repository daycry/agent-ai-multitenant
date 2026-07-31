---
title: Editar un fuente con `pathlib.write_text()` en Windows lo convierte entero a CRLF
area: windows
encountered: 2026-07-31
stack: Python 3.12, Git for Windows, pre-commit
---

## Síntoma

Aplicas un cambio de UNA línea a un `.py` con un script de un solo uso
(`p.write_text(p.read_text().replace(a, b))` — el truco habitual para mutar y
restaurar un fichero al comprobar que un test falla por el motivo correcto), y
acto seguido git avisa:

```
warning: in the working copy of 'apps/.../approval_repo.py',
CRLF will be replaced by LF the next time Git touches it
```

`git diff` sigue enseñando el cambio pequeño (git normaliza al comparar), así
que **no hay nada visible que investigar**. El daño aparece más tarde: el hook
`mixed-line-ending` reescribe el fichero al commitear, o el fichero entra en el
índice con finales de línea distintos a los del resto del repo.

## Causa raíz

`Path.write_text()` abre en modo TEXTO. En modo texto, Python traduce cada `\n`
a `os.linesep`, que en Windows es `\r\n`. Y `read_text()` hace lo simétrico: te
devuelve `\n`. O sea que el round-trip **no es idempotente en Windows** —
`write_text(read_text(...))` reescribe el fichero ENTERO con otros finales de
línea aunque no cambies un solo carácter.

Los editores y las tools de edición del repo escriben bytes, así que esto solo
pasa cuando el cambio lo hace un script Python ad-hoc. Por eso es fácil de
achacar al `.gitattributes` o a `core.autocrlf`, que no tienen nada que ver.

## Fix

Para editar un fuente desde un script, trabaja en BYTES:

```python
data = p.read_bytes()
p.write_bytes(data.replace(b"antes", b"despues"))
```

O, si necesitas texto, desactiva la traducción explícitamente:

```python
p.write_text(nuevo, encoding="utf-8", newline="\n")
```

Si ya lo hiciste, normalizar es una línea:

```python
p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
```

Comprobación rápida de si un fichero quedó tocado:

```bash
grep -qU $'\r' RUTA && echo CRLF || echo LF
```

## Relacionado

- [`windows-git-crlf-vs-hooks.md`](./windows-git-crlf-vs-hooks.md) — la otra
  mitad: `core.autocrlf=true` peleándose con `mixed-line-ending`.
- [`precommit-mixed-line-ending-vs-gitattributes.md`](./precommit-mixed-line-ending-vs-gitattributes.md)
