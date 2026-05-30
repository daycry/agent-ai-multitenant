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

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `11-guardrails-precios`                   |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `02-ejecucion-agentes`                    |
| **Tiempo estimado (calendario)**   | 3-4 semanas                               |
| **Tiempo estimado (persona-días)** | 60-80                                     |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 150 € – 240 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/11-guardrails-precios`              |
| **Secciones del .docx**            | [19.5, 30.8]                              |

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

- [ ] **Título**: Modelo model_prices con todos los campos (provider, model_id, modality, prices, currency, vigencia, source, etc.)
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

- [ ] **Título**: Migración + RLS (es global, accesible a System Admin)
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

- [ ] **Título**: Endpoint CRUD del catálogo (solo System Admin)
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

- [ ] **Título**: Snapshot del precio en cada model_call (campo price_snapshot_at)
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

- [ ] **Título**: Pantalla 'Modelos & Precios' en menú System Admin con listado, filtros, edición manual, histórico, gráficas
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

- [ ] **Título**: Botón 'Sincronizar precios' que lee el JSON público de precios de LiteLLM (data feed)
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

- [ ] **Título**: Diff visual + confirmación obligatoria si subida >10%
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

- [ ] **Título**: Detección de modelos nuevos y descontinuados
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

- [ ] **Título**: Sincronización programada (cron job) configurable
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

- [ ] **Título**: Audit log de cada sincronización (quién, qué cambió)
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

- [ ] **Título**: Tabla guardrail_events + dashboard del tenant
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

- [ ] **Título**: Alertas configurables (X violaciones/hora dispara alerta)
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

- [ ] **Título**: Guardrails específicos del chat de planning (topic adherence, hallucination check, validación estructural)
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

- [ ] **Título**: Documentación + ADRs + changelog
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
