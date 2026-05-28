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

ENTRYPOINT ["sleep", "infinity"]
