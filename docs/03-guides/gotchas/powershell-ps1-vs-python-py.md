---
title: PowerShell scripts (.ps1) no se invocan con python.exe
area: powershell, windows
encountered: 2026-05-26
stack: Windows 11 + PowerShell + venv Python 3.12
---

## Síntoma

Lanzas un script PowerShell de `scripts/dev/` mezclando con la convención
del Python venv y obtienes algo como:

```
PS C:\...> .venv\Scripts\python scripts\dev\run-human-tests.ps1
  File "scripts\dev\run-human-tests.ps1", line 26
    [string]$Only = "all",
            ^
SyntaxError: invalid syntax
```

O lanzas un script Python directo (sin invocar el Python del venv) y
no ves nada:

```
PS C:\...> .\scripts\demo_human_04_5_01.py
PS C:\...>   # ← se cierra sin imprimir
```

## Causa raíz

Dos errores opuestos, mismo origen:

1. **`python xxx.ps1`** — Python intenta parsear el script PowerShell
   como código Python. `[CmdletBinding()]` y `[string]$x = "y"` no son
   válidos en Python.

2. **`.\xxx.py`** sin invocar el venv — Windows asocia la extensión
   `.py` al primer Python que encuentra en `PATH` (típicamente el del
   sistema, no el del venv `.venv\Scripts\python.exe`). El intérprete
   del sistema no tiene las dependencias del proyecto, lanza
   `ModuleNotFoundError` y la consola lo cierra antes de imprimir
   nada útil — el usuario ve "nada".

## Fix

Convención por extensión:

| Extensión | Cómo se invoca                                   |
| --------- | ------------------------------------------------ |
| `.py`     | `.\.venv\Scripts\python.exe .\scripts\<demo>.py` |
| `.ps1`    | `.\scripts\dev\<script>.ps1`                     |

La forma directa funciona para `.ps1` porque PowerShell ejecuta scripts
del directorio actual sin prefijo `python`. Los `.py` necesitan el
binario del venv explícitamente para que las dependencias estén
disponibles.

## Cómo verificar el fix

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_04_5_01.py
# →  ========================================================================
# →    demo human_04_5_01 — Memory replay end-to-end
# →  ========================================================================
```

```powershell
.\scripts\dev\run-human-tests.ps1 -Only 04_5 -SkipStack
# →  ==> Reutilizando el stack ya arrancado (flag -SkipStack)
```

## Referencias

- `docs/03-guides/run-demo-human-tests.md` — guía de tests humanos
  arranca con esta misma advertencia.
- `scripts/dev/run-human-tests.ps1` — launcher one-shot que usa la
  convención correcta internamente.
