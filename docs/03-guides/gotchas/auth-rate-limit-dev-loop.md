---
title: Rate limit de `/auth/login` se acumula entre ejecuciones repetidas del E2E
area: redis / auth
encountered: 2026-05-21
stack: api-server task_00_10, Redis 7, rate limit sliding-window
---

## Síntoma

El primer pase del E2E va verde. Al rerun N veces seguidas
(ajustando algo del script, depurando, etc.), llega un momento en
que `/auth/login` empieza a devolver `429 Too Many Requests` —
incluso para la credencial buena.

Si tu lógica del script trata "cualquier non-2xx" como "password
malo" y vuelve a intentar registrar, el `/auth/register` devuelve
`409 Conflict` (el user existe), y el script aborta con un mensaje
falso de "DIFFERENT password" que no se corresponde con la realidad.

## Causa raíz

`task_00_10` implementó rate limit sliding-window por **IP** y por
**email** sobre `/auth/login`, con contadores guardados en Redis:

```
rl:login:email:<email>
rl:login:ip:<ip>
```

Los contadores tienen TTL bajo (~1 min) pero en un loop de
desarrollo (script que reintenta + Playwright que prueba "wrong
password") se llena el bucket antes de que expire. Como uvicorn
escucha desde la misma IP (`127.0.0.1`), todos los intentos van al
mismo bucket.

En producción esto es exactamente lo que queremos. En dev es
fricción.

## Fix

Antes de probar el flow de login en dev, limpia los buckets:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  exec -T redis redis-cli DEL \
    "rl:login:email:$ADMIN_EMAIL" \
    "rl:login:ip:127.0.0.1"
```

En el código de script, distingue 429 de 401:

```powershell
function Get-AdminLoginStatus {
    try {
        Invoke-RestMethod ... -ErrorAction Stop | Out-Null
        return 'ok'
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        switch ($code) {
            401 { return 'bad-password' }
            429 { return 'rate-limited' }
            default { return "other:$code" }
        }
    }
}
```

…para no decir "password mismatch" cuando en realidad es brownout.

## Cómo verificar el fix

```bash
# Trigger el rate-limit (6+ intentos con la password mala)
for i in $(seq 1 8); do
    curl -s -o /dev/null -w "%{http_code}\n" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"root@example.com","password":"nope"}' \
        http://127.0.0.1:8001/auth/login
done
# 401 401 401 401 401 429 429 429 (o similar)

# DEL las claves y reintenta — vuelve 401 puro (sin 429)
docker compose ... exec redis redis-cli DEL \
    "rl:login:email:root@example.com" "rl:login:ip:127.0.0.1"
```

`scripts/dev/run-e2e.{ps1,sh}` borra estas claves antes de la
comprobación de admin y distingue 401 vs 429 en su lógica de
register-fallback.
