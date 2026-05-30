---
plan_id: 12-backup-restore
title: Backup, Restore y Continuidad
status: in_progress
blocking_plan: [00-fundaciones]
started_at: 2026-05-30
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 40-55
estimated_cost_human_eur: 16.000 € – 22.000 €
estimated_cost_ai_eur: 60 € – 100 €
created_by: system_architect
spec_sections_referenced: [25]
docs_language: es
---

# Plan 12 — Backup, Restore y Continuidad

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `12-backup-restore`                       |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `00-fundaciones`                          |
| **Tiempo estimado (calendario)**   | 2-3 semanas                               |
| **Tiempo estimado (persona-días)** | 40-55                                     |
| **Previsión de coste — humano**    | 16.000 € – 22.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 60 € – 100 €                              |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/12-backup-restore`                  |
| **Secciones del .docx**            | [25]                                      |

---

## Descripción Detallada

### Resumen Ejecutivo

Backup automatizado diario (pg_dump + tar de volúmenes) con destinos remotos opcionales (S3, B2, NAS, rclone). Restore selectivo por tenant. Monitorización del host. Runbooks de DR documentados.

### Contexto

El backup manual con cron de Fase 0 es suficiente para arrancar; esta fase lo institucionaliza con UI, restore probado, monitoring.

### Alcance

**Entra en este plan**:

- Backup automático diario 03:00 (cron) configurable.
- pg_dump lógico para PostgreSQL (permite restore selectivo por tenant).
- tar + gzip de volúmenes (MinIO, Vault snapshots, Redis RDB).
- Retención local 7 días + destinos remotos opcionales (S3, B2, SFTP/NAS, rclone genérico).
- Restore con UI: lista de backups disponibles, doble confirmación, log de progreso.
- Restore selectivo por tenant.
- Monitorización del host: disco, RAM, swap, CPU, OOM kills.
- Alertas si disco >80%, si último backup falla, si RAM sostenida >90%.
- Runbooks documentados: DR completo, restore selectivo, backup manual, rotación de unseal keys.

**Queda fuera (otras fases)**:

- Réplicas en streaming (queda fuera del modelo mono-máquina).
- Multi-región (queda fuera del scope).

### Decisiones Clave

- pg_dump lógico (no pgBaseBackup binario): más portable, permite restore selectivo por tenant.
- Cifrado opcional en reposo del backup (AES-256 con clave del Vault).
- Backup pre-upgrade automático antes de cualquier docker compose pull.

### Riesgos Identificados

| Riesgo                                | Probabilidad | Impacto | Mitigación                                                                                          |
| ------------------------------------- | ------------ | ------- | --------------------------------------------------------------------------------------------------- |
| Backup corrupto sin detectar          | Media        | Crítico | Verificación post-backup (pg_restore --list, tar -tf). Test de restore semanal en máquina paralela. |
| Espacio en disco se llena con backups | Media        | Alto    | Política de rotación + alertas tempranas + sincronización inmediata a destino remoto.               |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Motor de Backup

#### `task_12_01` — Script de backup full: pg_dump + tar de volúmenes + verificación

- [x] **Título**: Script de backup full: pg_dump + tar de volúmenes + verificación
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: devops + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_12_01_a
    description: "Script de backup full: pg_dump + tar de volúmenes + verificación"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_full.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_02` — Cifrado opcional con clave del Vault

- [x] **Título**: Cifrado opcional con clave del Vault
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: security
- **Dependencias**: `task_12_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_02_a
    description: "Cifrado opcional con clave del Vault"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_encryption.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_03` — Verificación post-backup automática (corruption check)

- [x] **Título**: Verificación post-backup automática (corruption check)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_12_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_03_a
    description: "Verificación post-backup automática (corruption check)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_verification.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_04` — Configuración de cron + ventana horaria configurable desde panel admin

- [x] **Título**: Configuración de cron + ventana horaria configurable desde panel admin
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops + frontend-dev
- **Dependencias**: `task_12_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_04_a
    description: "Configuración de cron + ventana horaria configurable desde panel admin"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/backup-schedule.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Destinos Remotos

#### `task_12_05` — Destino S3 (cualquier provider compatible) con boto3

- [x] **Título**: Destino S3 (cualquier provider compatible) con boto3
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_12_05_a
    description: "Destino S3 (cualquier provider compatible) con boto3"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dest_s3.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_06` — Destino Backblaze B2 (es S3-compatible pero con quirks)

- [x] **Título**: Destino Backblaze B2 (es S3-compatible pero con quirks)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_12_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_06_a
    description: "Destino Backblaze B2 (es S3-compatible pero con quirks)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dest_b2.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_07` — Destino SFTP/NAS

- [x] **Título**: Destino SFTP/NAS
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_12_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_07_a
    description: "Destino SFTP/NAS"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dest_sftp.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_08` — Destino rclone genérico (cualquier backend)

- [x] **Título**: Destino rclone genérico (cualquier backend)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_12_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_08_a
    description: "Destino rclone genérico (cualquier backend)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dest_rclone.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_09` — UI de configuración de destinos con test de conectividad

- [x] **Título**: UI de configuración de destinos con test de conectividad
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_12_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_09_a
    description: "UI de configuración de destinos con test de conectividad"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/backup-destinations.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Restore

#### `task_12_10` — Restore completo desde backup (con detención del stack y reinicio limpio)

- [x] **Título**: Restore completo desde backup (con detención del stack y reinicio limpio)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: devops + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_12_10_a
    description: "Restore completo desde backup (con detención del stack y reinicio limpio)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_full.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_11` — Restore selectivo por tenant (solo sus tablas + sus volúmenes parciales)

- [x] **Título**: Restore selectivo por tenant (solo sus tablas + sus volúmenes parciales)
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_12_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_11_a
    description: "Restore selectivo por tenant (solo sus tablas + sus volúmenes parciales)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_per_tenant.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_12` — UI de restore con lista de backups, preview, doble confirmación, log de progreso

- [x] **Título**: UI de restore con lista de backups, preview, doble confirmación, log de progreso
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_12_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_12_a
    description: "UI de restore con lista de backups, preview, doble confirmación, log de progreso"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/restore-ui.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Monitorización del Host y Cierre

#### `task_12_13` — node-exporter + cAdvisor en el stack para métricas del host y contenedores

- [x] **Título**: node-exporter + cAdvisor en el stack para métricas del host y contenedores
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_12_13_a
    description: "node-exporter + cAdvisor en el stack para métricas del host y contenedores"
    check_type: automated
    runtime: generic-shell
    command: "curl -f http://prometheus:9090/api/v1/query?query=node_load1"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_14` — Alertmanager con reglas: disco >80%, RAM >90% sostenida, swap activo, OOM kills, último backup fallido

- [ ] **Título**: Alertmanager con reglas: disco >80%, RAM >90% sostenida, swap activo, OOM kills, último backup fallido
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_12_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_14_a
    description: "Alertmanager con reglas: disco >80%, RAM >90% sostenida, swap activo, OOM kills, último backup fallido"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_host_alerts.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_15` — Dashboards Grafana del host (CPU, RAM, disco, red, contenedores)

- [ ] **Título**: Dashboards Grafana del host (CPU, RAM, disco, red, contenedores)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_12_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_15_a
    description: "Dashboards Grafana del host (CPU, RAM, disco, red, contenedores)"
    check_type: automated
    runtime: generic-shell
    command: "curl -f http://grafana:3000/api/dashboards/uid/host-overview"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_16` — Runbooks: DR completo, restore selectivo, backup manual, rotación unseal keys

- [ ] **Título**: Runbooks: DR completo, restore selectivo, backup manual, rotación unseal keys
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer + devops
- **Dependencias**: `task_12_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_16_a
    description: "Runbooks: DR completo, restore selectivo, backup manual, rotación unseal keys"
    check_type: automated
    runtime: generic-shell
    command: "ls docs/06-runbooks/*.md | wc -l | awk '$1>=4 {exit 0} {exit 1}'"
    expected_signal: "exit_code == 0"
  ```

#### `task_12_17` — Documentación + ADRs + changelog

- [ ] **Título**: Documentación + ADRs + changelog
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_12_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_12_17_a
    description: "Documentación + ADRs + changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/12-backup-restore.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_12_01
  description: "Backup automático funciona sin intervención"
  hint: "Esperar 24h y verificar backup en disco + remoto"
  checklist:
    - "A las 03:00 el job dispara"
    - "Backup completo aparece en /data/.../backups/"
    - "Si hay destino remoto configurado, se sincroniza"
    - "Log de backup en audit + notificación al admin si fallo"

- id: human_12_02
  description: "Restore completo en máquina virgen"
  hint: "En máquina nueva, hacer restore de un backup completo"
  checklist:
    - "El stack se restaura con todos los tenants y sus datos"
    - "Los usuarios pueden hacer login con sus credenciales previas"
    - "Los proyectos, planes, conversaciones aparecen intactos"
    - "Los volúmenes (MinIO, Vault, Redis) restauran correctamente"

- id: human_12_03
  description: "Restore selectivo por tenant"
  hint: "Tenant accidentalmente borra todos sus proyectos; restaurar solo ese tenant"
  checklist:
    - "La UI ofrece restore selectivo del tenant afectado"
    - "Otros tenants NO se ven afectados durante el restore"
    - "El tenant afectado recupera sus datos al momento del backup elegido"
    - "Audit log refleja la operación con quién la hizo"

- id: human_12_04
  description: "Alertas del host funcionan"
  hint: "Simular disco lleno (fill con dd)"
  checklist:
    - "Alerta llega al canal del System Admin en menos de 5 min"
    - "El sistema entra en modo degradado: pausa workers no críticos, evita escribir más backups"
    - "Tras liberar espacio, alerta de recuperación"
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 13** (`13-api-publica-webhooks.md`).
