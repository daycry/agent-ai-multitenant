---
title: "Los 5 s de `expect` no cubren un backend vivo: 21 rojos de 41 que no dicen nada del producto"
area: frontend / tests e2e
encountered: 2026-08-20
stack: Playwright 1.60, Next.js 15.5 (next start), api-server FastAPI, arnés con backend vivo
docs_language: es
---

# 21 rojos de 41, ninguno del producto: el presupuesto de `expect` contra backend real

## Síntoma

Levantas el arnés de los 12 specs de `apps/admin-panel/e2e` que **no mockean**
nada (los que CI no corre) y sale una alfombra de rojos como este:

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('services-grid')
Expected: visible
Timeout: 5000ms
```

Lo que delata que no es el producto:

1. **Casi todos fallan por el mismo reloj.** Medido el 2026-08-20: **21 de los 41
   casos** en rojo con el presupuesto por defecto; con el presupuesto en 25 s,
   **11**. Diez rojos desaparecieron sin tocar una línea de código de producto.
2. **Ninguno menciona nada del producto.** Dicen «no encontré el testid», que es
   lo único que un `expect` agotado sabe decir.
3. **La captura del fallo enseña la pantalla bien.** Igual que en el gotcha de
   `next dev`, la captura se toma al agotarse el test y el elemento ya está ahí.

## Causa raíz

No es lentitud del panel: es que **la aserción está detrás de una llamada cuyo
techo es de 10 segundos**.

`services-grid` solo se renderiza cuando llega `health.data`
(`apps/admin-panel/app/admin/dashboard/page.tsx`), es decir cuando contesta
`GET /admin/system-health`. Y ese endpoint
(`apps/api-server/src/api_server/routers/admin.py`) sondea **ocho** servicios:

```
postgres, redis, vault, minio, clamav, docling-serve, ollama, egress-proxy
```

Dos detalles que hay que leer juntos, porque uno sin el otro lleva a la
conclusión equivocada:

- **Las sondas van en paralelo** (`asyncio.gather`), así que **no** se suman ocho
  latencias. Quien lea «una petición por servicio» y multiplique, se pasa.
- **Cada sonda tiene un techo de 10 s** (`_PROBE_TIMEOUT_S = 10.0`, subido desde
  2 s y luego 5 s porque el probe de postgres se cancelaba en el arranque en frío
  de la suite). La respuesta espera a **la más lenta de las ocho**, así que el
  peor caso del endpoint es ~10 s. Y en un arnés local hay sondas condenadas a
  agotar el techo: la api-server corre en el host, varios de esos servicios solo
  existen dentro de la red del compose, y en Windows cada conexión a `localhost`
  paga además el intento IPv6 antes de caer a IPv4
  ([gotcha](./localhost-ipv6-primero-cuesta-dos-segundos.md)).

Con eso, **5 s no es un presupuesto ajustado: es aritméticamente insuficiente**.
Los números medidos del mismo caso (`admin-login.spec.ts:18 › login + dashboard
happy path`, que es el que asserta `services-grid`):

| pasada                          | duración del caso |
| ------------------------------- | ----------------- |
| primera, en frío                | **15,1 s**        |
| pasadas siguientes, en caliente | 7,0 / 7,2 / 7,5 s |

Y no es solo el dashboard: en una tanda **sana** (40 de 41 en verde) hay casos de
`lang-switcher` a 10,0 s y de `agents-catalog` a 9,2 s. Con 5 s de paciencia por
aserción, el rojo es el reloj, no el producto.

## Fix

La variable ya está puesta en `apps/admin-panel/playwright.config.ts`, **con el
default intacto en 5 s**:

```ts
expect: {
  timeout: Number(process.env.E2E_EXPECT_TIMEOUT ?? 5_000),
},
```

Se sube **solo al correr los specs con backend vivo**:

```bash
cd apps/admin-panel
NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build
E2E_EXPECT_TIMEOUT=25000 E2E_WEBSERVER_CMD="npm run start" \
  npx playwright test e2e/admin-login.spec.ts e2e/agents-catalog.spec.ts …
```

### Y antes de subirlo, quita la espera que sí es artificial

Buena parte del techo de 10 s se lo comen sondas que **no tienen a nadie
enfrente**. El api-server del arnés corre en el host, no dentro de la red del
compose, así que cada URL sondeada tiene que apuntar al puerto **publicado** en
loopback: si hereda un nombre de servicio de docker (`vault`, `minio`, `ollama`)
o un puerto que este stack no publica, esa sonda está condenada a agotar su techo
y la respuesta espera a la más lenta. Fijarlas explícitamente recorta la espera
de verdad, y entonces el presupuesto que hay que subir es mucho menor:

```bash
API_SERVER_VAULT_URL=http://127.0.0.1:8200 \
API_SERVER_MINIO_URL=http://127.0.0.1:9000 \
  python -m uvicorn api_server.main:app --port 8001 --host 127.0.0.1
```

(los puertos salen de `docker/.env`, que está en `.gitignore` — léelos de ahí,
no los copies)

[`scripts/dev/e2e-live-harness.ps1`](../../../scripts/dev/e2e-live-harness.ps1)
—la receta ejecutable del arnés— ya hace las dos cosas: redirige las URLs de los
servicios sondeados y sugiere `E2E_EXPECT_TIMEOUT=25000` al final. Úsalo en vez de
montar el entorno a mano.

## Por qué aquí sí se sube el reloj y en el gotcha de `next dev` no

Parecen el mismo caso y son opuestos, así que conviene tener el criterio escrito:

|                       | [`playwright-next-dev-…`](./playwright-next-dev-compila-la-ruta-y-agota-el-test.md) | esta nota                                            |
| --------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Qué se está esperando | que `next dev` **compile** la ruta                                                  | que el backend **conteste**                          |
| ¿Existe en CI?        | No: CI sirve un build de producción                                                 | Sí existiría: el fan-out es del producto             |
| ¿Es la espera real?   | No, es un artefacto del modo dev                                                    | Sí, es comportamiento del endpoint                   |
| Conclusión            | arregla el arnés (`next build` + `next start`)                                      | sube el presupuesto **de esos specs**, no el default |

La regla, en una línea: **si la espera es un artefacto del entorno, arregla el
entorno; si la espera es del producto, el presupuesto tiene que caber en ella.**

Y por eso el default se queda en 5 s: en el subconjunto mockeado que corre CI no
hay backend al que esperar, así que ahí 5 s **es señal** — un click que tarda más
ya ha fallado. Subirlo para todos habría cambiado una guarda que funciona por la
comodidad de otra.

## Cómo verificar el fix

Mide el endpoint antes de acusar al frontend. Con una cookie de sesión de System
Admin:

```bash
curl -o /dev/null -w "%{http_code} %{time_total}s\n" \
  -H "Cookie: agentic_session=<token>" \
  http://localhost:8001/admin/system-health
```

Si `time_total` se acerca a 10 s, ya tienes la causa y sabes qué presupuesto hace
falta. Para saber **cuál** de las ocho sondas se lo come, la respuesta lo dice:
el servicio con `"detail": "timeout"` es el que agotó el techo (y
`system_health.probe_failed` lo deja en el log del servidor).

La comprobación de que la variable no ha contaminado el subset de CI:

```bash
grep -n "E2E_EXPECT_TIMEOUT" apps/admin-panel/playwright.config.ts   # ?? 5_000
grep -rn "E2E_EXPECT_TIMEOUT" .github/workflows/ci.yml               # sin resultados
```

## Nota al margen encontrada por el camino

El comentario de cabecera del bloque en `admin.py` dice todavía «Each probe has a
2 s ceiling» mientras la constante vale 10.0. Quien lea el comentario y no la
constante concluirá que el endpoint no puede tardar más de 2 s, que es justo la
inferencia que hace perder la tarde.

## Relacionado

- [`playwright-next-dev-compila-la-ruta-y-agota-el-test.md`](./playwright-next-dev-compila-la-ruta-y-agota-el-test.md)
  — el otro rojo por reloj de este arnés, con el criterio opuesto.
- [`auth-rate-limit-dev-loop.md`](./auth-rate-limit-dev-loop.md)
  — el otro reloj: un `toHaveURL` que nunca llega porque el login recibió un 429.
- [`localhost-ipv6-primero-cuesta-dos-segundos.md`](./localhost-ipv6-primero-cuesta-dos-segundos.md)
  — por qué cada conexión a `localhost` en Windows arranca con dos segundos de
  penalización.
