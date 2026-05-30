---
plan_id: 10-asistente-personal
title: Asistente Personal y Notificaciones Multicanal
status: in_progress
blocking_plan: [06-testing-revision-git]
started_at: 2026-05-30
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 60-80
estimated_cost_human_eur: 24.000 € – 32.000 €
estimated_cost_ai_eur: 120 € – 180 €
created_by: system_architect
spec_sections_referenced: [17]
docs_language: es
---

# Plan 10 — Asistente Personal y Notificaciones Multicanal

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `10-asistente-personal`                   |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `06-testing-revision-git`                 |
| **Tiempo estimado (calendario)**   | 3-4 semanas                               |
| **Tiempo estimado (persona-días)** | 60-80                                     |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 120 € – 180 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/10-asistente-personal`              |
| **Secciones del .docx**            | [17]                                      |

---

## Descripción Detallada

### Resumen Ejecutivo

Asistente personal cross-proyecto **por tenant, accesible únicamente a Tenant Admins** (ver nota de revisión vigente en sección 17 del .docx). Identidad personalizable a nivel tenant (nombre, avatar, tono, idioma, tools habilitadas) y preferencias de canal por Tenant Admin individual. Canales Telegram, WhatsApp, Email, Slack, Teams, Discord, SMS, webhooks salientes con plantillas pre-configuradas, firma HMAC, reintentos, dead-letter queue. Toggle `Organization.personal_assistant_enabled` por tenant (default false).

### Contexto

Hasta aquí las notificaciones eran in-app. Esta fase abre el sistema a los canales que la gente usa de verdad: Telegram, Slack, Email, WhatsApp. El usuario configura los suyos.

### Alcance

**Entra en este plan**:

- Servicio notification-dispatcher centralizado.
- Modelos NotificationChannel, NotificationPreference, NotificationLog.
- Canales: Telegram, WhatsApp Cloud API, Email (SMTP), Slack, Microsoft Teams, Discord, SMS (Twilio), webhooks salientes.
- Plantillas pre-configuradas (Slack message blocks, Teams Adaptive Cards, Discord embeds, Jinja2 custom).
- Configuración del asistente a nivel tenant: identidad (nombre, avatar, tono, idioma, system_prompt override, lista de tools habilitadas). Configuración de canales y preferencias de notificación a nivel Tenant Admin individual. System Admin define plataformas habilitadas globalmente.
- Toggle `Organization.personal_assistant_enabled` (default false). Si false, ningún Tenant Admin del tenant puede interactuar con el asistente.
- Tools del asistente con visibilidad cross-project del tenant: `tenant_projects_status`, `tenant_plans_summary`, `tenant_budget_status`, `tenant_recent_activity`, etc., respetando RBAC del admin que pregunta.
- Asistente personal conversacional cross-proyecto — **solo accesible para users con role=admin del tenant**. Users con role=member NO pueden interactuar con él.
- Eventos del sistema mapeados a notificaciones: task_blocked, plan_approved, review_needed, **budget_alert (umbrales cruzados, ver Fase 11 y sección 28.7 del .docx)**, escalado a humano tras max_review_retries, etc.
- Firma HMAC para webhooks salientes.
- Reintentos con backoff exponencial y dead-letter queue.

**Queda fuera (otras fases)**:

- Webhooks entrantes (Fase 13).
- Plantillas custom complejas (los tenants pueden añadirlas pero plantillas pre-cargadas básicas).

### Decisiones Clave

- Telegram como canal primario por simplicidad de setup (sin verificación Meta).
- WhatsApp via Cloud API (no Twilio): coste menor pero requiere setup Meta Business.
- Webhooks salientes con HMAC SHA-256 + nonce + timestamp para anti-replay.

### Riesgos Identificados

| Riesgo                                             | Probabilidad | Impacto | Mitigación                                                                         |
| -------------------------------------------------- | ------------ | ------- | ---------------------------------------------------------------------------------- |
| WhatsApp requiere aprobación de plantillas en Meta | Alta         | Medio   | Documentar bien el proceso. Empezar con Telegram + Email.                          |
| Spam de notificaciones molesta al usuario          | Media        | Alto    | Rate limiting por canal, agrupación de eventos similares, preferencias granulares. |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Modelo y Dispatcher

#### `task_10_01` — Modelos NotificationChannel, Preference, Log con scopes (plataforma/tenant/usuario)

- [x] **Título**: Modelos NotificationChannel, Preference, Log con scopes (plataforma/tenant/usuario)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_10_01_a
    description: "Modelos NotificationChannel, Preference, Log con scopes (plataforma/tenant/usuario)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_notification_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_02` — Servicio notification-dispatcher con colas dedicadas Celery

- [x] **Título**: Servicio notification-dispatcher con colas dedicadas Celery
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_02_a
    description: "Servicio notification-dispatcher con colas dedicadas Celery"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dispatcher.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_03` — Sistema de plantillas con Jinja2 + plantillas pre-cargadas

- [x] **Título**: Sistema de plantillas con Jinja2 + plantillas pre-cargadas
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_03_a
    description: "Sistema de plantillas con Jinja2 + plantillas pre-cargadas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_templates.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_04` — Mapeo eventos del sistema → notificaciones

- [x] **Título**: Mapeo eventos del sistema → notificaciones
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_04_a
    description: "Mapeo eventos del sistema → notificaciones"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_event_mapping.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Canales Primarios

#### `task_10_05` — Canal Telegram con python-telegram-bot

- [x] **Título**: Canal Telegram con python-telegram-bot
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_10_05_a
    description: "Canal Telegram con python-telegram-bot"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_telegram.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_06` — Canal Email con aiosmtplib (SMTP) o sendgrid (opcional)

- [x] **Título**: Canal Email con aiosmtplib (SMTP) o sendgrid (opcional)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_06_a
    description: "Canal Email con aiosmtplib (SMTP) o sendgrid (opcional)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_email.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_07` — Canal Slack con slack-bolt + blocks

- [x] **Título**: Canal Slack con slack-bolt + blocks
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_07_a
    description: "Canal Slack con slack-bolt + blocks"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_slack.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_08` — Canal Microsoft Teams con webhooks + Adaptive Cards

- [x] **Título**: Canal Microsoft Teams con webhooks + Adaptive Cards
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_08_a
    description: "Canal Microsoft Teams con webhooks + Adaptive Cards"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_teams.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_09` — Canal Discord con embeds via webhook

- [x] **Título**: Canal Discord con embeds via webhook
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_09_a
    description: "Canal Discord con embeds via webhook"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_discord.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Canales Secundarios y Webhooks

#### `task_10_10` — Canal WhatsApp Cloud API con plantillas pre-aprobadas

- [x] **Título**: Canal WhatsApp Cloud API con plantillas pre-aprobadas
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_10_10_a
    description: "Canal WhatsApp Cloud API con plantillas pre-aprobadas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_whatsapp.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_11` — Canal SMS con Twilio

- [ ] **Título**: Canal SMS con Twilio
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_11_a
    description: "Canal SMS con Twilio"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_channel_sms.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_12` — Webhooks salientes con firma HMAC + nonce + timestamp

- [ ] **Título**: Webhooks salientes con firma HMAC + nonce + timestamp
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_10_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_12_a
    description: "Webhooks salientes con firma HMAC + nonce + timestamp"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_outbound_webhooks.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_13` — Reintentos exponenciales + dead-letter queue + reintento manual desde UI

- [ ] **Título**: Reintentos exponenciales + dead-letter queue + reintento manual desde UI
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_10_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_13_a
    description: "Reintentos exponenciales + dead-letter queue + reintento manual desde UI"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_retries_dlq.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Asistente Personal y UI

#### `task_10_14` — Asistente personal conversacional (responde queries del estado global por chat)

- [ ] **Título**: Asistente personal conversacional (responde queries del estado global por chat)
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_10_14_a
    description: "Asistente personal conversacional (responde queries del estado global por chat)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_personal_assistant.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_15` — UI de configuración de canales en 3 capas (plataforma/tenant/usuario)

- [ ] **Título**: UI de configuración de canales en 3 capas (plataforma/tenant/usuario)
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_10_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_15_a
    description: "UI de configuración de canales en 3 capas (plataforma/tenant/usuario)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/notification-config.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_16` — Inbox in-app con histórico de notificaciones

- [ ] **Título**: Inbox in-app con histórico de notificaciones
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_10_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_16_a
    description: "Inbox in-app con histórico de notificaciones"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/notification-inbox.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_10_17` — Documentación + ADRs + changelog

- [ ] **Título**: Documentación + ADRs + changelog
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_10_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_10_17_a
    description: "Documentación + ADRs + changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/10-asistente-personal.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_10_01
  description: "Notificaciones por Telegram funcionan"
  hint: "Configurar canal Telegram personal y disparar evento task_blocked"
  checklist:
    - "El mensaje llega al chat correcto en menos de 30s"
    - "Contiene contexto suficiente (tarea, proyecto, motivo)"
    - "Click en el botón inline 'Ver' abre la UI en la tarea"

- id: human_10_02
  description: "Preferencias granulares funcionan"
  hint: "Usuario desactiva notificaciones de budget_alert por Slack pero las mantiene por email"
  checklist:
    - "budget_alert NO llega por Slack"
    - "budget_alert SÍ llega por email"

- id: human_10_03
  description: "Webhooks salientes con firma"
  hint: "Configurar webhook outbound + endpoint receptor de prueba"
  checklist:
    - "La firma HMAC verifica con la clave compartida"
    - "Anti-replay funciona: el mismo nonce no se acepta dos veces"
    - "Si el receptor devuelve 5xx, reintenta con backoff"
    - "Tras 5 reintentos, va a dead-letter queue"

- id: human_10_04
  description: "Asistente personal responde queries"
  hint: "Usuario habla con asistente: 'qué tareas tengo pendientes?'"
  checklist:
    - "Devuelve listado consolidado cross-proyecto"
    - "Puede aprobar planes pendientes desde el chat con el asistente"
    - "Mantiene contexto entre mensajes"
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

Tras cerrar este plan, el siguiente es **Plan 11** (`11-guardrails-precios.md`).
