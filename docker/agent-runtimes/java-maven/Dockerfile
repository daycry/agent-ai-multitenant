# syntax=docker/dockerfile:1.7
# agent-runtime-java-maven:v1 — test-runtime template for Java + Maven (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM maven:3-eclipse-temurin-26@sha256:166ca19b6b5fe1e924ab2d66b64ba9854c739f16210b94bbe0074b036c5c7992 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-java-maven" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: JDK 21 + Maven 3.9."

ENV MAVEN_OPTS="-XX:+UseContainerSupport -Djava.security.egd=file:/dev/./urandom"

# The maven base image already carries everything we need; the project's
# pom.xml is honored at task start when the worker runs `mvn ...`.

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
