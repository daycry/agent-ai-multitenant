---
title: "Una e2e Playwright falla en «Cargando…» porque next dev estaba compilando la ruta"
area: frontend
encountered: 2026-08-20
stack: Playwright 1.60, Next.js 15.5 (App Router), next dev
docs_language: es
---

# Una e2e falla en «Cargando…», y la captura de fallo muestra la página perfecta

## Síntoma

Una spec que estaba en verde empieza a fallar en local sin que nadie haya tocado
ni la spec ni la pantalla:

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('cortex-voice-toggle')
Expected: visible
Timeout: 5000ms
Error: element(s) not found
```

Y en el `error-context.md` que Playwright deja al lado, el snapshot ARIA de la
página es este —el armazón montado y el contenido sin llegar:

```yaml
- main:
    - paragraph: Cargando…
```

Lo que lo hace desconcertante son tres cosas:

1. **La captura de pantalla del fallo muestra la página ENTERA y correcta**, con
   el elemento que el test dice no encontrar perfectamente visible. Es que la
   captura se toma al agotarse el test y el snapshot ARIA en el momento de la
   aserción: entre los dos, la página acabó de cargar.
2. **Falla un test distinto en cada corrida.** En seis pasadas del mismo fichero
   de dos tests, el reparto de fallos fue: el primero, el primero, el segundo,
   los dos, el segundo, los dos. **Cada uno de los dos pasó al menos una vez.**
   Ésa es la firma de una carrera de tiempos, no la de un defecto.
3. **No hay ni un error en consola** y las peticiones mockeadas con `page.route`
   respondieron 200. El trace lo confirma.

## Causa raíz

`next dev` **compila cada ruta la primera vez que se pide**, servidor y cliente.
En una máquina cargada eso no son milisegundos. Medido el 2026-08-20 en el repo,
con cinco agentes trabajando en paralelo:

```bash
# primer golpe a la ruta: compila
curl -o /dev/null -w "%{http_code} %{time_total}s\n" \
  -H "Cookie: agentic_session=e2e-fake-token; agentic_csrf=e2e-csrf-token" \
  http://localhost:3000/admin/cortex
200 27.791879s      # <-- veintisiete segundos

# segundo golpe: ya compilada
200 2.566293s
```

**27,8 s de compilación contra un presupuesto de 30 s por test**, más la
hidratación de React, más el primer `fetch` de TanStack Query — que no arranca
hasta que el cliente hidrata. La página se queda en su rama de carga
(`isLoading`), la aserción gasta sus 5 s de paciencia contra ese estado, y el
test muere hablando de un testid cuando el problema era el reloj.

Precalentar con `curl` **no basta**: eso compila el render de servidor, pero los
chunks de cliente se compilan en la primera navegación de un navegador de
verdad. Por eso una corrida con el servidor «caliente» a base de `curl` puede
fallar igual.

## Fix

**No toques la spec ni los timeouts.** El arnés ya tiene la respuesta y está
escrita en `apps/admin-panel/playwright.config.ts`: CI **no usa `next dev`**.
Compila el bundle de producción y lo sirve con `next start`, que no compila nada
bajo demanda:

```bash
cd apps/admin-panel
NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build
E2E_WEBSERVER_CMD="npm run start" npx playwright test e2e/<tu-spec>.spec.ts
```

Eso es exactamente lo que hace `.github/workflows/ci.yml` («next build (for
e2e)» + `E2E_WEBSERVER_CMD: npm run start`), y con esa receta la spec da un
veredicto de verdad en vez de un sorteo.

Si sólo quieres una pasada rápida en local y te vale con `next dev`, **calienta
con un navegador, no con curl**: corre la spec dos veces seguidas contra el mismo
servidor (`npm run dev` en otra terminal; `reuseExistingServer` lo reutiliza) y
mira el resultado de la segunda. En la medición de arriba la primera pasada tardó
2 min 24 s y la segunda 41 s.

## Cómo distinguirlo de un defecto de verdad

Antes de tocar una línea de producto, abre las dos evidencias que Playwright ya
te dejó en `test-results/<test>/`:

- **`test-failed-1.png`** — si la página sale entera y con el elemento a la
  vista, el producto está bien y lo que falló fue el reloj.
- **`error-context.md`** — si el snapshot ARIA dice «Cargando…» / spinner, la
  aserción corrió contra el estado de carga.

Y si quieres el número: pide la ruta con `curl` midiendo `%{time_total}` como
arriba. Si el primer golpe se acerca al presupuesto del test, ya tienes la causa.

## Por qué no se «arregla» subiendo el timeout

Porque el `expect.timeout` y el timeout de test son compartidos con CI, y CI
corre con `--timeout=15000` sobre un servidor precompilado donde el elemento
aparece de inmediato. Subirlos para acomodar una máquina saturada haría que CI
tardase más en dar un rojo legítimo, a cambio de nada: en CI el problema no
existe. La condición anómala es la carga local, no la configuración.

## Relacionado

- [`playwright-route-glob-intercepts-navigation.md`](./playwright-route-glob-intercepts-navigation.md) — el otro modo de fallo de este arnés: un `page.route` demasiado ancho que se come la navegación.
- [`nextjs-stale-next-cache-after-branch-switch.md`](./nextjs-stale-next-cache-after-branch-switch.md) — cuando el problema sí es la caché de `.next`.
