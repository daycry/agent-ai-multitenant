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
#
# 2026-08-14: se sube de `v1.48.0-jammy` a `v1.62.0-noble`. La base anterior era
# Ubuntu 22.04 y acumulaba 16 CVE HIGH CON fix publicado: 8 del sistema (git
# —CVE-2025-48384—, gpg, openssl/libssl3 y libtiff5) y 8 del Python que trae la
# imagen (setuptools 75.2.0 → CVE-2025-47273, wheel, jaraco.context). Ninguna se
# arreglaba refrescando el digest de la 1.48: Microsoft dejó de reconstruir esa
# línea, así que el pin apuntaba a un snapshot que ya no recibe parches.
#
# Que sea `noble` (24.04) y no `jammy` es el fondo del asunto: es el salto de
# release lo que trae los paquetes parcheados, no un digest más nuevo de la
# misma. Y el salto de Playwright es seguro aquí porque `browser_runtime` solo
# usa API estable —`sync_playwright`, `chromium.launch`, `new_context`,
# `goto`/`click`/`fill`/`wait_for`—, que no ha cambiado entre la 1.48 y la 1.62.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d AS runtime

LABEL org.opencontainers.image.title="browser-runtime" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Sesión de navegador (Playwright+Chromium) sandboxeada — ADR 0080."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/agent

# El paquete Python `playwright` NO viene en la imagen base — la oficial trae
# los BINARIOS de los navegadores en /ms-playwright y el toolchain de sistema,
# pero no la librería. Este Dockerfile nunca lo instaló, así que
# `PlaywrightDriver.__init__` (import perezoso de `playwright.sync_api`) moría
# con ModuleNotFoundError en cuanto se abría una sesión de verdad. No se veía
# porque el import es perezoso: el módulo carga, los tests unitarios pasan, y
# sólo revienta al navegar. Comprobado que faltaba también en la base anterior
# (v1.48.0-jammy), o sea que es un defecto de origen, no del salto de versión.
#
# La versión va CLAVADA a la del tag de la base: el protocolo entre la librería
# y el build de Chromium empaquetado es solidario, así que las dos suben juntas
# o no sube ninguna.
#
# Y después se retira el instalador: la imagen final corre `python -m
# browser_runtime` y no instala nada en ejecución. pip arrastra `msgpack` 1.1.2
# VENDORIZADO (GHSA-6v7p-g79w-8964) y virtualenv sus wheels de siembra con
# `setuptools` vulnerable; ninguno se puede parchear por separado porque van
# empotrados. Quitarlos no silencia el hallazgo: saca el código vulnerable de la
# imagen, y de paso deja el sandbox del Principio Rector 2 sin instalador de
# paquetes, que es justo lo que uno no quiere a mano en un contenedor que abre
# páginas no confiables.
RUN set -eux; \
    pip install --no-cache-dir playwright==1.62.0; \
    python -m pip uninstall -y virtualenv; \
    rm -rf /usr/local/lib/python3.12/dist-packages/pip \
           /usr/local/lib/python3.12/dist-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
           /root/.cache; \
    python -c "from playwright.sync_api import sync_playwright; print('playwright OK sin pip')"

WORKDIR /app
COPY docker/agent-runtimes/browser-runtime/browser_runtime /app/browser_runtime

# uid 1000 (el mismo que fuerza el worker) con un HOME escribible: Chromium
# necesita escribir su perfil, y el root del contenedor va read-only.
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent /app
USER 1000:1000

# Sin argumentos: la sesión viaja en BROWSE_SESSION_SPEC (env) y el resultado
# sale por stdout como una línea JSON que el worker cosecha.
ENTRYPOINT ["python", "-m", "browser_runtime"]
