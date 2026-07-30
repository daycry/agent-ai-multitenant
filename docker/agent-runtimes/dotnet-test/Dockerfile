# syntax=docker/dockerfile:1.7
# agent-runtime-dotnet-test:v1 — test-runtime template for .NET (Plan 06 task_06_02).

FROM mcr.microsoft.com/dotnet/sdk:8.0 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-dotnet-test" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: .NET SDK 8.0 + dotnet test."

ENV DOTNET_NOLOGO=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    NUGET_XMLDOC_MODE=skip

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
