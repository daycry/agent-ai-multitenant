# syntax=docker/dockerfile:1.7
# agent-runtime-java-gradle:v1 — test-runtime template for Java + Gradle (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM gradle:8-jdk21@sha256:dae150d9066fc04a791ec7f0adc1a0eb4e867f11d76d03063ee0a60e5da56149 AS runtime

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
