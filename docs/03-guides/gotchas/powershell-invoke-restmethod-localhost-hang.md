---
title: `Invoke-RestMethod http://localhost:NNNN/...` se cuelga hasta TimeoutSec en Windows
area: windows
encountered: 2026-05-21
stack: Windows 11, PowerShell 5.1, uvicorn binding 127.0.0.1 only
---

## Síntoma

Un servidor local (uvicorn) está corriendo y responde:

```powershell
PS> Invoke-WebRequest -Uri "http://localhost:8002/healthz" -UseBasicParsing
StatusCode : 200
```

Pero la versión con `Invoke-RestMethod` se cuelga hasta el timeout:

```powershell
PS> Invoke-RestMethod -Uri "http://localhost:8002/healthz" -TimeoutSec 2
Invoke-RestMethod : La operación sobrepasó el tiempo de espera.
```

El servidor nunca registra la petición (no aparece en su access log).
Cambiar a `127.0.0.1` arregla el problema instantáneamente:

```powershell
PS> Invoke-RestMethod -Uri "http://127.0.0.1:8002/healthz" -TimeoutSec 2
status
------
ok
```

## Causa raíz

En Windows, el resolver del sistema devuelve `::1` (IPv6 loopback)
**antes** que `127.0.0.1` para `localhost`. uvicorn por defecto solo
bindea `127.0.0.1` (IPv4). `Invoke-RestMethod` en PS 5.1 prueba la
primera dirección, se queda esperando ACK del three-way handshake
sobre IPv6, y no cae al fallback IPv4 a tiempo dentro del
`-TimeoutSec`. `Invoke-WebRequest -UseBasicParsing` usa el mismo
HttpWebRequest por debajo, pero su comportamiento de retry es
ligeramente distinto y suele funcionar.

`curl.exe` y `python -m requests` también caen rápido a IPv4 y por
eso "funcionan" donde `Invoke-RestMethod` falla.

## Fix

Usa `127.0.0.1` explícito (no `localhost`) en los URLs que pasas a
`Invoke-RestMethod` cuando el servidor solo bindea IPv4:

```powershell
# MAL — se cuelga
Invoke-RestMethod -Uri "http://localhost:$ApiPort/healthz" -TimeoutSec 2

# BIEN — funciona
Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2
```

Alternativas si necesitas `localhost`:

- Bindea uvicorn a `--host 0.0.0.0` (escucha en IPv4 e IPv6).
- O cambia a `Invoke-WebRequest -UseBasicParsing`.

## Cómo verificar el fix

```powershell
# Lanza uvicorn en background
$p = Start-Process -PassThru -NoNewWindow -FilePath python `
    -ArgumentList '-m','uvicorn','api_server.main:app','--port','8002'
Start-Sleep 3

# Estos dos calls deben ambos devolver 200 OK ahora
Invoke-RestMethod -Uri "http://127.0.0.1:8002/healthz" -TimeoutSec 2
Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8002/healthz"

taskkill /F /T /PID $p.Id
```
