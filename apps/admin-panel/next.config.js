/**
 * @type {import('next').NextConfig}
 *
 * `output: 'standalone'` keeps the prod image small — Next emits a
 * self-contained server bundle plus a minimal node_modules tree.
 * The Dockerfile in phase 12 will COPY only that.
 *
 * `headers()` es la fuente canónica de las cabeceras de seguridad del panel
 * (plan prod-09 `task_prod09_15`, hallazgos frontend-6 y frontend-8). La lógica
 * vive en `lib/security-headers.js` para poder testearla; aquí sólo se cablea.
 * Si prod-01 acaba sirviendo el panel tras un reverse proxy que añada las
 * mismas cabeceras, la que manda es esta (y allí habrá que quitarlas para no
 * duplicarlas).
 */
const { assertPublicApiUrl, buildSecurityHeaders } = require("./lib/security-headers");

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    // `next build` invoca headers() (loadCustomRoutes) antes de compilar nada,
    // así que este assert falla el build; `next lint` sólo lee el objeto de
    // configuración y no lo dispara.
    assertPublicApiUrl({
      nodeEnv: process.env.NODE_ENV,
      apiUrl: process.env.NEXT_PUBLIC_API_URL,
    });

    return [
      {
        source: "/(.*)",
        headers: buildSecurityHeaders({
          nodeEnv: process.env.NODE_ENV,
          apiUrl: process.env.NEXT_PUBLIC_API_URL,
          enforceCsp: process.env.CSP_ENFORCE === "1",
        }),
      },
    ];
  },
};

module.exports = nextConfig;
