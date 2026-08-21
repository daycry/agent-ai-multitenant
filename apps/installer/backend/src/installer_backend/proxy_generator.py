"""Reverse-proxy (Caddy) config generator — Plan prod-01 task_15 / deploy-7.

Pure functions that render the ``Caddyfile`` the installer materialises next to
the generated ``docker-compose.yml``. The single Caddy service is the only
host-published surface (ADR 0061): it terminates TLS, adds HSTS, and routes a
single origin ``https://{domain}`` to the two internal apps.

Routing (the order is load-bearing — Caddy evaluates ``handle`` blocks top-down,
first match wins):

  1. ``handle /api/v1/*``   → ``api-server`` INTACT (the public versioned API is
     the only backend route that already starts with ``/api``; stripping it
     would yield ``/v1/*`` which the backend does not serve).
  2. ``handle_path /api/*`` → ``api-server`` with ``/api`` STRIPPED (the UI API +
     SSO callback + SCIM + incoming webhooks all live here, reached as bare
     backend paths once the prefix is removed).
  3. ``handle``             → ``admin-panel`` (the Next.js SPA catch-all).

Los dos upstreams de la api-server llevan **readiness activa** (``health_uri
/readyz``, ``task_audit14_08``): Caddy deja de enrutarle tráfico mientras el
proceso no puede atenderlo y lo repone solo cuando vuelve a 200. Ver
:data:`_API_UPSTREAM` para por qué el consumidor es el proxy y NO el
``healthcheck`` del contenedor.

No host access, no secrets, no ``${ENV}`` references: the domain and TLS choice
are baked into the text. The e2e (task_20) is what runs Caddy for real; these
helpers are unit-tested on the rendered string only.
"""

from __future__ import annotations

from .config import InstallerConfig

#: Where the corporate cert/key are mounted in the container when
#: ``tls_mode == "provided"`` (the host dir is ``{data_root}/caddy/tls``).
_PROVIDED_CERT = "/etc/caddy/tls/server.crt"
_PROVIDED_KEY = "/etc/caddy/tls/server.key"

#: Cómo se enruta a la api-server: SIEMPRE con readiness activa
#: (``task_audit14_08``, hallazgo AUD14-06).
#:
#: Caddy es el consumidor correcto de ``/readyz`` porque puede dejar de mandarle
#: tráfico a un backend que aún no puede atenderlo (o que se quedó sin
#: PostgreSQL/Redis) **sin tocar el ciclo de vida del contenedor**: cuando
#: ``/readyz`` vuelve a 200, el siguiente check lo repone solo. El
#: ``healthcheck`` de Docker NO sirve para esto — es liveness, sólo hay uno por
#: contenedor y el watchdog reinicia lo que sale ``unhealthy``, así que apuntarlo
#: a readiness convertiría «la BD se cayó» en «la api-server se reinicia en
#: bucle». Ver el módulo ``api_server.routers.health``.
#:
#: Los dos handlers llevan su propio checker. El pool de upstreams de Caddy es
#: global por dirección, así que con uno bastaría para marcar el host caído;
#: declararlo en ambos cuesta 12 peticiones/minuto y no depende de ese detalle
#: interno.
_API_UPSTREAM = "\n".join(
    (
        "\t\treverse_proxy api-server:8000 {",
        "\t\t\thealth_uri /readyz",
        "\t\t\thealth_interval 10s",
        "\t\t\thealth_timeout 5s",
        "\t\t\thealth_status 2xx",
        "\t\t}",
    )
)


def _global_block(cfg: InstallerConfig) -> str:
    """The Caddy global options block. ``admin off`` keeps the admin API off the
    wire; the ACME email/CA are emitted ONLY in ``acme`` mode."""

    lines = ["\tadmin off"]
    if cfg.system.tls_mode == "acme":
        # tls_acme_email is required by the SystemConfig validator in acme mode.
        lines.append(f"\temail {cfg.system.tls_acme_email}")
        if cfg.system.tls_acme_ca:
            lines.append(f"\tacme_ca {cfg.system.tls_acme_ca}")
    body = "\n".join(lines)
    return "{\n" + body + "\n}"


def _tls_directive(cfg: InstallerConfig) -> str:
    """The site's TLS directive, one per mode.

    * ``internal`` → ``tls internal`` (Caddy's local CA, self-signed).
    * ``provided`` → ``tls <crt> <key>`` (the bind-mounted corporate cert).
    * ``acme``     → no directive (Caddy does ACME by default for a public host;
      the email lives in the global block).
    """

    mode = cfg.system.tls_mode
    if mode == "internal":
        return "\ttls internal"
    if mode == "provided":
        return f"\ttls {_PROVIDED_CERT} {_PROVIDED_KEY}"
    return "\t# tls: gestionado por ACME (ver el bloque global: email/acme_ca)"


def generate_caddyfile(cfg: InstallerConfig) -> str:
    """Render the Caddyfile for the configured domain + TLS mode (ADR 0061)."""

    domain = cfg.system.domain
    return f"""\
# Caddyfile — GENERADO por el instalador (installer_backend.proxy_generator).
# NO editar a mano: se regenera en cada install/reinstall. Origen único:
# el admin-panel en / y el api-server bajo /api/* (ADR 0061, prod-01 task_15).

{_global_block(cfg)}

# Puerto 80: endpoint de salud PLANO (sin redirección a https, para que el
# healthcheck del contenedor no caiga por el 308 + cert autofirmado) y
# redirección del resto del tráfico a https.
:80 {{
\thandle /healthz {{
\t\trespond "OK" 200
\t}}
\thandle {{
\t\tredir https://{{host}}{{uri}} permanent
\t}}
}}

{domain} {{
{_tls_directive(cfg)}

\t# Cabeceras de seguridad en todas las respuestas.
\theader {{
\t\tStrict-Transport-Security "max-age=31536000; includeSubDomains"
\t\tX-Content-Type-Options "nosniff"
\t\tX-Frame-Options "DENY"
\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\t-Server
\t}}

\tencode zstd gzip

\t# 1) API pública versionada — SIN strip (ya nace en /api). DEBE ir antes del
\t#    handle_path genérico o /api/v1/* se rompería a /v1/*. Matcher NOMBRADO:
\t#    `handle` solo acepta UN matcher — dos paths inline (`handle /api/v1
\t#    /api/v1/*`) son un ERROR de parseo de Caddyfile (cazado con `caddy
\t#    validate` en la verificación del instalador, 2026-07-18: el proxy no
\t#    habría arrancado nunca en producción). El matcher cubre la ruta desnuda
\t#    `/api/v1` y `/api/v1/*` para que ninguna caiga al strip genérico.
\t@apiv1 path /api/v1 /api/v1/*
\thandle @apiv1 {{
{_API_UPSTREAM}
\t}}

\t# 2) Resto del backend bajo /api/* — Caddy RETIRA el prefijo /api. Aquí entran
\t#    la API interactiva del SPA, el callback SSO (/api/auth/sso/oidc/callback),
\t#    SCIM (/api/scim/v2/*) y los webhooks entrantes (/api/webhooks/incoming/*).
\thandle_path /api/* {{
{_API_UPSTREAM}
\t}}

\t# 3) Todo lo demás → el SPA admin-panel (incluye /admin/*, /login, _next/*).
\thandle {{
\t\treverse_proxy admin-panel:3000
\t}}
}}
"""
