# Vault production config. File backend on a docker volume.
# TLS is disabled on THIS listener because the stack lives behind the single
# TLS-terminating reverse proxy (Caddy) the installer adds — ADR 0061 / prod-01
# Fase E. Vault stays on the internal network; direct internet exposure of this
# listener is never intended.

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = "true"
}

api_addr      = "http://0.0.0.0:8200"
cluster_addr  = "http://0.0.0.0:8201"
ui            = true
disable_mlock = false

# Telemetry — Prometheus scrapes Vault metrics from /v1/sys/metrics.
telemetry {
  prometheus_retention_time = "24h"
  disable_hostname          = true
}
