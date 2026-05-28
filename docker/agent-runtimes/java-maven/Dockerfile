# syntax=docker/dockerfile:1.7
# agent-runtime-java-maven:v1 — test-runtime template for Java + Maven (Plan 06 task_06_02).

FROM maven:3.9-eclipse-temurin-21 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-java-maven" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: JDK 21 + Maven 3.9."

ENV MAVEN_OPTS="-XX:+UseContainerSupport -Djava.security.egd=file:/dev/./urandom"

# The maven base image already carries everything we need; the project's
# pom.xml is honored at task start when the worker runs `mvn ...`.

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
