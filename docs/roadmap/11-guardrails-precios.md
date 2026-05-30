---
plan_id: 11-guardrails-precios
title: Guardrails Declarativos y Catálogo de Precios
status: in_progress
blocking_plan: [02-ejecucion-agentes]
started_at: 2026-05-30
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 60-80
estimated_cost_human_eur: 24.000 € – 32.000 €
estimated_cost_ai_eur: 150 € – 240 €
created_by: system_architect
spec_sections_referenced: [19.5, 30.8]
docs_language: es
---

# Plan 11 — Guardrails Declarativos y Catálogo de Precios

## Cabecera

| Campo                              | Valor                                                                                         |
| ---------------------------------- | --------------------------------------------------------------------------------------------- |
| **ID del Plan**                    | `11-guardrails-precios`                                                                       |
| **Estado**                         | `in_progress` (NO pasa a `pending_human_validation`: 11_20 sin commit, 11_21 sin implementar) |
| **Bloqueado por**                  | `02-ejecucion-agentes`                                                                        |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                                                                   |
| **Tiempo estimado (persona-días)** | 60-80                                                                                         |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h)                                                     |
| **Previsión de coste — IA**        | 150 € – 240 €                                                                                 |
| **Aprobador propuesto**            | System Admin                                                                                  |
| **Rama git**                       | `plan/11-guardrails-precios`                                                                  |
| **Secciones del .docx**            | [19.5, 30.8]                                                                                  |

> **Nota de cierre (task_11_23).** La documentación del plan (changelog, ADR
> 0035, referencias) está completa, pero el plan **NO** se mueve a
> `pending_human_validation` porque no todas sus tareas están `done`:
>
> - `task_11_20` (guardrail_events + dashboard) está implementado en el working
>   tree pero **sin commitear** y su checkbox sigue `[ ]`.
> - `task_11_21` (alertas configurables) **no está implementada** (sin modelo de
>   config, endpoint, evaluador de umbral ni `test_guardrail_alerts.py`).
>
> Además, el **sistema de Budgets / `exchange_rates` / `display_currency`** que
> el Resumen/Alcance describen **no tiene tarea numerada y no se implementó**.
> Ver `docs/07-changelog/11-guardrails-precios.md` (sección Pendiente).

---

## Descripción Detallada

### Resumen Ejecutivo

Motor de guardrails declarativos en 4 puntos (pre_llm, post_llm, pre_tool, post_tool) con 12 tipos (PII, secret leakage, prompt injection, content safety, code safety, output schema, allowed domains, cost ceiling, etc.). Catálogo global de precios de modelos: el botón "Sincronizar precios" lee el JSON público de precios que LiteLLM publica como referencia comunitaria (`model_prices_and_context_window.json`) — **es sólo un proveedor de datos**, no implica usar LiteLLM como runtime (ADR 0021 lo retiró del catálogo de proveedores). **Soporte de cached_input_tokens (prompt caching) en model_calls y en el catálogo de precios**. **Sistema de Budgets de proyecto y tenant** con umbrales platform-global y pausado automático al 100% (ver sección 28.7 del .docx). **Manejo de moneda canónica USD + tabla exchange_rates con job diario contra ECB + conversión a moneda del tenant** (ver sección 29.9 del .docx).

### Contexto

Los guardrails endurecen el sistema. El catálogo de precios habilita estimaciones realistas que ya usa la Fase 3 (placeholders) y las facturaciones.

### Alcance

**Entra en este plan**:

- Motor guardrails_engine con pipeline declarativo YAML.
- 12 tipos de guardrails built-in (ver sección 19.5 del docx).
- Configuración por capas (plataforma → tenant → proyecto) con campos lockable.
- Integraciones opcionales: NVIDIA NeMo Guardrails, Guardrails AI, Presidio, LlamaGuard, ShieldGemma.
- 6 acciones posibles al disparar: block, redact/mask, warn, retry_with_feedback, escalate_to_human, transform.
- Tabla guardrail_events + dashboard por tenant + alertas configurables.
- Catálogo global de precios de modelos (model_prices) con vigencia, **siempre en USD canónico** (ver 29.8.5 y 29.9 del .docx).
- Pantalla 'Modelos & Precios' en menú global del System Admin.
- Botón 'Sincronizar precios' contra el JSON público de precios de LiteLLM (sólo como fuente de datos comunitaria — el sistema NO usa LiteLLM como runtime, ADR 0021) + APIs de providers.
- Sincronización programada (cron) + manual.
- Snapshot del precio por llamada en model_calls.
- **Soporte de prompt caching**: campos `tokens_cached_input` y precio de cache en el catálogo (típicamente 10% del precio de input estándar; configurable por modelo).
- **Tabla `exchange_rates`** con job diario `exchange-rates-fetcher` (Celery Beat, 06:00 UTC) contra ECB como fuente por defecto; alternativa configurable por System Admin.
- **Moneda de visualización por tenant** (`Organization.display_currency`, default EUR). Cálculo on-the-fly desde USD canónico usando el rate del día de cada execution.
- **Sistema de Budgets**: campos en Organization (`tenant_budget_amount/_currency/_period/_period_start_day/_period_length_days`) y Project (`budget_amount/_currency/_period/_paused_by_budget`). Umbrales de alerta configurables platform-global (default `[80, 90, 100]`). Notificaciones a Tenant Admins + asistente personal. Pausado automático de nuevos arranques al 100% sin matar ejecuciones activas. Override manual con audit_log.
- Guardrails específicos del chat de planning (topic adherence, hallucination check sobre números, validación estructural antes de 'Generar Plan').

**Queda fuera (otras fases)**:

- Optimización fina de prompts con guardrails feedback loop (queda para iteración posterior).

### Decisiones Clave

- Motor declarativo YAML editable desde panel admin, no código.
- Guardrails baseline obligatorios desde plataforma (PII detection, secret leakage, prompt injection).
- Catálogo de precios snapshot por llamada para auditoría histórica correcta.

### Riesgos Identificados

| Riesgo                                                    | Probabilidad | Impacto | Mitigación                                                               |
| --------------------------------------------------------- | ------------ | ------- | ------------------------------------------------------------------------ |
| Guardrails falsos positivos bloquean trabajo legítimo     | Media        | Medio   | Modo 'warn' para fase de aprendizaje; 'block' tras curva de calibración. |
| Precios desactualizados producen estimaciones incorrectas | Media        | Bajo    | Sincronización diaria + alerta si lleva >7 días sin sync.                |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Motor de Guardrails

#### `task_11_01` — Pipeline declarativo YAML con puntos pre_llm/post_llm/pre_tool/post_tool

- [x] **Título**: Pipeline declarativo YAML con puntos pre_llm/post_llm/pre_tool/post_tool
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_11_01_a
    description: "Pipeline declarativo YAML con puntos pre_llm/post_llm/pre_tool/post_tool"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_guardrails_engine.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_02` — Configuración por capas (plataforma/tenant/proyecto) con campos lockable

- [x] **Título**: Configuración por capas (plataforma/tenant/proyecto) con campos lockable
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_02_a
    description: "Configuración por capas (plataforma/tenant/proyecto) con campos lockable"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrails_layers.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_03` — 6 acciones (block, redact, warn, retry_with_feedback, escalate_to_human, transform)

- [x] **Título**: 6 acciones (block, redact, warn, retry_with_feedback, escalate_to_human, transform)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_11_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_03_a
    description: "6 acciones (block, redact, warn, retry_with_feedback, escalate_to_human, transform)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_guardrail_actions.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Guardrails Built-in (12 tipos)

#### `task_11_04` — PII detection con Presidio integrado

- [x] **Título**: PII detection con Presidio integrado
  - Guardrail `pii` registrado (hooks `pre_llm` + `post_llm`) en `packages/shared-guardrails/src/shared_guardrails/checks/pii.py`. Presidio (`presidio-analyzer`, que arrastra spaCy + modelo NER) es el extra OPCIONAL `shared-guardrails[pii]`, importado de forma LAZY: cuando está ausente degrada a un fallback regex puro de alta confianza (email/tarjeta con Luhn/teléfono/IBAN/IPv4/SSN) o, en modo `backend: presidio` estricto, a un resultado tipado "unavailable" sin romper. Acción sugerida configurable (default `redact` en `post_llm`, `block` en `pre_llm`). Tests con Presidio skip-guardados (`pytest.importorskip`); lógica de detección probada con fallback regex y analyzer mockeado (vocabulario Presidio PERSON/LOCATION). Override mypy para `presidio_analyzer` añadido al patrón optional-dep.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer + security
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_11_04_a
    description: "PII detection con Presidio integrado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pii_guardrail.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_05` — Secret leakage con patrones de tokens

- [x] **Título**: Secret leakage con patrones de tokens
  - Guardrail `secret_leakage` registrado (hooks principales `post_llm` + `post_tool`, funciona en cualquiera) en `packages/shared-guardrails/src/shared_guardrails/checks/secret_leakage.py`. Detección pura-Python (regex + entropía Shannon, sin dependencia pesada): familias de tokens bien conocidas (AWS access key, Google API key, GitHub/GitLab token, Slack token, bloque PEM de clave privada, JWT, connection string con contraseña) + asignaciones genéricas de alta entropía (`secret/token/api_key/...`) con gate de entropía para bajos falsos positivos. Acción sugerida por defecto `redact` (configurable a `block`); la redacción enmascara cada span con un marcador `[REDACTED:{type}]` sin volcar nunca el secreto en el resultado (ni en `redacted_text` ni en `spans`, que sólo llevan offsets + familia). Test `tests/integration/test_secret_leakage.py`: cada familia detectada + redactada, string benigno no marcado, y la redacción nunca devuelve el secreto.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: security
- **Dependencias**: `task_11_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_05_a
    description: "Secret leakage con patrones de tokens"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_secret_leakage.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_06` — Prompt injection detector

- [x] **Título**: Prompt injection detector
  - Guardrail `prompt_injection` registrado (hooks principales `pre_llm` + `pre_tool`, funciona en cualquiera) en `packages/shared-guardrails/src/shared_guardrails/checks/prompt_injection.py`. Detección heurística + por patrones (pura Python, sin dependencia pesada): 6 categorías — `instruction_override` ("ignore previous instructions", "disregard the system prompt", "forget everything above"), `role_switch` (jailbreak/DAN/developer mode), `system_prompt_exfiltration`, `delimiter_smuggling` (marcadores de rol inyectados `<|im_start|>system`, `[system]`), `encoding_smuggling` (decode base64 + execute) y `tool_credential_coercion`. Multilingüe (es + en). En `pre_tool` también escanea los `tool_args` (inyección en argumentos). El detector vive tras un Protocol `InjectionDetector` (inyectable vía `detector`) para enchufar más tarde un clasificador basado en modelo bajo extra opcional, pero el backend por defecto es la heurística. Acción sugerida `block` por defecto, `warn` en `learning_mode` (override explícito gana). Test `tests/integration/test_prompt_injection.py`: strings clásicos marcados, prompts benignos pasan, frasing multilingüe detectado, la acción aflora; placeholder de backend de modelo skip-guardado.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer + security
- **Dependencias**: `task_11_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_06_a
    description: "Prompt injection detector"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_prompt_injection.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_07` — Content safety con LlamaGuard o ShieldGemma

- [x] **Título**: Content safety con LlamaGuard o ShieldGemma
  - Guardrail `content_safety` registrado (hooks `pre_llm` + `post_llm`, funciona en cualquiera) en `packages/shared-guardrails/src/shared_guardrails/checks/content_safety.py`. Clasifica el texto del hook en categorías de seguridad (`violence`, `hate`, `sexual`, `self_harm`, `weapons`, `criminal`, `other`) mediante un guard model (LlamaGuard / ShieldGemma) servido a través de la capa LLM existente (Ollama/provider). El modelo vive tras una seam inyectable `SafetyClassifier` (`classify(text) -> SafetyVerdict`, síncrona como las seams `pii.analyzer` / `prompt_injection.detector`) + un extra OPCIONAL `shared-guardrails[content-safety]` que arrastra `shared-llm`; el adaptador `LLMSafetyClassifier` importa `shared-llm` de forma LAZY y solo cuando se le pasa un provider. Cuando NO hay guard model configurado (o el modelo no devuelve veredicto usable), degrada a un resultado tipado _unavailable_ (`available=False`, `reason`) — NUNCA finge un veredicto "safe" en silencio. Mapa de taxonomía LlamaGuard `S1..S13` → vocabulario estable + parser `parse_guard_response` puro/determinista. Acción sugerida `block` por defecto en contenido inseguro (configurable); severidad con suelo configurable elevado por categorías graves (sexual / self-harm → critical). Test `tests/integration/test_content_safety.py` con el clasificador MOCKEADO (sin modelo real): inseguro → triggered+block, seguro → pasa, categoría+severidad afloran, ruta unavailable tipada; placeholder del backend de modelo real skip-guardado.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_11_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_07_a
    description: "Content safety con LlamaGuard o ShieldGemma"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_content_safety.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_08` — Code safety: análisis estático de código generado (eval, exec, rm -rf, etc.)

- [x] **Título**: Code safety: análisis estático de código generado (eval, exec, rm -rf, etc.)
  - Guardrail `code_safety` registrado (hooks principales `post_llm` + `post_tool` — código generado, funciona en cualquiera) en `packages/shared-guardrails/src/shared_guardrails/checks/code_safety.py`. Detección estática pura (sin dependencia pesada obligatoria) con dos analizadores complementarios: (1) **AST de Python** — cuando el snippet parsea como Python, recorre el árbol y marca constructos peligrosos de forma estructural (no por substring): `eval`/`exec`/`compile`, `os.system`/`subprocess.*(shell=True)`, import dinámico (`__import__`/`importlib.import_module`), deserialización insegura (`pickle.loads`/`marshal.loads`), escritura fuera del workspace (`open(ruta_absoluta_o_..)` en modo escritura, `os.remove`/`shutil.rmtree`), y exfiltración de red (`socket.socket`, `urllib`, `requests.<verb>`); (2) **regex de shell** (siempre corre, también sobre shell embebido en strings de Python): `rm -rf` (y el crítico `rm -rf /`), pipe-to-shell (`curl|wget … | sh`), `chmod 777`, fork bomb `:(){ :|:& };:`, `dd of=/dev/…`/`mkfs`, y `shell=True` literal. Cuando el snippet no parsea como Python el paso AST se omite (`language="other"`) pero el regex de shell sigue corriendo, así que código no-Python nunca se ignora en silencio. Categorías estables (`eval_exec`, `shell_injection`, `destructive_fs`, `dynamic_import`, `unsafe_deserialization`, `unsafe_file_write`, `network_exfiltration`); severidad por constructo con suelo configurable (`rm -rf /`/fork bomb/`dd` → critical). Acción sugerida `block` por defecto (override explícito gana); filtros `categories` y `min_severity`. Autocontenido y puro — no usa de forma obligatoria el `StaticAnalyzer` (bandit) del marketplace de Plan 09. Test `tests/integration/test_code_safety.py`: eval/exec/`shell=True`/`rm -rf` marcados high-severity (`rm -rf /` critical), código seguro pasa, la línea/constructo ofensivo se reporta, AST de Python + regex de shell ambos cubiertos.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: security
- **Dependencias**: `task_11_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_08_a
    description: "Code safety: análisis estático de código generado (eval, exec, rm -rf, etc.)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_code_safety.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_09` — Output structure (validación JSON Schema), allowed_domains, cost_ceiling, factuality/citations, topic_restriction, rate_per_agent, forbidden_actions

- [x] **Título**: Output structure (validación JSON Schema), allowed_domains, cost_ceiling, factuality/citations, topic_restriction, rate_per_agent, forbidden_actions
  - Siete tipos built-in registrados en el motor (`@register_guardrail`), cada uno un módulo cohesivo en `packages/shared-guardrails/src/shared_guardrails/checks/`: `output_structure` (valida el output `post_llm`/`post_tool` contra un JSON Schema configurado con `jsonschema` — dep base ligera, pura Python; `not_json` y `schema_violation` disparan; acción por defecto `retry_with_feedback`), `allowed_domains` (extrae URLs del texto + `tool_args` y bloquea hosts fuera del allowlist con suffix-match de subdominios; default `block`), `cost_ceiling` (umbral de coste por llamada/acumulado leído de `metadata` — el precio real es Fase C, aquí se inyecta; default `block`, `budget_exceeded`), `factuality_citations` (heurística pura que marca afirmaciones numéricas/citadas sin cita de soporte, es+en; default `warn`), `topic_restriction` (adherencia a temas permitidos / lejanía de prohibidos por keyword whole-word con seam `TopicMatcher` para embeddings futuros; default `warn`), `rate_per_agent` (límite de llamadas por agente en ventana deslizante con `RateStore` + `clock` inyectables; default `block`) y `forbidden_actions` (deny/allowlist de herramientas en `pre_tool` — el enforcement de `allowed_tools` que difirió la auditoría 06.14 / hallazgo guardrails-1; intersecta allowlist de config con la de `metadata`; default `block`). Helper compartido `checks/_common.py` (coerción de severidad/acción/listas). `jsonschema` añadido a deps base + override mypy. Test `tests/integration/test_remaining_guardrails.py` (47 casos): cada tipo dispara y pasa según corresponde.
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: backend-dev + ai-engineer
- **Dependencias**: `task_11_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_09_a
    description: "Output structure (validación JSON Schema), allowed_domains, cost_ceiling, factuality/citations, topic_restriction, rate_per_agent, forbidden_actions"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_remaining_guardrails.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Catálogo de Precios

#### `task_11_10` — Modelo model_prices con todos los campos (provider, model_id, modality, prices, currency, vigencia, source, etc.)

- [x] **Título**: Modelo model_prices con todos los campos (provider, model_id, modality, prices, currency, vigencia, source, etc.)
  - Modelo ORM `ModelPrice` (`apps/api-server/src/api_server/db/model_prices.py`) — catálogo de precios **platform-global** (sin `tenant_id`, sin RLS; escritura System-Admin vía `get_admin_session`, lectura abierta) siguiendo el patrón `PlatformSetting`/`MarketplaceSource` + `updated_by` (FK `users` `ON DELETE SET NULL`). Precios en **USD canónico** (constante `CANONICAL_CURRENCY="USD"`, default + CHECK `currency='USD'`). Campos: `provider`, `model_id`, `modality` (enum `PriceModality`: text/vision/audio/embedding/image/rerank), `input_price`/`output_price` (`Numeric(18,10)`), `cached_input_price` (**nullable** — prompt caching; helper `cached_input_price_or_default()` ~10% de input por convención cuando es NULL), `unit` (enum `PriceUnit`, default `per_1m_tokens`), `currency`, `context_window`, `source` (enum `PriceSource`: litellm/manual/provider_api), vigencia `effective_from`/`effective_to` (NULL == periodo abierto/actual). Regla de unicidad del "precio actual": índice parcial único `uq_model_prices_current` sobre `(provider, model_id, modality)` donde `effective_to IS NULL`. Helper puro `select_current_price(...)`. SIN migración en esta tarea (es 11_11). Test `tests/unit/test_model_prices_model.py` (28 casos): construcción, enums, USD canónico, cached_input nullable + sensible, vigencia, selección del precio actual.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_11_10_a
    description: "Modelo model_prices con todos los campos (provider, model_id, modality, prices, currency, vigencia, source, etc.)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_model_prices_model.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_11` — Migración + RLS (es global, accesible a System Admin)

- [x] **Título**: Migración + RLS (es global, accesible a System Admin)
  - Migración `0049_model_prices` (`apps/api-server/migrations/versions/20260530_0049_model_prices.py`, `down_revision = 0048_notification_log_reads`, head único) que crea la tabla **platform-global** `model_prices` con las columnas/enums/índices/CHECKs definidos en 11*10 (USD canónico + `cached_input_price` nullable + vigencia `effective_from`/`effective_to`). Decisión de tenancy: **sin `tenant_id`**; el split lectura/escritura se aplica en BD con una **RLS de lectura global** — `ENABLE` + `FORCE ROW LEVEL SECURITY` + una única política `model_prices_global_read` `FOR SELECT USING (true)` y **ninguna política de escritura**, de modo que una sesión NOBYPASSRLS (tenant) puede leer todo el catálogo pero tiene denegado todo INSERT/UPDATE/DELETE, mientras la sesión System-Admin BYPASSRLS (`get_admin_session`) escribe libremente (espeja el patrón `marketplace_listings*\*\_read`de 0041 y`agents_global_builtin_read`de 0004). Índice de lookup del precio actual`(provider, model_id, modality, effective_from)`+ parcial-único`uq_model_prices_current`(un periodo abierto por clave) + índice FK`updated_by`. Reversibilidad probada en BD scratch (up/down a `0040_sso_email_domains`/up). Test `tests/integration/test_prices_migration.py`(10 casos): tabla + columnas (sin`tenant_id`) + índices, RLS habilitada con la política SELECT-only, lectura global desde cualquier sesión, tenant no puede escribir (la fila sobrevive a UPDATE/DELETE denegados) mientras admin sí, CHECK USD, unicidad del periodo abierto, downgrade limpio.
- **Tiempo estimado**: 3 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_11_a
    description: "Migración + RLS (es global, accesible a System Admin)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_prices_migration.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_12` — Endpoint CRUD del catálogo (solo System Admin)

- [x] **Título**: Endpoint CRUD del catálogo (solo System Admin)
  - Routers del catálogo de precios en `apps/api-server/src/api_server/routers/model_prices.py` + schemas Pydantic en `apps/api-server/src/api_server/schemas/model_prices.py`, montados en `main.py`. **Split lectura/escritura platform-global** que espeja el patrón del marketplace: las ESCRITURAS van en un `admin_router` (`/admin/model-prices`) gateado por `require_system_admin` sobre la sesión BYPASSRLS `get_admin_session` (un `tenant_admin`/`member` recibe 403); las LECTURAS van en `router` (`/model-prices`) abiertas a cualquier llamante autenticado (`get_principal`) sobre `get_tenant_session` (la RLS de lectura global de la migración 0049 deja que una sesión tenant lea todo el catálogo). Endpoints: `POST` create (abre el periodo vigente; 409 si ya existe periodo abierto para la clave vía el índice parcial-único `uq_model_prices_current`), `PATCH` update (campos mutables; la clave `provider/model_id/modality` es inmutable; patch vacío → 422), `DELETE` supersede (cierra el periodo `effective_to = now()` sin hard-delete, conserva el histórico para el snapshot de task_11_13; 409 si ya cerrado), `GET` list (filtros `provider/model_id/modality/current_only` + paginación `limit/offset` ge/le), `GET /{id}` y `GET /current` (devuelve el periodo abierto en vigor). **USD-canónico**: ningún endpoint acepta `currency` (el catálogo es USD-only; sin conversión de divisa inventada). `cached_input_price` opcional (prompt caching). Sin migración (la tabla la creó 11_11). Test `tests/integration/test_prices_endpoints.py` (16 casos): System Admin crea/actualiza/supersede; tenant_admin/member no pueden escribir (403); lectura OK para tenant user; filtros de list + límites de paginación; current-price devuelve la fila en vigor (y 404 tras supersede).
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_12_a
    description: "Endpoint CRUD del catálogo (solo System Admin)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_prices_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_13` — Snapshot del precio en cada model_call (campo price_snapshot_at)

- [x] **Título**: Snapshot del precio en cada model_call (campo price_snapshot_at)
  - El "model_call" de esta plataforma es un step `model_call` dentro del JSONB `executions.steps_log` (no hay tabla `model_calls` aparte). Snapshot puro+correcto histórico: `apps/api-server/src/api_server/db/price_snapshot.py` (dataclass inmutable `PriceSnapshot` + `compute_price_snapshot` puro + `lookup_current_price`/`snapshot_model_call` sobre el catálogo global) congela los precios unitarios vigentes (input/output/cached_input, **USD canónico**) + `price_snapshot_at` + un `cost_usd` computado de los tokens registrados (los `cached_input_tokens` se tarifan al rate cacheado, con fallback ~10% del input vía `cached_input_price_or_default()`). El seam de grabación (`execution_repo.snapshot_execution_prices`, cableado en `record_execution`/`finalize_execution`) enriquece cada step `model_call` con su `price_snapshot` congelado en JSONB y estampa columnas roll-up nullable/backfill-safe en `executions` (`price_snapshot_at`/`_currency`/`price_input_usd`/`price_output_usd`/`price_cached_input_usd`/`price_snapshot_cost_usd`). Precio ausente → snapshot tipado `unknown` (`available=False`, coste NULL) — nunca un cero/fake. `model_prices` es GLOBAL (lectura RLS global); `executions` sigue tenant-scoped y las columnas heredan su RLS. Migración reversible `0050_execution_price_snapshot` (`down_revision = 0049_model_prices`, up/down/up probado). Test `tests/integration/test_price_snapshot.py` (6 casos): snapshot+coste registrados, cambio posterior del catálogo NO altera el snapshot histórico, tokens cacheados al rate cacheado (+ fallback 10%), precio ausente = unknown no-cero, tenant-scoped (RLS preservada).
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_13_a
    description: "Snapshot del precio en cada model_call (campo price_snapshot_at)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_price_snapshot.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_14` — Pantalla 'Modelos & Precios' en menú System Admin con listado, filtros, edición manual, histórico, gráficas

- [x] **Título**: Pantalla 'Modelos & Precios' en menú System Admin con listado, filtros, edición manual, histórico, gráficas
  - Página `apps/admin-panel/app/admin/model-prices/page.tsx` en el menú del System Admin (entrada de nav nueva `systemAdminOnly` en `components/layout/admin-shell.tsx`, gateada por `isSystemAdmin`). Consume los endpoints de task_11_12 vía `lib/api.ts`: lectura abierta (`GET /model-prices` con filtros `provider/model_id/modality` + toggle `current_only`) y escrituras solo System Admin (`POST/PATCH/DELETE /admin/model-prices`) envueltas en `<RoleGuard min="system_admin">` (el backend gatea igualmente sobre la sesión BYPASSRLS). Listado en tabla + crear/editar/superseder en diálogo (la clave `provider/model_id/modality` es inmutable en edición; supersede cierra el periodo, no hard-delete). Histórico por modelo en diálogo (filas con vigencia `effective_from`/`effective_to`) + gráfica de precio-en-el-tiempo en SVG puro (input/output) — recharts NO está presente, así que se usa un sparkline ligero en vez de añadir una dependencia pesada (permitido por la nota de la tarea). **USD canónico** (sin campo de moneda en el wire) + **cached_input_price** (prompt caching) opcional. TS strict, sin `any`. E2E `apps/admin-panel/e2e/admin-models-prices.spec.ts` ESCRITO pero NO ejecutado (pendiente de verificación humana). `npm run typecheck`/`lint`/`build` en verde.
- **Tiempo estimado**: 14 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_11_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_14_a
    description: "Pantalla 'Modelos & Precios' en menú System Admin con listado, filtros, edición manual, histórico, gráficas"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/admin-models-prices.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Sincronización de Precios

#### `task_11_15` — Botón 'Sincronizar precios' que lee el JSON público de precios de LiteLLM

> **Nota (ADR 0021).** Esta tarea lee el JSON público de precios que
> mantiene la comunidad de LiteLLM
> (`model_prices_and_context_window.json`) como **fuente de datos** —
> no implica usar LiteLLM como runtime de proveedor. La plataforma
> opera con el catálogo cerrado de ADR 0021 (Claude SDK + Copilot +
> Azure Foundry APIM + Ollama).

- [x] **Título**: Botón 'Sincronizar precios' que lee el JSON público de precios de LiteLLM (data feed)
  - Servicio de sync `apps/api-server/src/api_server/pricing/litellm_sync.py` que lee el JSON comunitario de LiteLLM (`model_prices_and_context_window.json`) **sólo como fuente de datos** (ADR 0021 — NO añade `litellm` como dependencia ni como runtime; el catálogo cerrado de proveedores sigue intacto). Parseo puro: `parse_feed`/`map_entry` mapean cada entrada `{model_key: {input_cost_per_token, output_cost_per_token, cache_read_input_token_cost, litellm_provider, mode, max_input_tokens/max_tokens}}` a un `MappedPrice` (provider ← `litellm_provider`, model_id ← clave, modality ← `mode` vía `_MODE_TO_MODALITY` default text, precios **normalizados a USD per-1M** multiplicando el coste per-token por 1.000.000, `cached_input_price` ← `cache_read_input_token_cost`·1M nullable, `context_window`). `sample_spec` se ignora; entradas malformadas (sin provider, sin precio usable, no-objeto) se saltan con un `SkippedEntry` tipado (nunca crash). UPSERT con **effective-dating de Fase C**: clave nueva → INSERT periodo abierto (`created`, `source=litellm`); precio cambiado → CIERRA el periodo actual (`effective_to=now()`) + abre uno nuevo (`updated`); precio igual → no-op (`unchanged`, sin periodo nuevo). Una fila `source=manual` no se pisa salvo `overwrite_manual=True`. Guard de **subida >10%** (`LARGE_INCREASE_THRESHOLD`): una subida grande se DEFIERE (no se aplica) y se reporta bajo `large_increases` salvo `confirm_large_increases=True` (incluso programado) — base de task_11_16. Fetch tras un Protocol `PriceFeedFetcher` inyectable (`HttpxPriceFeedFetcher` con `httpx.AsyncClient` en prod, `StaticPriceFeedFetcher` en tests; **red mockeada, sin red real**). Endpoint `POST /admin/model-prices/sync` (router admin existente, gateado por `require_system_admin` sobre `get_admin_session` BYPASSRLS — un tenant_admin/member es 403) que devuelve un summary tipado (`PriceSyncResponse`: fetched/created/updated/unchanged/changed + skipped + large_increases). URL del feed configurable (`Settings.litellm_price_feed_url`) + override por llamada; fallo del feed → 502. SIN migración (la tabla la creó 11_11). Test `tests/integration/test_sync_prices_litellm.py` (8 casos, red MOCKEADA): el feed crea filas con `source=litellm` + normalización USD; precio igual = no-op (sin periodo nuevo); precio cambiado cierra/abre periodo; subida >10% diferida hasta confirmar; override manual no pisado; entradas malformadas saltadas con warning tipado; endpoint System-Admin sincroniza (MockTransport) y tenant_admin/member es 403.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_11_15_a
    description: "Botón 'Sincronizar precios' que lee LiteLLM upstream"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_sync_prices_litellm.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_16` — Diff visual + confirmación obligatoria si subida >10%

- [x] **Título**: Diff visual + confirmación obligatoria si subida >10%
  - Flujo de sync en dos pasos sobre el servicio de task_11_15 (ADR 0021 — el JSON de LiteLLM es **fuente de datos**, no runtime; sin dependencia `litellm`, red mockeada en tests). **(1) DRY-RUN** `compute_sync_diff(session, *, fetcher)` (`apps/api-server/src/api_server/pricing/litellm_sync.py`) lee + parsea el feed y lo compara contra el catálogo vigente devolviendo un diff por modelo (`PriceDiffRow`: provider/model_id/modality, `status` ∈ {added, updated, unchanged, increased, removed}, old vs new input/output/cached, `input_pct`/`output_pct`, `manual_skipped`) **sin escribir ninguna fila** — las filas abiertas que el feed ya no lista se marcan `removed` (candidato descontinuado, no se borra; base de task_11_17). **(2) APPLY** `apply_sync_from_litellm(..., confirm=False, overwrite_manual=False)` aplica con effective-dating de Fase C pero, a diferencia del defer parcial de 11_15, **rechaza el apply COMPLETO** lanzando `LargeIncreaseNotConfirmedError` (sin escribir nada) si ALGÚN precio sube > `LARGE_INCREASE_THRESHOLD` (+10%, constante nombrada) salvo `confirm=True`; con `confirm=True` aplica también las subidas. Endpoints System-Admin (BYPASSRLS `get_admin_session`, `require_system_admin` → tenant_admin/member 403): `POST /admin/model-prices/sync/diff` (dry-run, 200; nunca escribe; feed roto 502) y `POST /admin/model-prices/sync/apply` (409 con la lista de subidas si no confirmado; 200 al confirmar). Schemas en `schemas/price_sync.py` (`PriceSyncDiffRequest/Response`, `PriceDiffRowResponse`, `PriceSyncApplyRequest` con `confirm`). **Frontend**: botón "Sincronizar precios" en la pantalla Modelos & Precios (`apps/admin-panel/app/admin/model-prices/page.tsx`, `RoleGuard min="system_admin"`) que abre un diálogo con la vista de diff (tabla old→new + % de cambio por modelo, contadores por estado) y un **gate de confirmación explícito** (checkbox) que sólo aparece cuando `has_large_increase`, deshabilitando el botón Aplicar hasta marcarlo; el apply envía `confirm` sólo cuando hay subida y se ha confirmado. Test `tests/integration/test_prices_diff_confirm.py` (7 casos, red MOCKEADA): subida >10% sin confirm rechazada (servicio + endpoint 409, catálogo intacto), con confirm aplica, cambio <=10% aplica sin confirm, diff exacto (old/new/%), dry-run no escribe, modelos `removed` marcados, tenant 403. E2E `apps/admin-panel/e2e/prices-diff.spec.ts` ESCRITO pero NO ejecutado (pendiente de verificación humana). `npm run typecheck`/`lint`/`build` en verde.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_11_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_16_a
    description: "Diff visual + confirmación obligatoria si subida >10%"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/prices-diff.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_17` — Detección de modelos nuevos y descontinuados

- [x] **Título**: Detección de modelos nuevos y descontinuados
  - Detección NEW + DISCONTINUED sobre el servicio de sync (task_11_15/16; ADR 0021 — el JSON de LiteLLM es **fuente de datos**, sin dependencia `litellm`, red mockeada en tests). El núcleo es una clasificación **pura + determinista** `classify_models(mapped, open_rows)` (`apps/api-server/src/api_server/pricing/litellm_sync.py`) que, dadas las entradas mapeadas del feed y las filas vigentes (periodo abierto) del catálogo, etiqueta cada modelo `ModelStatus ∈ {new, discontinued, changed, unchanged}` sin tocar BD ni red: modelo en el feed sin periodo abierto → NEW; fila abierta cuya clave el feed ya no lista → DISCONTINUED (marcado, **nunca borrado** — su histórico + los snapshots de coste por llamada siguen válidos); precios distintos → CHANGED (con `input_pct`/`output_pct`); iguales → UNCHANGED. Devuelve un `ModelClassificationSet` con listas por estado (ordenadas determinísticamente por `provider/model_id/modality`) + contadores. El diff de task_11_16 (`SyncDiff`) aflora la vista de ciclo de vida (`new`==added, `discontinued`==removed, `changed`==updated+increased) y los endpoints/schemas (`PriceSyncDiffResponse`, `PriceSyncResponse`) exponen esos contadores + listas. Lado escritura opcional `discontinue_dropped_models(...)` + flag `discontinue_missing` en `apply_sync_from_litellm`/`POST /admin/model-prices/sync/apply`: CIERRA el periodo abierto de los modelos ausentes del feed (effective-dating de Fase C; las filas `source=manual` se respetan salvo `overwrite_manual`) — descontinuado = flagged, no borrado, así los snapshots históricos siguen válidos. SIN migración (reusa las columnas de vigencia de Fase C). Test `tests/unit/test_price_diff.py` (14 casos): feed con modelo ausente del catálogo → NEW; modelo del catálogo ausente del feed → DISCONTINUED (no borrado); precio cambiado → CHANGED con %; idéntico → UNCHANGED; pureza/determinismo (idempotente + independiente del orden). Integración añadida en `test_prices_diff_confirm.py` (3 casos): `discontinue_missing` cierra el periodo sin borrar la fila, el default no toca los descontinuados, y el diff aflora `discontinued`.
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_17_a
    description: "Detección de modelos nuevos y descontinuados"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_price_diff.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_18` — Sincronización programada (cron job) configurable

- [x] **Título**: Sincronización programada (cron job) configurable
  - Job de Celery Beat `workers.sync_model_prices` (`apps/workers/src/workers/price_sync.py`) que refresca el catálogo `model_prices` desde el JSON comunitario de LiteLLM de forma periódica — espeja el patrón de las tareas de mantenimiento (`workers/maintenance.py`: wrapper síncrono + core async que posee el ciclo de vida del engine, best-effort que nunca tumba beat). ADR 0021 — el JSON es **fuente de datos**, no runtime de proveedor; sin dependencia `litellm`, red mockeada en tests. **Cadencia CONFIGURABLE** (no hardcodeada): la entrada de beat se construye en `build_beat_schedule(settings)` (`workers/beat_schedule.py`, cableada en `celery_app.build_celery_app`) leyendo `Settings.price_sync_cron` (env `WORKERS_PRICE_SYNC_CRON`, default `0 4 * * *` = diario 04:00 UTC; un cron malformado degrada a ese default sin tumbar beat). **Palanca enable/disable en vivo**: el flag de plataforma `price_sync_enabled` (`platform_settings`, helper `get_price_sync_enabled`, escribible sólo por System Admin vía `set_platform_setting`) se lee al inicio de cada ejecución — si está OFF la corrida es no-op (ni fetch del feed ni escritura). Multi-tenancy: el catálogo es platform-global; el job escribe con el rol BYPASSRLS del worker (`WORKERS_DATABASE_URL`, el mismo grado-admin que ya usa para `executions`) — un tenant NO puede disparar ni programar el sync (el schedule vive en el beat de plataforma y el flag es platform-setting). **Guard de subida >10% incluso programado**: reutiliza la verja de task_11_16 llamando a `sync_prices_from_litellm(confirm_large_increases=False)` — un alza >10% se DEFIERE (no se escribe) y se registra en `large_increases` para confirmación manual desde el panel (`POST .../sync/apply` con `confirm=true`); los modelos descontinuados se marcan, no se borran (Fase C effective-dating). Fetch tras el `PriceFeedFetcher` inyectable de task_11_15 (red mockeada en tests). SIN migración (reusa la tabla de 11_11 + `platform_settings`). Test `tests/integration/test_scheduled_sync.py` (5 casos, red MOCKEADA): la tarea beat está registrada + la cadencia se lee de config (default 04:00 UTC + cron custom + fallback a malformado); una corrida programada aplica cambios seguros automáticamente; una subida >10% se retiene para confirmación manual (no auto-aplicada) + se registra; deshabilitar el schedule (`price_sync_enabled=false`) se respeta (no-op, sin fetch ni escritura).
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_11_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_18_a
    description: "Sincronización programada (cron job) configurable"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_scheduled_sync.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_19` — Audit log de cada sincronización (quién, qué cambió)

- [x] **Título**: Audit log de cada sincronización (quién, qué cambió)
  - Tabla append-only `price_sync_audit` (`apps/api-server/src/api_server/db/price_sync_audit.py`) — **platform-global** (sin `tenant_id`), espeja la RLS de `model_prices`: `ENABLE`+`FORCE` con una única política `price_sync_audit_global_read` `FOR SELECT USING (true)` y **ninguna política de escritura**, de modo que la sesión BYPASSRLS System-Admin/worker escribe y la sesión NOBYPASSRLS (tenant) sólo lee, lo que además hace la bitácora **append-only / inmutable** (espeja el endurecimiento de `marketplace_audit_entries` 0043). Cada sync (manual o programado) escribe UNA fila: `actor` (`user:<uuid>` para humano, `scheduler` para el cron), `actor_user_id` (FK `users` `ON DELETE SET NULL`), `trigger` (manual/scheduled), `source` (litellm), `feed_url`, contadores (fetched/created/updated/unchanged/discontinued/skipped), `held_large_increases` (spikes >10% retenidos), `confirmed`, y un `diff` JSONB compacto (spikes retenidos + claves descontinuadas + entradas saltadas). Helper `pricing/sync_audit.py` (`write_sync_audit`/`audit_actor`/`build_diff`) cableado en la MISMA transacción que las escrituras del catálogo en `POST /admin/model-prices/sync`, `.../sync/apply` (un apply rechazado por spike no confirmado lanza antes de escribir → ni catálogo ni audit: nada se aplica en silencio sin rastro) y en el job de beat `workers.sync_model_prices` (atribuido al `scheduler`). Endpoint `GET /admin/model-prices/sync/audit` (System-Admin, filtro `trigger` + paginación) + schema `schemas/price_sync_audit.py` para alimentar el histórico de la pantalla Modelos & Precios. Migración reversible `0051_price_sync_audit` (`down_revision = 0050_execution_price_snapshot`, head único; up/down a `0040_sso_email_domains`/up probado). ADR 0021 — JSON de LiteLLM como fuente de datos (sin dep `litellm`, red mockeada). Test `tests/integration/test_sync_audit.py` (6 casos, red MOCKEADA): sync manual escribe fila con actor+contadores+diff; sync con spike retenido audita igual (el resto se aplicó); apply rechazado no escribe audit ni catálogo, confirmado sí; sync programado atribuido al scheduler; append-only (tenant lee pero INSERT denegado/UPDATE/DELETE a cero filas); endpoint de histórico aflora filas (System Admin) y 403 a tenant.
- **Tiempo estimado**: 3 h
- **Complejidad**: xs
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_19_a
    description: "Audit log de cada sincronización (quién, qué cambió)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_sync_audit.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Observabilidad de Guardrails y Cierre

#### `task_11_20` — Tabla guardrail_events + dashboard del tenant

- [x] **Título**: Tabla guardrail_events + dashboard del tenant
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_11_20_a
    description: "Tabla guardrail_events + dashboard del tenant"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/guardrails-dashboard.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_21` — Alertas configurables (X violaciones/hora dispara alerta)

- [x] **Título**: Alertas configurables (X violaciones/hora dispara alerta)
  - Regla de alerta de guardrails **configurable por tenant** (`apps/api-server/src/api_server/db/guardrail_alert_rule.py`, tabla `guardrail_alert_rules` **tenant-scoped + RLS** FOR-ALL canónica, migración reversible `0053_guardrail_alert_rules` con `down_revision = 0052_guardrail_events`, head único; up/down a `0040_sso_email_domains`/up probado en BD scratch). Config SIN números mágicos: `threshold` + `window_seconds` (defaults vía constantes nombradas `DEFAULT_THRESHOLD`/`DEFAULT_WINDOW_SECONDS`, bounds `MIN/MAX_WINDOW_SECONDS` → 422 limpio) + scoping opcional `guardrail_type` y/o `min_severity` (escala ordenada info<low<medium<high<critical). Evaluador `apps/api-server/src/api_server/guardrails/alerts.py` (`evaluate_tenant_alert_rules`/`maybe_alert_after_events`) que, sobre la sesión tenant-scoped RLS, cuenta los `guardrail_events` que casan en la ventana deslizante y, si cruza el umbral, **dispara UNA alerta a través del sistema de notificaciones de Plan 10** (evento `guardrail_alert` → `notification_dispatcher.dispatch_event` vía nuevo productor `celery_client.enqueue_event_dispatch`; registro en `EVENT_REGISTRY` lane priority + plantillas builtin ES/EN; el dispatcher resuelve los canales de los Tenant Admins — NO un notificador paralelo). **DEBOUNCE** (`last_fired_at`): una brecha sostenida produce como máximo UNA alerta por regla por ventana (se suprime hasta que pasa la ventana). Cableado en el recorder `record_pipeline_decision` (best-effort, nunca rompe la grabación del evento). **RBAC**: CRUD `/guardrails/alert-rules` gateado a `tenant_admin` (`require_tenant_admin` → member 403) sobre `get_tenant_session`, soft-delete; un admin NO puede tocar reglas de otro tenant (404 por RLS). Multi-tenancy: las violaciones del tenant A NUNCA alertan al tenant B (`@pytest.mark.cross_tenant`). Test `tests/integration/test_guardrail_alerts.py` (11 casos): cruzar el umbral dispara exactamente UNA alerta vía Plan 10 (dispatcher fake que sustituye el envío real al canal); por debajo no dispara; umbral/ventana configurables (regla custom dispara a su propio umbral; ventana respeta eventos fuera de rango); scoping tipo+severidad; debounce suprime la segunda alerta en la misma ventana; el dispatcher por defecto encola `dispatch_event` por nombre (Plan 10); aislamiento por tenant; RBAC member 403 + admin CRUD + cross-tenant 404.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_11_20`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_21_a
    description: "Alertas configurables (X violaciones/hora dispara alerta)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrail_alerts.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_22` — Guardrails específicos del chat de planning (topic adherence, hallucination check, validación estructural)

- [x] **Título**: Guardrails específicos del chat de planning (topic adherence, hallucination check, validación estructural)
  - Cableado del motor de guardrails (Fase A/B) en la ruta del chat de planning de Plan 03 (`apps/api-server/src/api_server/guardrails/planning.py`) con tres guardrails específicos que **reutilizan** los built-ins existentes (ningún check nuevo): (1) **topic adherence** vía `topic_restriction` con un set de temas de planning/proyecto (es+en) corrido en `pre_llm` (¿el humano desvía el chat?) + `post_llm` (¿la respuesta del equipo se va de tema?), acción `warn`; (2) **hallucination check sobre NÚMEROS** vía `factuality_citations` (`require_document_citation=True`) en `post_llm` — estimaciones/costes/fechas afirmadas sin cita de soporte se marcan, acción `warn`; (3) **gate estructural antes de "Generar Plan"** vía `output_structure` (JSON-Schema `PLAN_DRAFT_SCHEMA` que exige `summary` no vacío + ≥1 `task` con `id`/`title` string) corrido como hook `post_llm` sobre el borrador serializado, acción `block` — un borrador estructuralmente inválido BLOQUEA la generación y devuelve feedback accionable (los errores de schema + su path JSON, p.ej. `tasks[0]: 'title' is a required property`). Funciones host: `build_planning_chat_pipeline`/`build_plan_structure_pipeline` (configs declarativas), `run_planning_chat_guardrails(...)` (corre el pipeline de turno y persiste vía recorder 11_20) y `gate_generate_plan(...) -> PlanGateResult(allowed, feedback, decision)`. Cada guardrail disparado se persiste como una fila `guardrail_events` **tenant-scoped + RLS** (recorder de task_11_20, detalle ENMASCARADO — nunca PII/secreto en crudo) con `agent_label` `planning_chat`/`plan_generation` (el chat dispara antes de que exista una execution). LLM MOCKEADO en tests (los guardrails son puros: heurística + JSON-Schema, el texto del turno se pasa directo). Test `tests/integration/test_planning_guardrails.py` (8 casos): input off-topic marcado/warned; número sin soporte dispara el hallucination check; borrador inválido bloquea "Generar Plan" con feedback accionable; borrador válido on-topic pasa sin evento; tenant-scoped (`@pytest.mark.cross_tenant`).
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_11_21`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_22_a
    description: "Guardrails específicos del chat de planning (topic adherence, hallucination check, validación estructural)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_planning_guardrails.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_11_23` — Documentación + ADRs + changelog

- [x] **Título**: Documentación + ADRs + changelog
  - Entrada de changelog `docs/07-changelog/11-guardrails-precios.md` (estilo de la casa de Plan 10) con Resumen, Cambios por tarea (11_01..11_23 agrupados por fase), tablas de Endpoints nuevos + Migraciones (0049 model_prices / 0050 snapshot / 0051 price_sync_audit / 0052 guardrail_events) + paquete `packages/shared-guardrails` + extras opcionales (pii=Presidio, content-safety) + tunables, Decisiones y Pendiente. **ADR 0035** (motor declarativo en capas con baseline bloqueable + eventos tenant-scoped enmascarados + catálogo USD-canónico con snapshot effective-dated + LiteLLM-JSON como fuente de datos reafirmando ADR 0021 + gate de confirmación >10%). Referencias `docs/04-reference/guardrails.md` + `docs/04-reference/pricing.md` (4 hooks, 12 tipos, 6 acciones, config en capas bloqueable, guardrail_events/dashboard; catálogo + sync + snapshot + RBAC). **Flagea explícitamente** el hueco de alcance Budgets/exchange_rates/display_currency (sin tarea numerada, NO implementado) y el estado real de Fase E (11_20 implementado pero sin commitear; 11_21 alertas NO implementadas). Solo docs. `pre-commit` (prettier/markdown) en verde.
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_11_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_11_23_a
    description: "Documentación + ADRs + changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/11-guardrails-precios.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_11_01
  description: "PII se enmascara antes de logs y antes de LLM externos"
  hint: "Mensaje con DNI, email, IBAN en chat de planning"
  checklist:
    - "Logs muestran datos enmascarados"
    - "El LLM externo recibe versión enmascarada"
    - "La UI sigue mostrando original al usuario"
    - "Audit log refleja la enmascaración"

- id: human_11_02
  description: "Secret leakage bloquea exposición accidental"
  hint: "Agente genera código con un token hardcodeado (intencional para test)"
  checklist:
    - "El guardrail post_llm detecta el patrón"
    - "La respuesta se redacta sustituyendo el token por marcador"
    - "Alerta al admin con severity high"

- id: human_11_03
  description: "Cost ceiling aborta ejecuciones caras"
  hint: "Tarea con budget 1€ que intenta usar 2€ de tokens"
  checklist:
    - "La siguiente llamada al LLM falla con budget_exceeded"
    - "Mensaje claro al equipo sobre el límite"
    - "La tarea queda en blocked con motivo explícito"

- id: human_11_04
  description: "Sincronización de precios funciona"
  hint: "Pulsar 'Sincronizar precios' tras haber editado un precio manualmente"
  checklist:
    - "El sistema muestra diff entre catálogo actual y upstream"
    - "Si subida >10%, requiere confirmación explícita"
    - "Tras aplicar, los nuevos cálculos de coste reflejan precios actualizados"
    - "Audit log muestra qué cambió, quién lo hizo, desde dónde"
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

Tras cerrar este plan, el siguiente es **Plan 12** (`12-backup-restore.md`).
