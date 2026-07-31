# syntax=docker/dockerfile:1.7
# browser-runtime:v1 — navegador real sandboxeado para el córtex (ADR 0080).
#
# El córtex NO ejecuta un navegador: el worker lanza ESTE contenedor efímero por
# sesión de navegación (previa aprobación humana), con cap-drop ALL, root de
# solo lectura, sin socket Docker, no-root y con la red interna del agente —
# cuya única salida es el egress-proxy con allowlist.
#
# Base oficial de Playwright: trae Chromium y sus dependencias del sistema ya
# instaladas (instalarlas por sesión añadiría minutos a cada navegación).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy@sha256:6bbd515848db4042068571135979b6ee6d330a794b28e037e3a6ccd7f2abfa26 AS runtime

LABEL org.opencontainers.image.title="browser-runtime" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Sesión de navegador (Playwright+Chromium) sandboxeada — ADR 0080."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/agent

WORKDIR /app
COPY docker/agent-runtimes/browser-runtime/browser_runtime /app/browser_runtime

# uid 1000 (el mismo que fuerza el worker) con un HOME escribible: Chromium
# necesita escribir su perfil, y el root del contenedor va read-only.
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent /app
USER 1000:1000

# Sin argumentos: la sesión viaja en BROWSE_SESSION_SPEC (env) y el resultado
# sale por stdout como una línea JSON que el worker cosecha.
ENTRYPOINT ["python", "-m", "browser_runtime"]
