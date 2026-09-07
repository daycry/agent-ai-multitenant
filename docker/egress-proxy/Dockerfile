# Proxy de egress allowlisted entre el sandbox `agent-runtime` y los
# proveedores LLM (ADR 0019 / task_02_35). Tinyproxy con `FilterDefaultDeny`:
# sólo los hosts del fichero `filter` salen al exterior.
#
# Construye solo: `docker build -t agentic-platform/egress-proxy:v1 docker/egress-proxy/`.
# En producción lo levanta docker-compose como servicio `egress-proxy`.
# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

RUN apk add --no-cache tinyproxy~=1.11 && \
    install -d -o tinyproxy -g tinyproxy /var/log/tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter.txt /etc/tinyproxy/filter

EXPOSE 8888

# Foreground, sin syslog, para que docker capture stdout/stderr.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
