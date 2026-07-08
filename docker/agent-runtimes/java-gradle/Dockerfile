# syntax=docker/dockerfile:1.7
# agent-runtime-java-gradle:v1 — test-runtime template for Java + Gradle (Plan 06 task_06_02).

FROM gradle:8-jdk21 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-java-gradle" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: JDK 21 + Gradle 8."

ENV GRADLE_OPTS="-Dorg.gradle.daemon=false -Xmx1g"

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
