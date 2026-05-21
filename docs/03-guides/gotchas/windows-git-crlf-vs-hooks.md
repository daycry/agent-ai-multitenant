---
title: `core.autocrlf=true` en Windows pelea con el hook `mixed-line-ending`
area: windows
encountered: 2026-05-20
stack: Git for Windows, pre-commit-hooks
---

## Síntoma

Loop infinito de pre-commit:

1. `git add` mete CRLF en el index (autocrlf).
2. El hook `mixed-line-ending --fix=lf` los pasa a LF.
3. Los archivos quedan unstaged.
4. Reintentas, vuelve a 1.

`git commit` nunca termina porque pre-commit modifica los archivos
en cada pasada.

## Causa raíz

`Git for Windows` instala con `core.autocrlf=true`: al hacer `add`
convierte LF → CRLF para el index. Pero el hook fuerza LF en el
disco, lo que crea una desincronización persistente entre disco e
index.

## Fix

Añade `.gitattributes` en la raíz que **fuerce LF** para todo archivo
de texto, sobreescribiendo `autocrlf`:

```gitattributes
* text=auto eol=lf

# Scripts Windows mantienen CRLF
*.ps1  text eol=crlf
*.cmd  text eol=crlf
*.bat  text eol=crlf

# Binarios — no tocar
*.png    binary
*.docx   binary
*.zip    binary
...
```

Tras añadirlo, normaliza el repo entero:

```bash
git add --renormalize .
git commit -m "style: normalize line endings via .gitattributes"
```

## Cómo verificar el fix

```bash
git add -A
git commit -m "test"
# No aparece "files were modified by this hook" en mixed-line-ending.
```

Y al clonar en otro Windows:

```bash
git ls-files --eol | grep "lf\b" | wc -l   # > 0, esperado
```
