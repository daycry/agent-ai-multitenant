# syntax=docker/dockerfile:1.7
# agent-runtime-java-gradle:v1 — test-runtime template for Java + Gradle (Plan 06 task_06_02).

FROM gradle:8-jdk21 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-java-gradle" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: JDK 21 + Gradle 8."

ENV GRADLE_OPTS="-Dorg.gradle.daemon=false -Xmx1g"

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
