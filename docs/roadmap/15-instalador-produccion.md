---
plan_id: 15-instalador-produccion
title: Instalador, Endurecimiento y Producción
status: in_progress
started_at: 2026-05-31
blocking_plan:
  [
    00-fundaciones,
    01-dominio-minimo,
    02-ejecucion-agentes,
    03-chat-planning-aprobacion,
    04-memoria-rag-kbs,
    05-mcp-tools-avanzadas,
    06-testing-revision-git,
    07-documentacion-visor,
    08-sso-empresarial,
    09-marketplace,
    10-asistente-personal,
    11-guardrails-precios,
    12-backup-restore,
    13-api-publica-webhooks,
    14-evals-estadisticas,
  ]
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 80-100
estimated_cost_human_eur: 32.000 € – 40.000 €
estimated_cost_ai_eur: 150 € – 240 €
created_by: system_architect
spec_sections_referenced: [22]
docs_language: es
---

# Plan 15 — Instalador, Endurecimiento y Producción

## Cabecera

| Campo                              | Valor                                                                                                                     |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**                    | `15-instalador-produccion`                                                                                                |
| **Estado**                         | `in_progress` (override humano del gate blocking_plan; 15_27 pentest externo + 15_29 release v1.0.0 reservados al humano) |
| **Bloqueado por**                  | todas las fases anteriores (`00-fundaciones` … `14-evals-estadisticas`)                                                   |
| **Tiempo estimado (calendario)**   | 4-5 semanas                                                                                                               |
| **Tiempo estimado (persona-días)** | 80-100                                                                                                                    |
| **Previsión de coste — humano**    | 32.000 € – 40.000 € (tarifa media 50 €/h)                                                                                 |
| **Previsión de coste — IA**        | 150 € – 240 €                                                                                                             |
| **Aprobador propuesto**            | System Admin                                                                                                              |
| **Rama git**                       | `plan/15-instalador-produccion`                                                                                           |
| **Secciones del .docx**            | [22]                                                                                                                      |

---

## Descripción Detallada

### Resumen Ejecutivo

Instalador con UI tipo wizard de 9 pasos, modo CLI desatendido, endurecimiento de seguridad, runbooks operativos completos, documentación pública, hardening del panel admin.

### Contexto

El sistema funcional ya está. Esta fase lo hace instalable por terceros sin asistencia y endurece todo para producción real.

### Alcance

**Entra en este plan**:

- Contenedor installer que sirve UI temporal (autodestructiva).
- Wizard de 9 pasos (Bienvenida → Config básica → Recursos/GPU → Almacenamiento → Providers LLM → Tenant inicial → Resumen → Instalación → Listo).
- Detección automática de GPU NVIDIA.
- Generación de docker-compose.yml + .env + config/global.yaml + estructura /data/agent-platform/.
- Bootstrap del Vault con unseal keys mostradas UNA vez.
- Validación pre-instalación de prerequisitos (Docker, Compose v2, RAM, disco).
- Modo CLI desatendido: install.sh --config install.yaml.
- Plantillas YAML por perfil (minimal, recommended, gpu).
- Script uninstall.sh con doble confirmación.
- Reinstalación sobre datos existentes con preservación opcional.
- Auditoría de seguridad completa (pentest, threat model).
- Endurecimiento aislamiento de contenedores (seccomp validado, AppArmor profiles).
- Rotación automática de credenciales (Vault dynamic secrets).
- Runbooks operativos completos (10+ runbooks).
- Documentación pública + portal de desarrollador.
- Hardening del panel admin (MFA obligatorio, IP allowlist, sesiones cortas).
- Smoke tests post-deploy completos.

**Queda fuera (otras fases)**:

- Migración a Kubernetes (queda fuera).
- Multi-instancia HA (queda fuera del modelo mono-máquina).

### Decisiones Clave

- Installer en contenedor separado que se autodestruye tras completar.
- Unseal keys de Vault mostradas UNA vez sin posibilidad de recuperación → operador es responsable de guardarlas.
- Pentest interno antes de release público.

### Riesgos Identificados

| Riesgo                                                     | Probabilidad | Impacto | Mitigación                                                                                                       |
| ---------------------------------------------------------- | ------------ | ------- | ---------------------------------------------------------------------------------------------------------------- |
| Instalador frágil en máquinas con configuraciones exóticas | Alta         | Medio   | Validación exhaustiva de prerequisitos + mensajes de error explícitos + soporte CLI manual para casos avanzados. |
| Pentest descubre vulnerabilidades críticas tarde           | Media        | Crítico | Pentest interno temprano (Fase 12), pentest externo final.                                                       |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Wizard del Instalador

#### `task_15_01` — Contenedor installer (Next.js + FastAPI mínimo) que sirve UI temporal

- [x] **Título**: Contenedor installer (Next.js + FastAPI mínimo) que sirve UI temporal
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_15_01_a
    description: "Contenedor installer (Next.js + FastAPI mínimo) que sirve UI temporal"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/installer-wizard.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_02` — Paso 1: Validación de prerequisitos (Docker, Compose, RAM, disco, GPU)

- [x] **Título**: Paso 1: Validación de prerequisitos (Docker, Compose, RAM, disco, GPU)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_15_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_02_a
    description: "Paso 1: Validación de prerequisitos (Docker, Compose, RAM, disco, GPU)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_installer_prereq.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_03` — Pasos 2-6: Captura de config (sistema, recursos, almacenamiento, providers LLM, tenant inicial)

- [x] **Título**: Pasos 2-6: Captura de config (sistema, recursos, almacenamiento, providers LLM, tenant inicial)
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_15_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_03_a
    description: "Pasos 2-6: Captura de config (sistema, recursos, almacenamiento, providers LLM, tenant inicial)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/installer-steps.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_04` — Paso 7: Resumen y confirmación con preview de recursos

- [x] **Título**: Paso 7: Resumen y confirmación con preview de recursos
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_15_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_04_a
    description: "Paso 7: Resumen y confirmación con preview de recursos"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/installer-summary.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_05` — Paso 8: Instalación con progress + logs en tiempo real

- [x] **Título**: Paso 8: Instalación con progress + logs en tiempo real
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_15_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_05_a
    description: "Paso 8: Instalación con progress + logs en tiempo real"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/installer-progress.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_06` — Paso 9: Confirmación con credenciales mostradas UNA vez + autodestrucción del installer

- [x] **Título**: Paso 9: Confirmación con credenciales mostradas UNA vez + autodestrucción del installer
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_15_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_06_a
    description: "Paso 9: Confirmación con credenciales mostradas UNA vez + autodestrucción del installer"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_installer_finalize.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Generación de Config y CLI

#### `task_15_07` — Generador de docker-compose.yml según opciones del wizard

- [x] **Título**: Generador de docker-compose.yml según opciones del wizard
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_15_07_a
    description: "Generador de docker-compose.yml según opciones del wizard"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_08` — Generador de .env, config/global.yaml, estructura /data/agent-platform/

- [x] **Título**: Generador de .env, config/global.yaml, estructura /data/agent-platform/
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_15_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_08_a
    description: "Generador de .env, config/global.yaml, estructura /data/agent-platform/"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_config_generators.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_09` — Bootstrap del Vault: init + unseal + KV v2 + políticas iniciales

- [x] **Título**: Bootstrap del Vault: init + unseal + KV v2 + políticas iniciales
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: security + devops
- **Dependencias**: `task_15_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_09_a
    description: "Bootstrap del Vault: init + unseal + KV v2 + políticas iniciales"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_vault_bootstrap.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_10` — Modo CLI desatendido: install.sh --config install.yaml

- [x] **Título**: Modo CLI desatendido: install.sh --config install.yaml
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_15_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_10_a
    description: "Modo CLI desatendido: install.sh --config install.yaml"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_cli_install.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_11` — Plantillas YAML por perfil (minimal, recommended, gpu)

- [x] **Título**: Plantillas YAML por perfil (minimal, recommended, gpu)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_15_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_11_a
    description: "Plantillas YAML por perfil (minimal, recommended, gpu)"
    check_type: automated
    runtime: generic-shell
    command: "ls scripts/install-profiles/ | wc -l | awk '$1>=3 {exit 0} {exit 1}'"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_12` — Script uninstall.sh con doble confirmación

- [x] **Título**: Script uninstall.sh con doble confirmación
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_15_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_12_a
    description: "Script uninstall.sh con doble confirmación"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_uninstall.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_13` — Reinstalación con preservación de datos opcional

- [x] **Título**: Reinstalación con preservación de datos opcional
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_15_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_13_a
    description: "Reinstalación con preservación de datos opcional"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_reinstall.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Endurecimiento de Seguridad

#### `task_15_14` — Pentest interno: enumeración de superficie + escalada de privilegios + escapes de contenedor

- [x] **Título**: Pentest interno: enumeración de superficie + escalada de privilegios + escapes de contenedor
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: security
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_15_14_a
    description: "Pentest interno: enumeración de superficie + escalada de privilegios + escapes de contenedor"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/security/test_pentest_findings.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_15` — Endurecimiento seccomp con perfiles validados por contenedor

- [x] **Título**: Endurecimiento seccomp con perfiles validados por contenedor
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: security + devops
- **Dependencias**: `task_15_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_15_a
    description: "Endurecimiento seccomp con perfiles validados por contenedor"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/security/test_seccomp_profiles.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_16` — AppArmor profiles aplicados a contenedores

- [x] **Título**: AppArmor profiles aplicados a contenedores
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: security + devops
- **Dependencias**: `task_15_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_16_a
    description: "AppArmor profiles aplicados a contenedores"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/security/test_apparmor.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_17` — Rotación automática de credenciales con Vault dynamic secrets

- [x] **Título**: Rotación automática de credenciales con Vault dynamic secrets
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: security + devops
- **Dependencias**: `task_15_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_17_a
    description: "Rotación automática de credenciales con Vault dynamic secrets"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_credential_rotation.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_18` — Hardening del panel admin: MFA obligatorio, IP allowlist, sesiones cortas (15 min)

- [x] **Título**: Hardening del panel admin: MFA obligatorio, IP allowlist, sesiones cortas (15 min)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: security + backend-dev
- **Dependencias**: `task_15_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_18_a
    description: "Hardening del panel admin: MFA obligatorio, IP allowlist, sesiones cortas (15 min)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/security/test_admin_hardening.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Documentación y Runbooks

#### `task_15_19` — Runbook: instalación desde cero

- [x] **Título**: Runbook: instalación desde cero
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_15_19_a
    description: "Runbook: instalación desde cero"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/06-runbooks/01-installation-from-scratch.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_20` — Runbook: troubleshooting común

- [x] **Título**: Runbook: troubleshooting común
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_15_19`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_20_a
    description: "Runbook: troubleshooting común"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/06-runbooks/02-troubleshooting.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_21` — Runbook: upgrade del sistema

- [x] **Título**: Runbook: upgrade del sistema
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_15_20`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_21_a
    description: "Runbook: upgrade del sistema"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/06-runbooks/03-system-upgrade.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_22` — Runbook: DR completo + restore selectivo (consolidar de Fase 12)

- [x] **Título**: Runbook: DR completo + restore selectivo (consolidar de Fase 12)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_15_21`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_22_a
    description: "Runbook: DR completo + restore selectivo (consolidar de Fase 12)"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/06-runbooks/04-disaster-recovery.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_23` — Runbook: rotación de unseal keys + rotación de credenciales

- [x] **Título**: Runbook: rotación de unseal keys + rotación de credenciales
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer + security
- **Dependencias**: `task_15_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_23_a
    description: "Runbook: rotación de unseal keys + rotación de credenciales"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/06-runbooks/05-key-rotation.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_24` — Runbook: gestión de capacity (escalar workers, etc.)

- [ ] **Título**: Runbook: gestión de capacity (escalar workers, etc.)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer + devops
- **Dependencias**: `task_15_23`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_24_a
    description: "Runbook: gestión de capacity (escalar workers, etc.)"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/06-runbooks/06-capacity-management.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_25` — Documentación pública + portal de desarrollador (API reference, SDKs, tutoriales)

- [ ] **Título**: Documentación pública + portal de desarrollador (API reference, SDKs, tutoriales)
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_15_24`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_25_a
    description: "Documentación pública + portal de desarrollador (API reference, SDKs, tutoriales)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/dev-portal.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_26` — Smoke tests post-deploy automáticos completos

- [ ] **Título**: Smoke tests post-deploy automáticos completos
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops + qa
- **Dependencias**: `task_15_25`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_26_a
    description: "Smoke tests post-deploy automáticos completos"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/smoke/ -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Cierre Final

#### `task_15_27` — Pentest externo (auditoría profesional)

- [ ] **Título**: Pentest externo (auditoría profesional)
- **Tiempo estimado**: 40 h
- **Complejidad**: l
- **Rol sugerido**: external auditor
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_15_27_a
    description: "Pentest externo (auditoría profesional)"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/05-architecture-decisions/0099-external-pentest-results.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_28` — Documentación final + ADRs + changelog del plan

- [ ] **Título**: Documentación final + ADRs + changelog del plan
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_15_27`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_28_a
    description: "Documentación final + ADRs + changelog del plan"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/15-instalador-produccion.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_15_29` — Release v1.0.0

- [ ] **Título**: Release v1.0.0
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_15_28`
- **Tests automáticos**:
  ```yaml
  - id: auto_15_29_a
    description: "Release v1.0.0"
    check_type: automated
    runtime: generic-shell
    command: "git tag | grep -q v1.0.0"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_15_01
  description: "Instalación desde cero en máquina virgen"
  hint: "Provisionar máquina Ubuntu 24.04 nueva, clonar repo, ejecutar install.sh"
  checklist:
    - "Wizard aparece en navegador en menos de 30s"
    - "Cada paso del wizard es comprensible para alguien que no ha leído la doc"
    - "Detección de GPU funciona si la máquina tiene una"
    - "Tras instalar, panel admin accesible con credenciales mostradas"
    - "Contenedor installer se autodestruye"

- id: human_15_02
  description: "Modo CLI desatendido funciona"
  hint: "Provisionar otra máquina virgen y usar install.sh --config install.yaml"
  checklist:
    - "Sin intervención humana, el sistema arranca"
    - "Las plantillas YAML por perfil cubren los 3 casos típicos"
    - "Logs claros indican el progreso"
    - "Tras completar, el sistema responde como en instalación con UI"

- id: human_15_03
  description: "Pentest interno: aislamiento robusto"
  hint: "Auditor intenta escapes de contenedor, escalada de privilegios, fugas cross-tenant"
  checklist:
    - "Ningún escape de contenedor exitoso"
    - "Ningún cross-tenant unauthorized access"
    - "Rate limiting resiste DDoS básico"
    - "Credenciales rotadas no quedan en logs"

- id: human_15_04
  description: "Reinstalación sobre datos existentes"
  hint: "Instalar, llenar de datos, reinstalar con 'preservar datos'"
  checklist:
    - "Datos persistentes intactos tras reinstalación"
    - "Solo configuración se regenera (limpia)"
    - "Tenants existentes pueden hacer login con sus credenciales previas"

- id: human_15_05
  description: "Documentación es navegable"
  hint: "Desarrollador nuevo lee el portal de desarrollador"
  checklist:
    - "Tutorial Quick Start completable en menos de 30 min"
    - "API Reference completa y precisa"
    - "SDKs documentados con ejemplos"
    - "Runbooks cubren los escenarios operativos típicos"
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
