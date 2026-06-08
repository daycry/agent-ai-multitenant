---
adr_id: "0053"
title: "Selección de modelo LLM del asistente personal (default plataforma → override tenant, catálogo cerrado)"
status: accepted
date: 2026-06-08
authors: [system_architect]
plan_referenced: 10-asistente-personal
docs_language: es
---

# ADR 0053 — Selección de modelo LLM del asistente personal

> **Estado: `accepted`** (aprobado por el operador 2026-06-08).
> Cierra el cableado LLM del asistente personal que el Plan 10 (task_10_14) dejó como _seam_ (un
> `get_assistant_model` que devolvía 503: _"the provider selection lands with the broader LLM wiring"_).

## Contexto

El asistente personal (ADR 0033 — vive en el api-server reutilizando el sub-grafo de chat) tenía la
identidad configurable por tenant (nombre, tono, idioma, tools) pero **ningún punto de configuración de
modelo/provider**. La resolución del modelo (`routers/assistant.py::get_assistant_model`) era un _seam_
que lanzaba **503** a propósito; los tests inyectan un `ScriptedAssistantModel`, así que nunca se
contactaba un provider real.

Las piezas de plataforma ya existían pero no estaban conectadas al asistente:

- `llm_providers` (ADR 0028, migración 0070): catálogo **platform-global** de los cuatro caminos del ADR
  0021 (`claude_sdk`, `copilot`, `azure_foundry`, `ollama`); sin `tenant_id`, sin RLS, solo `system_admin`
  (BYPASSRLS) lo lee/escribe; las credenciales viven en Vault (`secret_vault_path`).
- `model_prices` (migración 0049/0071): catálogo de precios `(provider_family, model_id)`, lectura global,
  con `provider_id` opcional ligado a una fila `llm_providers`.
- `resolve_provider_config` (api-server, ADR 0028): resuelve `kind → base_url + secret(Vault)`, pero **no
  construye** un `LLMProvider` concreto. La factory `kind → provider` solo existía en el runtime
  (`agent_runtime.providers`), **no importable desde el api-server** (acoplamiento deliberadamente evitado).

Faltaban por tanto: (1) una factory `kind → LLMProvider` en el api-server, (2) persistir la elección de
modelo, (3) resolverla con herencia, (4) exponerla en la UI.

## Opciones consideradas

**Jerarquía de resolución:**

- **R-A. Default plataforma → override por tenant.** Coherente con el criterio "modelo heredable" del
  proyecto (default plataforma→proyecto→agente + override). El asistente no tiene capa proyecto/agente, así
  que el mapeo natural es plataforma→tenant. ✅ Multi-tenant; ✅ un default operativo para todos sin
  configurar cada tenant. ❌ Dos puntos de configuración (System Admin + Tenant Admin).
- **R-B. Solo por tenant.** Cada Tenant Admin elige obligatoriamente. ✅ Simple. ❌ El asistente no funciona
  hasta que cada tenant configura; sin default.
- **R-C. Solo plataforma (global).** Una elección global. ✅ La más simple. ❌ Pierde flexibilidad
  multi-tenant.

**Origen/validación del modelo:**

- **M-A. Provider + modelo del catálogo** (`model_prices`), validado: dropdown de providers activos +
  dropdown de modelos. ✅ Sin typos; ✅ respeta el catálogo cerrado del ADR 0021. ❌ Un Ollama sin precios
  asociados sale con lista vacía (hay que catalogar sus modelos).
- **M-B. Provider + `model_id` texto libre.** ✅ Flexible. ❌ Permite errores y modelos inexistentes.

## Decisión

**R-A (default plataforma → override tenant) + M-A (catálogo cerrado validado).**

1. **Persistencia (sin migración — tablas genéricas existentes):**
   - Override por tenant → `tenant_settings` `category='assistant'`, **`key='model'`**, valor
     `{"provider_id": "<uuid>", "model_id": "<str>"}`. Separado de `key='identity'` para no acoplar persona
     con modelo.
   - Default de plataforma → `platform_settings` `key='assistant.default_model'`, mismo shape. Lo escribe
     solo `system_admin` (`set_platform_setting`).
2. **Resolución** (`assistant/model_config.py::resolve_assistant_model`, sobre la sesión BYPASSRLS abierta
   internamente): override del tenant si existe **y** es válido → si no, default de plataforma si es válido
   → si no, `None` → el chat devuelve **503** con mensaje claro (se preserva el comportamiento seguro: nunca
   se fabrica respuesta sin provider).
3. **Validez** = el `provider_id` apunta a una fila `llm_providers` **activa** Y el `model_id` está en el
   catálogo vigente del provider. El catálogo de un provider = filas `model_prices` abiertas
   (`effective_to IS NULL`) cuya `provider_id` == la fila, **o** cuya familia (`provider`) está en el mapa
   `kind → familias LiteLLM` (`claude_sdk→anthropic`, `azure_foundry→azure/azure_ai/openai`,
   `copilot→openai/anthropic`; `ollama` sale solo de la asociación `provider_id` manual).
4. **Factory `kind → LLMProvider`** (`llm_providers/factory.py`): lee la fila + `resolve_provider_config`
   (base_url + secret Vault) y mapea `kind` a la clase concreta de `shared_llm.providers`
   (`ClaudeAgentProvider` / `CopilotProvider` / `AzureFoundryAPIMProvider` / `OllamaProvider`). **Import
   perezoso por kind**; `ImportError` (SDK opcional ausente) o credencial/endpoint faltante ⇒ `None` ⇒ 503.
   Para `azure_foundry` el `model_id` se pasa como `deployment` (la URL APIM fija el modelo en el
   constructor, no por llamada).
5. **API** (router `/assistant`): Tenant-Admin → `GET/PUT /assistant/model` (override; PUT valida y permite
   limpiar→heredar), `GET /assistant/model/options` (providers activos + sus modelos, sin secretos);
   System-Admin → `GET/PUT /assistant/default-model` (default de plataforma, validado).
6. **UI**: sección "Modelo LLM" en `/admin/assistant/settings` (dropdown provider + modelo, "volver al
   default") y control del default de plataforma para el System Admin.

Razones: la herencia plataforma→tenant es el patrón de modelo del proyecto y da un default operativo sin
forzar a cada tenant; el catálogo cerrado evita typos y respeta el ADR 0021; la factory vive en el
api-server (con import perezoso) para no acoplar el api-server a la cadena pesada del runtime/Claude-SDK; el
503 conservador mantiene la garantía de "nunca inventar respuesta sin provider".

## Consecuencias

**Mejora:** el asistente personal responde de verdad end-to-end; el Tenant Admin elige su modelo desde la
UI; el System Admin fija un default heredable; las credenciales siguen solo en Vault (la elección persiste
solo `provider_id` + `model_id`, nunca un secreto).

**Complejidad:** nueva factory en el api-server + import perezoso de los SDK opcionales; resolución con
sesión BYPASSRLS abierta dentro de un endpoint Tenant-Admin (necesaria porque `llm_providers` no es legible
por `app_user`, ADR 0028) — se usa solo para construir el provider server-side, nunca se expone al tenant.

**Trade-offs:** un Ollama sin `model_prices` asociados aparece sin modelos seleccionables (consecuencia
honesta del catálogo cerrado; el System Admin cataloga sus modelos). Dos puntos de configuración
(plataforma + tenant) a cambio de la flexibilidad multi-tenant.

## Riesgos

| Riesgo                                                            | Prob. | Impacto | Mitigación                                                                                                |
| ----------------------------------------------------------------- | ----- | ------- | --------------------------------------------------------------------------------------------------------- |
| Selección apunta a provider desactivado/borrado o modelo retirado | Media | Medio   | Validez se re-comprueba en cada resolución; si deja de ser válida → cae al default→503                    |
| SDK opcional del provider ausente en la imagen api-server         | Media | Medio   | Import perezoso por kind; `ImportError` → `None` → 503 claro (no crash)                                   |
| Un tenant ve config de provider de plataforma                     | Baja  | Alto    | La sesión BYPASSRLS solo construye el provider server-side; nada de `llm_providers` se devuelve al tenant |
| Catálogo `model_prices` sin asociar a provider                    | Media | Bajo    | Unión por `provider_id` **o** familia (kind→familias); Ollama documentado como caso a catalogar           |

## Alternativas rechazadas

R-B (solo tenant) por no tener default operativo; R-C (solo plataforma) por perder la flexibilidad
multi-tenant; M-B (texto libre) por permitir modelos inexistentes / typos, en contra del catálogo cerrado
del ADR 0021.

## Trazabilidad

- Roadmap: `docs/roadmap/10-asistente-personal.md` (cierra el seam de `task_10_14`).
- Backend: `apps/api-server/src/api_server/assistant/model_config.py`,
  `apps/api-server/src/api_server/llm_providers/factory.py`,
  `apps/api-server/src/api_server/routers/assistant.py`,
  `apps/api-server/src/api_server/schemas/assistant.py`.
- Frontend: `apps/admin-panel/app/admin/assistant/settings/page.tsx`.
- ADRs relacionados: 0021 (catálogo LLM cerrado), 0028 (`llm_providers` platform-global + precedencia
  DB>env), 0033 (asistente en api-server).
