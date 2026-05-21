---
title: uvicorn en Windows hace `multiprocessing.spawn` y `Stop-Process` deja workers huérfanos
area: windows
encountered: 2026-05-21
stack: Windows 11, Python 3.13 venv, uvicorn 0.x
---

## Síntoma

Lanzas uvicorn con `Start-Process -PassThru -FilePath
.venv\Scripts\python.exe -ArgumentList "-m","uvicorn",...`. El log
de uvicorn dice:

```
INFO: Started server process [5512]
```

Pero `$apiProc.Id` capturado por `Start-Process` es **16212**. Al
hacer `Stop-Process -Id 16212 -Force`, el PID 16212 muere pero el
puerto sigue ocupado por pid 5512 (el worker, ahora huérfano):

```powershell
PS> Get-CimInstance Win32_Process -Filter "ProcessId = 5512" | Select CommandLine
"C:\laragon\bin\python\python-3.13\python.exe" "-c" "from multiprocessing.spawn
 import spawn_main; spawn_main(parent_pid=16212, pipe_handle=876)" "--multiprocessing-fork"
```

El worker se ejecuta desde la **Python base** del venv (Laragon en
este caso), no desde `.venv\Scripts\python.exe`, así que ningún
filtro de "es nuestro proceso" basado en `ExecutablePath` lo
reconoce.

## Causa raíz

Dos efectos sumados:

1. **El "python.exe" del venv en Windows es un shim**. Al ejecutar
   `.venv\Scripts\python.exe -m uvicorn …`, ese shim re-ejecuta el
   intérprete base (`C:\…\python-3.13\python.exe`) con `PYTHONHOME`
   apuntando al venv. Eso es un proceso distinto con un PID nuevo.

2. **`multiprocessing.spawn` re-ejecuta `sys._base_executable`**.
   En Python 3.13 + Windows, `multiprocessing.spawn` arranca workers
   con la Python base, no con el shim del venv. Los args del worker
   no contienen "uvicorn" — solo `multiprocessing.spawn import …`.

`Stop-Process -Id $apiProc.Id` solo mata el PID que le pasas; no
camina la cadena padre→hijo, así que el worker sobrevive y
mantiene el socket en LISTEN (a veces creando un
[Windows TCP ghost listener](./windows-tcp-ghost-listener.md)).

## Fix

Usa **`taskkill /F /T /PID`** (con `/T` = tree) en vez de
`Stop-Process`. `/T` mata el padre Y todos sus descendientes
recursivamente.

```powershell
# MAL — deja workers huérfanos
Stop-Process -Id $apiProc.Id -Force

# BIEN — mata el árbol completo
& taskkill /F /T /PID $apiProc.Id
```

Para detectar workers huérfanos antes de relanzar (e.g. en un
preflight de un script), busca por **patrón de command line**, no
por `ExecutablePath` (porque el ejecutable real puede ser la Python
base):

```powershell
$isOurs = ($cmd -match 'uvicorn') -or
          ($cmd -match 'spawn_main') -or
          ($cmd -match 'multiprocessing')
```

## Cómo verificar el fix

```powershell
$p = Start-Process -PassThru -NoNewWindow -FilePath .\.venv\Scripts\python.exe `
    -ArgumentList "-m","uvicorn","api_server.main:app","--port","8003"
Start-Sleep 3
# Hay un worker hijo:
Get-CimInstance Win32_Process -Filter "ParentProcessId = $($p.Id)" | Select ProcessId, CommandLine

# taskkill /T mata padre + hijos en una sola llamada
& taskkill /F /T /PID $p.Id

# Y el puerto queda libre INMEDIATAMENTE:
Start-Sleep 1
try { ([Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,8003)).Start(); "free" } catch { "blocked" }
```

`scripts/dev/run-e2e.ps1` usa `taskkill /F /T` tanto en su cleanup
como en su preflight de "stray uvicorn".
