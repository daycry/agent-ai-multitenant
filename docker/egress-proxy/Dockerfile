# Proxy de egress allowlisted entre el sandbox `agent-runtime` y los
# proveedores LLM (ADR 0019 / task_02_35). Tinyproxy con `FilterDefaultDeny`:
# sólo los hosts del fichero `filter` salen al exterior.
#
# Construye solo: `docker build -t agentic-egress-proxy:v1 docker/egress-proxy/`.
# En producción lo levanta docker-compose como servicio `egress-proxy`.
FROM alpine:3.20

RUN apk add --no-cache tinyproxy~=1.11 && \
    install -d -o tinyproxy -g tinyproxy /var/log/tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter.txt /etc/tinyproxy/filter

EXPOSE 8888

# Foreground, sin syslog, para que docker capture stdout/stderr.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
