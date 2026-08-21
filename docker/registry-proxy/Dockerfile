# Proxy de egress allowlisted entre los RUNTIME-TEMPLATES y los registries de
# paquetes públicos (ADR 0094). Tinyproxy con `FilterDefaultDeny`: sólo los hosts
# del fichero `filter` salen al exterior. Segunda instancia, disjunta del
# `egress-proxy` LLM (el agent-runtime NUNCA usa este proxy).
#
# Construye solo: `docker build -t agentic-registry-proxy:v1 docker/registry-proxy/`.
# En producción lo levanta docker-compose como servicio `registry-proxy`.
# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc

RUN apk add --no-cache tinyproxy~=1.11 && \
    install -d -o tinyproxy -g tinyproxy /var/log/tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter.txt /etc/tinyproxy/filter

EXPOSE 8888

# Foreground, sin syslog, para que docker capture stdout/stderr.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
