---
title: "El rate limit de `/auth/login` frena el E2E, y el síntoma no es un 429"
area: redis / auth
encountered: 2026-05-21
updated: 2026-08-20
stack: api-server task_00_10, Redis 7, rate limit sliding-window, Playwright 1.60
---

## Síntoma

Tres formas de verlo, de la más obvia a la que te come la tarde.

**(1) El 429 a la cara.** El primer pase del E2E va verde. Al rerun N veces
seguidas (ajustando algo del script, depurando, etc.), llega un momento en que
`/auth/login` empieza a devolver `429 Too Many Requests` — incluso para la
credencial buena.

**(2) El 429 disfrazado de contraseña mala.** Si tu script trata "cualquier
non-2xx" como "password malo" y vuelve a intentar registrar, el
`/auth/register` devuelve `409 Conflict` (el user existe), y el script aborta con
un mensaje falso de "DIFFERENT password" que no se corresponde con la realidad.

**(3) El 429 que no se ve en ninguna parte.** Este es el caro. Corres una tanda
de specs de Playwright contra backend vivo y varios casos mueren así:

```
Error: expect(page).toHaveURL(expected) failed
Expected pattern: /\/admin\//
Received string:  "http://localhost:3000/login"
Timeout: 5000ms
```

No hay 429 en el log del test, ni en la consola del navegador, ni en la captura:
el 429 vive en la **respuesta que el test no mira**. El helper de login hace
click en «Iniciar sesión» y espera la navegación; el formulario recibe el 429, se
queda donde estaba, y lo único que el test sabe decir es que la URL no cambió.
Se depura buscando un fallo de routing o de sesión que no existe.

Y hay una pista que engaña en la dirección contraria: **cada caso agota su reloj
entero**. Medido el 2026-08-20 en la tanda de los 12 specs con backend vivo, seis
casos consumieron 30,4–30,6 s (el timeout de test por defecto) por no llegar
nunca a la URL. Un rojo que tarda exactamente el timeout dice «me quedé
esperando», no «falló la aserción».

## Causa raíz

`task_00_10` implementó rate limit sliding-window por **IP** y por **email**
sobre `/auth/login`, con contadores en Redis:

```
rl:login:email:<email>
rl:login:ip:<ip>
```

El presupuesto de producción son **5 intentos por ventana de 15 minutos**
(`login_rate_limit_count` / `login_rate_limit_window_seconds`). Y la parte que
convierte esto en aritmética inevitable, no en mala suerte:

- **los dos contadores se incrementan aunque el login tenga éxito** — a
  propósito, para que un atacante no pueda resetear el reloj cambiando de email
  (`routers/auth.py`, comentario `# Both record the hit whether or not the
credentials end up matching`);
- **todos los logins de un arnés local salen de la misma IP** (`127.0.0.1`), así
  que el bucket por IP es el que revienta primero, y revienta para _todos_ los
  usuarios a la vez.

Con 41 casos que hacen al menos un login real cada uno, el sexto login de la
tanda ya es un 429. No hay nada que «se llene antes de que expire»: **la tanda no
cabe en el presupuesto por diseño**. En producción esto es exactamente lo que
queremos.

## Fix

**En un bucle de desarrollo** (probar el flow a mano, un script que reintenta),
limpia los buckets:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  exec -T redis redis-cli DEL \
    "rl:login:email:$ADMIN_EMAIL" \
    "rl:login:ip:127.0.0.1"
```

Y distingue 429 de 401 en la lógica del script, para no decir "password mismatch"
cuando en realidad es brownout:

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

`scripts/dev/run-e2e.{ps1,sh}` borra estas claves antes de la comprobación de
admin y distingue 401 vs 429 en su lógica de register-fallback.

**En un arnés e2e con backend vivo**, borrar buckets no sirve: no hay un punto
donde hacerlo entre casos, y el arnés seguiría corriendo al borde del límite. Se
cambia el presupuesto **solo en el proceso del arnés**, y se cambian las dos
mitades de la ventana deslizante:

```bash
API_SERVER_LOGIN_RATE_LIMIT_COUNT=1000 \
API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS=60 \
  python -m uvicorn api_server.main:app --port 8001 --host 127.0.0.1
```

Que se toquen las dos no es exceso de celo: subir solo el recuento deja una
ventana de 15 minutos, así que el bucket sobrevive entre tandas y la tercera del
día empieza con la deuda de las dos anteriores. Con la ventana en 60 s el
contador se vacía solo mientras tú lees el rojo de la tanda previa.

Es lo que hace [`scripts/dev/e2e-live-harness.ps1`](../../../scripts/dev/e2e-live-harness.ps1),
la receta ejecutable del arnés: si vas a levantarlo, úsalo en vez de reconstruir
el entorno a mano.

Dos condiciones para que esto no sea debilitar una guarda:

1. **Va en el entorno del arnés, no en el default.** `login_rate_limit_count`
   sigue valiendo 5 y la ventana 15 min en `config.py`, que es lo que se
   despliega.
2. **El límite de verdad sigue teniendo quien lo ejercite**:
   `tests/integration/test_api_rate_limit.py::test_over_limit_returns_429_with_retry_after`
   comprueba que el intento `limit+1` da 429 con `Retry-After`, y lo hace
   _fijando_ el límite en su propio fixture. Ese test es el que protege la
   política; los specs de UI no la prueban ni pretenden probarla.

Lo que **no** vale: meter esperas entre specs para caber en 5 intentos cada 15
minutos (una tanda de 41 casos tardaría dos horas de reloj de pared en no probar
nada nuevo), ni marcar los specs de login como `skip`.

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

Y para confirmar que es esto lo que te está tumbando una tanda de Playwright, sin
tocar la spec: mira el contador mientras corre.

```bash
docker compose ... exec -T redis redis-cli --scan --pattern "rl:login:ip:*"
```

Si el bucket de `127.0.0.1` existe y la tanda va por su séptimo login, el
`toHaveURL` que "no llega" es este 429.

## Relacionado

- [`expect-de-cinco-segundos-no-cubre-un-backend-vivo.md`](./expect-de-cinco-segundos-no-cubre-un-backend-vivo.md)
  — el otro reloj del mismo arnés, y por qué un rojo por tiempo no informa de
  nada del producto.
- [`redis-aof-ignores-a-restored-rdb.md`](./redis-aof-ignores-a-restored-rdb.md)
  — los rate limits viven en Redis, así que un restore mal hecho los deja
  vacíos… y nadie se entera.
