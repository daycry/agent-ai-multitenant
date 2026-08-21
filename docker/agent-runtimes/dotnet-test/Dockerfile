# syntax=docker/dockerfile:1.7
# agent-runtime-dotnet-test:v1 — test-runtime template for .NET (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `sdk:8.0` a `sdk:10.0` (la LTS vigente). Aquí el
# refresco del digest tampoco bastaba, y el motivo merece quedar escrito: se
# escaneó `sdk:8.0` con su digest más reciente (SDK 8.0.424) y seguía dando 11
# HIGH. Seis vienen de CVE-2025-26646 en Microsoft.Build.Tasks.Core 17.11.48, y
# la lista de versiones corregidas que publica Microsoft —17.8.29, 17.10.29,
# 17.12.36, 17.13.26, 17.14.8— NO incluye ninguna 17.11.x: la banda 8.0.4xx del
# SDK quedó anclada a un MSBuild que no recibirá el parche. Esperar a un digest
# nuevo de la 8.0 era esperar a algo que no va a llegar.
#
# Un SDK 10 compila y ejecuta proyectos que tengan como target net8.0 (la
# compatibilidad va hacia atrás), así que los proyectos de los tenants no
# necesitan cambiar de TFM.
FROM mcr.microsoft.com/dotnet/sdk:10.0@sha256:e1ffd2a92ae84c1291bc1b6887501f8af98e6331e7af6d4c8d37168c5e87a64c AS runtime

LABEL org.opencontainers.image.title="agent-runtime-dotnet-test" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: .NET SDK 10.0 + dotnet test."

ENV DOTNET_NOLOGO=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    NUGET_XMLDOC_MODE=skip

# La imagen del SDK trae PowerShell empaquetado, y su copia de
# System.Security.Cryptography.Xml (10.0.6) va por detrás del fix (10.0.10):
# son los 5 HIGH restantes tras subir el SDK. Aquí no se ejecuta nada de
# PowerShell —el runtime existe para `dotnet test`—, así que la respuesta
# correcta no es tolerar la CVE ni suprimirla, es no llevar el código: menos
# superficie dentro del contenedor donde corre código NO confiable (Principio
# Rector 2).
RUN rm -rf /usr/share/powershell /usr/bin/pwsh \
 && dotnet --version

WORKDIR /workspace

# prod-12 task_prod12_img_01 (sandbox-6): el worker fuerza uid 1000 en cada
# run; la imagen le da un HOME escribible para que los dep-caches (pip/
# composer/npm/...) funcionen, y arranca no-root tambien por defecto
# (defensa en profundidad, como el agent-runtime). USER numerico: no
# necesita entrada en /etc/passwd (uniforme en debian y alpine).
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
