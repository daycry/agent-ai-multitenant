# Vault production config. File backend on a docker volume.
# TLS is disabled here because the stack is meant to live behind a
# reverse proxy (added by the installer in phase 15). Direct internet
# exposure of this listener is never intended.

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

# Telemetry — Prometheus scrapes from /v1/sys/metrics in phase 15.
telemetry {
  prometheus_retention_time = "24h"
  disable_hostname          = true
}
