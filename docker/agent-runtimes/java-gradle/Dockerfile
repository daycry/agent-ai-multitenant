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
#
# 2026-08-18: se cambia de variante, de `gradle:9-jdk21` (Ubuntu 26.04
# «resolute») a `gradle:9-jdk21-noble` (Ubuntu 24.04 LTS). Es el MISMO Gradle
# 9.7.0 sobre el MISMO Temurin 21.0.11+10 —comprobado ejecutando las dos
# imágenes—: lo único que se mueve es la capa de sistema operativo, y se mueve
# porque ahí está el problema.
#
# Qué denunciaba Trivy (8 HIGH, 0 CRITICAL) y dónde exactamente: los OCHO en UN
# solo fichero, `/usr/bin/pebble`, un binario Go compilado con la stdlib
# **1.26.5**. Son CVE-2026-33818 (`encoding/asn1`, DoS por recursión en
# Unmarshal), CVE-2026-39821 (`golang.org/x/net/idna`), CVE-2026-46600
# (`golang.org/x/net/dns/dnsmessage`), CVE-2026-56853 (`net/http`, HTTP/2 sin
# cifrar), CVE-2026-56858 (`html/template`, XSS), CVE-2026-56859
# (`encoding/xml`, DoS), CVE-2026-56860 (`net/url`, DoS) y CVE-2026-56862
# (`crypto/tls`, DoS), todas con fix publicado en Go 1.26.6. El resto de la
# imagen sale limpio: la capa de SO da 0 hallazgos y los JAR de
# `/opt/gradle/lib` también (eso lo cerró el salto a Gradle 9 del 13-08).
#
# Es deriva de CVE, igual que en sus cuatro plantillas hermanas: esto pasó
# Trivy en verde el 13-08 y dejó de pasarlo sin que cambiara una línea de este
# fichero. Lo que se movió fue la base de datos de Trivy. Pero aquí el arreglo
# NO puede ser el de las hermanas, y conviene dejar escrito por qué para que el
# siguiente lector no pierda la tarde intentándolo:
#
#   1. **No hay digest nuevo que poner.** `gradle:9-jdk21` sigue resolviendo HOY
#      al mismo `86e1c174…` que ya estaba pineado, y su base anotada
#      `eclipse-temurin:21-jdk-resolute` al mismo `b3505e61…`. Aguas arriba no
#      hay nada más reciente que coger: refrescar el pin sería reescribir el
#      mismo digest.
#   2. **La caché de capas no tiene nada que ver aquí.** En `ruby-rspec` y en
#      `php-*` el culpable (`libpq`) lo instalaba un `apt-get` del propio
#      Dockerfile y lo servía viejo la caché `type=gha`. Este Dockerfile no
#      instala NADA: el binario vulnerable viene dentro de la imagen base, así
#      que no hay capa cacheada que invalidar ni índice de apt que refrescar.
#   3. **Un `apt-get upgrade` tampoco lo tocaría.** `/usr/bin/pebble` no
#      pertenece a ningún paquete —`dpkg -S /usr/bin/pebble` no encuentra
#      dueño—: lo deposita Canonical al construir la imagen OCI de Ubuntu
#      26.04, y se rebaja sólo cuando Canonical la reconstruye con un Go nuevo.
#      No hay parche en el canal de seguridad que aplicar.
#
# Por qué la variante `-noble` y no borrar el binario: `rm /usr/bin/pebble`
# también pondría el gate en verde —no lo usamos, el ENTRYPOINT es `sleep
# infinity` y el worker exec-uta los comandos—, pero sería recortar la imagen
# del proveedor para tapar el síntoma: el siguiente binario Go que Canonical
# meta en 26.04 vuelve a traer la misma deriva, y quien lea el Dockerfile no
# entendería qué se está borrando ni por qué. Ubuntu 24.04 no trae pebble
# siquiera —verificado— y es la base sobre la que ya corren en verde las
# plantillas hermanas de Java (`java-maven`, sobre `maven:3.9-eclipse-temurin-21`)
# y `node-playwright` (que se mudó a noble el 13-08 por este mismo tipo de
# razón). Lo que se acepta a cambio: 24.04 en vez de 26.04, es decir una LTS
# con soporte estándar hasta 2029 en lugar de la más nueva. Del toolchain que
# de verdad ejecuta el código del tenant —JDK y Gradle— no cambia nada.
#
# Cuándo revisar esto: cuando Canonical publique la 26.04 con un pebble
# compilado con Go >= 1.26.6 y gradle reconstruya su `9-jdk21`, este comentario
# deja de aplicar y se puede volver a la variante por defecto. Mientras tanto,
# relajar el gate (`.trivyignore`) sería suprimir ocho HIGH **con fix
# publicado** en la imagen donde el Principio Rector 2 deposita el aislamiento
# del código NO confiable, teniendo una base sin ellos a un tag de distancia.
FROM gradle:9-jdk21-noble@sha256:7818865626870c3bfd7847482bce8462e1e436a02e0b1c7737c0c47a06a1a6de AS runtime

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
