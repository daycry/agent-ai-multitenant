# Proxy de egress allowlisted entre el sandbox `agent-runtime` y los
# proveedores LLM (ADR 0019 / task_02_35). Tinyproxy con `FilterDefaultDeny`:
# sólo los hosts del fichero `filter` salen al exterior.
#
# Construye solo: `docker build -t agentic-egress-proxy:v1 docker/egress-proxy/`.
# En producción lo levanta docker-compose como servicio `egress-proxy`.
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
