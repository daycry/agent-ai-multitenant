# syntax=docker/dockerfile:1.7
# agent-runtime-java-gradle:v1 — test-runtime template for Java + Gradle (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `gradle:8-jdk21` a `gradle:9-jdk21`. Aquí refrescar el
# digest NO servía y conviene dejarlo escrito: se escaneó `gradle:8-jdk21` con
# su digest más reciente y seguía dando 4 HIGH, porque no vienen del sistema
# operativo sino de los JAR que Gradle empaqueta en `/opt/gradle/lib`
# (jackson-core y jackson-databind 2.18.6 → GHSA-r7wm-3cxj-wff9, CVE-2026-54512
# y CVE-2026-54513; plexus-utils 3.5.1 → CVE-2025-67030). La rama 8 no los ha
# subido; la 9 sale limpia. El sistema operativo de ambas (Ubuntu 24.04) ya
# estaba sin hallazgos.
#
# Qué se acepta al subir de major: los proyectos que traen su propio wrapper
# (`./gradlew`) no se enteran —el wrapper descarga su Gradle—, pero un proyecto
# que invoque el `gradle` de la imagen sobre un build script de la era 8 puede
# topar con APIs retiradas en 9.
FROM gradle:9-jdk21@sha256:86e1c174075288adf17b93172e07a4f018073735ae317134986c0ad55d0244db AS runtime

LABEL org.opencontainers.image.title="agent-runtime-java-gradle" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: JDK 21 + Gradle 9."

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
