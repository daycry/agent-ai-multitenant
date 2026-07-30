/**
 * Cabeceras de seguridad del panel — fuente canónica (plan prod-09,
 * `task_prod09_15`; hallazgos frontend-6 y frontend-8).
 *
 * Vive en JavaScript CommonJS a propósito: `next.config.js` se carga con
 * `require()` antes de que exista cualquier transpilación, así que no puede
 * importar TypeScript. El contrato de tipos está en `security-headers.d.ts`,
 * de modo que `tsc --noEmit` y los tests siguen viéndolo tipado.
 *
 * ## Por qué hay DOS cabeceras de CSP
 *
 * Una CSP estricta mal calibrada deja el panel en blanco, y ese riesgo está
 * anotado en el propio plan (riesgo 2 de prod-09: "CSP que rompe el panel …
 * empezar en `Content-Security-Policy-Report-Only`"). Next 14 App Router
 * inyecta scripts INLINE con los datos de Flight (`self.__next_f.push(…)`) y
 * no hay forma de firmarlos con un nonce sin añadir un `middleware.ts` que
 * reescriba la cabecera en cada request. Así que:
 *
 * - `Content-Security-Policy` lleva sólo las directivas que NO pueden romper
 *   el render: `frame-ancestors`, `base-uri`, `object-src` y `form-action`.
 *   Van EN VIGOR desde el primer despliegue y cierran clickjacking, secuestro
 *   de `<base>`, plugins y `form-action` a un tercero.
 * - `Content-Security-Policy-Report-Only` lleva la política completa
 *   (`default-src 'self'` y compañía) para que el navegador reporte
 *   violaciones sin bloquear nada.
 *
 * Cuando el operador haya comprobado que no hay violaciones, promueve la
 * política completa construyendo con `CSP_ENFORCE=1`.
 *
 * ⚠ `CSP_ENFORCE` y `NEXT_PUBLIC_API_URL` se leen en tiempo de BUILD. Con
 * `output: 'standalone'` Next serializa `headers()` en `routes-manifest.json`,
 * así que ponerlas en el `environment:` del compose NO tiene ningún efecto:
 * hay que pasarlas como build-arg al construir la imagen.
 */

/**
 * Directivas que no interactúan con scripts, estilos ni conexiones y por tanto
 * son seguras de aplicar en vigor desde el minuto uno.
 */
const BASELINE_DIRECTIVES = [
  "base-uri 'self'",
  "object-src 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
];

/**
 * Orígenes permitidos en `connect-src`.
 *
 * El panel habla con la api-server en `NEXT_PUBLIC_API_URL`, que puede ser
 * absoluto (dev: `http://127.0.0.1:8001`) o relativo (producción tras Caddy:
 * `/api`, ADR 0061). En el caso relativo basta `'self'`; en el absoluto hay que
 * listar el origen y su equivalente WebSocket, porque `lib/ws.ts` deriva
 * `ws(s)://` de esa misma base.
 *
 * @param {string | undefined} apiUrl
 * @param {boolean} isDev
 * @returns {string[]}
 */
function connectSources(apiUrl, isDev) {
  const sources = ["'self'"];
  const base = (apiUrl ?? "").trim();
  const match = /^(https?):\/\/[^/?#]+/i.exec(base);

  if (match) {
    const origin = match[0];
    const scheme = match[1].toLowerCase();
    sources.push(origin);
    // OJO: no vale `replace(/^http/, "wss")` — sobre "https://…" produce
    // "wsss://…". Se recorta el esquema por longitud.
    sources.push((scheme === "https" ? "wss" : "ws") + origin.slice(scheme.length));
  } else if (isDev) {
    // Sin variable, `lib/api.ts` y `lib/ws.ts` caen a :8001. En producción ese
    // fallback ya no existe: `assertPublicApiUrl` revienta el build antes.
    sources.push("http://localhost:8001", "ws://localhost:8001");
  }

  if (isDev) {
    // El HMR de Next abre un WebSocket propio contra el dev-server.
    sources.push("ws:", "wss:");
  }

  return sources;
}

/**
 * Política completa: la que se querría tener en vigor.
 *
 * `'unsafe-inline'` en `script-src` es inevitable sin nonce (ver cabecera del
 * fichero). `style-src` lo necesita por Tailwind y por los SVG que genera
 * mermaid. `media-src blob:` es real: el TTS del asistente reproduce un `Blob`
 * de audio (`components/voice/voice-call-shell.tsx`).
 *
 * @param {{ isDev: boolean, apiUrl?: string | undefined }} options
 * @returns {string}
 */
function fullPolicy(options) {
  const scriptSources = ["'self'", "'unsafe-inline'"];
  if (options.isDev) {
    // React Refresh compila con `eval` bajo `next dev`.
    scriptSources.push("'unsafe-eval'");
  }

  return [
    "default-src 'self'",
    `script-src ${scriptSources.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self' blob: data:",
    `connect-src ${connectSources(options.apiUrl, options.isDev).join(" ")}`,
    "frame-src 'none'",
    ...BASELINE_DIRECTIVES,
  ].join("; ");
}

/**
 * Las cabeceras que `next.config.js` sirve en todas las rutas.
 *
 * @param {{ nodeEnv?: string | undefined, apiUrl?: string | undefined, enforceCsp?: boolean | undefined }} [options]
 * @returns {{ key: string, value: string }[]}
 */
function buildSecurityHeaders(options) {
  const { nodeEnv, apiUrl, enforceCsp = false } = options ?? {};
  const isDev = nodeEnv !== "production";
  const full = fullPolicy({ isDev, apiUrl });

  const headers = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    // Redundante con `frame-ancestors` en navegadores modernos, pero sigue
    // siendo la única defensa en los que no implementan CSP nivel 2.
    { key: "X-Frame-Options", value: "DENY" },
    {
      key: "Content-Security-Policy",
      value: enforceCsp ? full : BASELINE_DIRECTIVES.join("; "),
    },
  ];

  if (!enforceCsp) {
    headers.push({ key: "Content-Security-Policy-Report-Only", value: full });
  }

  return headers;
}

/**
 * Revienta el build de producción si `NEXT_PUBLIC_API_URL` no está puesta.
 *
 * Sin esto (hallazgo frontend-8) el panel se construye en silencio con el
 * fallback `http://localhost:8001` de `lib/api.ts` y `lib/ws.ts`, y cada
 * navegador que lo abra intenta hablar con SU PROPIO localhost: la aplicación
 * parece desplegada y no funciona para nadie.
 *
 * Se invoca desde `headers()` a propósito, no al cargar el módulo:
 * `next build` llama a `headers()` (vía `loadCustomRoutes`) pero `next lint`
 * sólo lee el objeto de configuración, así que el lint no se ve afectado.
 * La cadena vacía cuenta como ausencia porque el `ARG NEXT_PUBLIC_API_URL=""`
 * del Dockerfile deja la variable definida y vacía.
 *
 * @param {{ nodeEnv?: string | undefined, apiUrl?: string | undefined }} [options]
 * @returns {void}
 */
function assertPublicApiUrl(options) {
  const { nodeEnv, apiUrl } = options ?? {};
  if (nodeEnv !== "production") return;
  if (typeof apiUrl === "string" && apiUrl.trim() !== "") return;

  throw new Error(
    [
      "NEXT_PUBLIC_API_URL no está definida y este es un build de producción.",
      "",
      "Next hornea las NEXT_PUBLIC_* en el bundle: sin ella, lib/api.ts y",
      "lib/ws.ts caen al fallback http://localhost:8001 y el panel apunta al",
      "localhost de cada usuario (hallazgo frontend-8 de prod-09).",
      "",
      "  docker build --build-arg NEXT_PUBLIC_API_URL=/api …    # tras Caddy",
      "  NEXT_PUBLIC_API_URL=/api npm run build                 # build local",
      "",
      "Para un build de e2e con los mocks apuntando al default histórico:",
      "  NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build",
    ].join("\n"),
  );
}

module.exports = { assertPublicApiUrl, buildSecurityHeaders };
