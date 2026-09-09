---
title: 'Con `$ErrorActionPreference = "Stop"`, el stderr de un .exe mata el script — y el síntoma parece un cuelgue'
area: dev-tooling, windows
encountered: 2026-09-09
stack: PowerShell 5.1, scripts/dev/up.ps1, celery
---

## Síntoma

`scripts/dev/up.ps1 -Runs` arranca api-server, worker y orchestrator —los tres
sanos, `/healthz` en 200 y `celery inspect ping` respondiendo a mano— y **nunca
llega a arrancar el panel**. No imprime ningún error: la ejecución simplemente
termina en el paso del ping del worker. Tres relanzamientos con el mismo final.

Y el diagnóstico natural es el equivocado: «el bucle del ping se ha colgado». Se
descarta buscando el proceso (`Get-CimInstance Win32_Process`) y comprobando que
NO hay ningún `celery inspect` vivo. No estaba colgado: estaba muerto.

## Causa raíz

Dos cosas que por separado son razonables:

1. La cabecera del script hace `$ErrorActionPreference = "Stop"`, que es lo que
   uno quiere en un script de arranque: si algo falla, para.
2. Mientras el worker arranca (~40 s en Windows: pool de hilos + los ~7 s de
   `mingle: searching for neighbors`), `celery inspect ping` escribe
   **`Error: No nodes replied within time constraint`** en **stderr** y devuelve
   un código de salida distinto de cero. Es el caso NORMAL, no una avería.

Y aquí está el detalle que las junta: en PowerShell, con
`$ErrorActionPreference = "Stop"`, **el stderr de un ejecutable NATIVO se
convierte en un error terminante** (`NativeCommandError`). No es un
`$LASTEXITCODE` que puedas mirar: es una excepción que aborta el script en la
primera iteración del bucle. Ni `2>$null` ni `| Out-Null` lo evitan — el
redirigido es el flujo, no la conversión en excepción.

O sea: un bucle de reintentos perfectamente escrito, con su deadline y su
comprobación del código de salida, **no llega a reintentar nunca**.

## Fix

Bajar la preferencia SÓLO alrededor del bucle, y restaurarla después:

```powershell
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
while ((Get-Date) -lt $deadline) {
    & $venvPython -m celery -A workers.celery_app:app inspect --timeout 5 ping 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 2
}
$ErrorActionPreference = $prevEap
if (-not $ok) { throw "..." }
```

Los otros dos detalles del mismo arreglo, que también costaron una pasada cada
uno:

- **`--timeout 5` en el `inspect`**: sin él la espera de la respuesta puede
  quedarse larga, y como el `while` sólo se evalúa ENTRE iteraciones, un comando
  que no vuelve convierte un bucle acotado en una espera infinita. Ahí sí
  hubiera sido un cuelgue de verdad.
- **El deadline, 120 s y no 30**: medido, el worker tarda ~40 s hasta
  `celery@host ready` y cada `inspect` gasta ~8 s en su propio arranque. Con 30 s
  el bucle se agotaba con el worker sano.

## Cómo detectarlo la próxima vez

El olor es: **un script de PowerShell que "termina antes de tiempo" sin mensaje**
justo después de invocar un `.exe` que escribe en stderr. Comprobación de 10
segundos:

```powershell
$ErrorActionPreference = "Stop"
& cmd /c "echo algo 1>&2; exit 0"   # <- lanza NativeCommandError, aunque exit 0
```

Si eso te revienta la consola, es esto.

## Relacionado

- El `| tail` de una tubería en la herramienta Bash **bufferiza toda la salida**
  hasta que el comando termina, así que un script que aborta a mitad no imprime
  nada y refuerza la impresión de cuelgue. Para diagnosticar un `up.ps1`,
  redirige a un fichero (`> .dev/up.log 2>&1`) y míralo mientras corre.
- [`redis-con-contrasena-rompe-la-integracion.md`](redis-con-contrasena-rompe-la-integracion.md)
  — el otro fallo de esta familia: la causa no se parece al síntoma.
