# Proxy de egress allowlisted entre los RUNTIME-TEMPLATES y los registries de
# paquetes públicos (ADR 0094). Tinyproxy con `FilterDefaultDeny`: sólo los hosts
# del fichero `filter` salen al exterior. Segunda instancia, disjunta del
# `egress-proxy` LLM (el agent-runtime NUNCA usa este proxy).
#
# Construye solo: `docker build -t agentic-registry-proxy:v1 docker/registry-proxy/`.
# En producción lo levanta docker-compose como servicio `registry-proxy`.
FROM alpine:3.20

RUN apk add --no-cache tinyproxy~=1.11 && \
    install -d -o tinyproxy -g tinyproxy /var/log/tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter.txt /etc/tinyproxy/filter

EXPOSE 8888

# Foreground, sin syslog, para que docker capture stdout/stderr.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
