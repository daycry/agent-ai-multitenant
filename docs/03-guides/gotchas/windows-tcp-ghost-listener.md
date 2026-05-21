---
title: `Get-NetTCPConnection` reporta listeners para procesos muertos en Windows
area: windows
encountered: 2026-05-21
stack: Windows 11, PowerShell 5.1, uvicorn 0.x con --reload o multiprocessing
---

## Síntoma

`netstat -ano | findstr :8001` no devuelve nada, pero
`Get-NetTCPConnection -LocalPort 8001 -State Listen` reporta un
listener con `OwningProcess = 18900`. Intentar bindear al puerto:

```
Solo se permite un uso de cada dirección de socket
```

`Get-Process -Id 18900` y `taskkill /PID 18900` confirman que el
proceso ya no existe. El puerto **sigue ocupado** durante minutos
(a veces hasta el siguiente reinicio).

## Causa raíz

Cuando un proceso que tenía un socket en estado `LISTEN` muere
abruptamente (kill -9, crash, taskkill /F sin /T sobre un padre que
había exec'd a otra cosa), Windows deja el TCB (TCP Control Block)
en un estado huérfano. La tabla MIB que lee `Get-NetTCPConnection`
guarda el último `OwningProcess` conocido, pero el verdadero socket
ya no existe — solo queda la reserva en el kernel.

Curiosamente, `0.0.0.0:8001` y `::1:8001` sí se pueden bindear; solo
la dirección específica `127.0.0.1:8001` queda atascada (porque
uvicorn bindea por defecto a `127.0.0.1`).

Esto es típico cuando se mata el **padre** de un proceso uvicorn que
ya había forkeado un worker vía `multiprocessing.spawn` (ver
[uvicorn-windows-multiprocessing-spawn](./uvicorn-windows-multiprocessing-spawn.md)).

## Fix

1. **Para detectar el problema con fiabilidad**: no confíes en
   `Get-NetTCPConnection`; haz un bind real:

   ```powershell
   function Test-PortBindable {
       param([int]$Port)
       try {
           $l = [System.Net.Sockets.TcpListener]::new(
               [System.Net.IPAddress]::Loopback, $Port)
           $l.Start(); $l.Stop()
           return $true
       } catch { return $false }
   }
   ```

2. **Para no crear ghosts**: usa `taskkill /F /T /PID` (con `/T` =
   tree) en vez de `Stop-Process`, que solo mata el PID indicado y
   deja huérfanos a los workers.

3. **Para desbloquear un puerto ya fantasma**:
   - Esperar 30 s – 2 min (lo más común).
   - Cambiar de puerto temporalmente (`-ApiPort 8002`).
   - Último recurso: `netsh int ip reset` (requiere admin + reboot).

## Cómo verificar el fix

```powershell
# Antes: Get-NetTCPConnection miente, bind falla
try { ([System.Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,8001)).Start() } catch { "blocked" }

# Después del wait/reset/cambio de puerto: bind ok
try { $l=[System.Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,8001); $l.Start(); $l.Stop(); "free" } catch { "blocked" }
```

`scripts/dev/run-e2e.ps1` implementa el bind test como preflight y
falla rápido con un mensaje útil cuando detecta un ghost.
