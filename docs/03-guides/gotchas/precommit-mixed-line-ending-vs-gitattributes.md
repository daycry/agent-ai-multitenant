---
title: `mixed-line-ending --fix=lf` ignora `.gitattributes` y rompe `.ps1`
area: pre-commit
encountered: 2026-05-21
stack: pre-commit-hooks (mixed-line-ending), Git for Windows, GitHub Actions
---

## Síntoma

En CI (Linux runner) tras hacer push desde Windows:

```
mixed line ending........................................................Failed
- hook id: mixed-line-ending
- exit code: 1

scripts/dev/bootstrap.ps1: fixed mixed line endings
```

El hook intenta reescribir `.ps1` con LF. Si lo permites, el script
deja de funcionar como espera PowerShell (que entiende CRLF nativo)
y rompes el flujo Windows.

## Causa raíz

`.gitattributes` dice que los archivos Windows-native conservan CRLF:

```gitattributes
* text=auto eol=lf

*.ps1  text eol=crlf
*.cmd  text eol=crlf
*.bat  text eol=crlf
```

Pero **`mixed-line-ending` no respeta `.gitattributes`**. Su flag
`--fix=lf` aplica LF a TODO archivo de texto sin distinguir entre
plataformas. Resultado: cada CI run del workflow re-detecta el CRLF
del `.ps1` y exige fixearlo.

## Fix

Excluir los Windows-native scripts del hook:

```yaml
- id: mixed-line-ending
  args: [--fix=lf]
  # Windows-native scripts keep CRLF (see .gitattributes).
  # mixed-line-ending doesn't honour .gitattributes, so we exclude
  # them explicitly here.
  exclude: \.(ps1|cmd|bat)$
```

Mantén `.gitattributes` como fuente de verdad para line endings;
el `exclude:` del hook solo está para que el formateador
**no entre en conflicto** con esa fuente.

## Cómo verificar el fix

```bash
.venv/Scripts/pre-commit run mixed-line-ending --all-files
# All passed; no menciona scripts/dev/bootstrap.ps1.
```

Y en CI, tras el siguiente push: el step "Run pre-commit on all files"
no falla en `mixed-line-ending`.

## Notas

- El hook también soporta `--fix=crlf` (todo a CRLF) y `--fix=no`
  (solo detecta). Ninguno encaja en un repo mixto.
- Si añades en el futuro otros archivos Windows-native (e.g.
  `.reg`, `.vbs`), añádelos al `exclude:`.
