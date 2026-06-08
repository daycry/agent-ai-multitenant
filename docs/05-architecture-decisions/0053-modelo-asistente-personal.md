---
adr_id: "0053"
title: "Selección de modelo LLM del asistente personal (default plataforma → override tenant, catálogo + sync)"
status: accepted
date: 2026-06-08
authors: [system_architect]
plan_referenced: 10-asistente-personal
docs_language: es
---

# ADR 0053 — Selección de modelo LLM del asistente personal

> **Estado: `accepted`** (aprobado por el operador 2026-06-08).
> Cierra el cableado LLM del asistente personal que el Plan 10 (`task_10_14`) dejó como _seam_ (un
> `get_assistant_model` que devolvía 503: "the provider selection lands with the broader LLM wiring").

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
  dropdown de modelos. ✅ Sin typos; ✅ respeta el catálogo cerrado del ADR 0021. ❌ El catálogo LiteLLM no
  conoce los modelos de Ollama **Cloud** (`glm-5.1`, `gpt-oss:120b`, …) — solo claves locales —, así que el
  desplegable saldría incompleto.
- **M-B. Provider + `model_id` texto libre.** ✅ Flexible. ❌ Permite errores y modelos inexistentes.
- **M-C. Catálogo + descubrimiento sincronizado.** El catálogo de precios **más** los modelos que el
  provider sirve de verdad, descubiertos **bajo demanda** (no en cada apertura). ✅ Sin typos y refleja la
  oferta real; ✅ una sola llamada de red al sincronizar. ❌ Hay que sincronizar al cambiar la oferta.

## Decisión

**R-A (default plataforma → override tenant) + M-C (catálogo + descubrimiento sincronizado).**

> Nota: la primera versión usó M-A (catálogo cerrado puro), pero el catálogo LiteLLM no lista los modelos de
> Ollama Cloud, así que el desplegable salía vacío de modelos reales. Se amplió a M-C: descubrimiento bajo
> demanda persistido, **sin** llamadas de red en el camino caliente del chat (decisión del operador:
> "sincronizar, no llamar cada vez").

1. **Persistencia (sin migración — tablas genéricas existentes):**
   - Override por tenant → `tenant_settings` `category='assistant'`, **`key='model'`**, valor
     `{"provider_id": "<uuid>", "model_id": "<str>"}`. Separado de `key='identity'` para no acoplar persona
     con modelo.
   - Default de plataforma → `platform_settings` `key='assistant.default_model'`, mismo shape. Lo escribe
     solo `system_admin` (`set_platform_setting`).
   - Modelos descubiertos → `llm_providers.config["models"]` (JSONB no-secreto), poblado por el sync.
2. **Resolución** (`assistant/model_config.py::resolve_assistant_model`, sobre la sesión BYPASSRLS abierta
   internamente): override del tenant si su provider sigue **activo** → si no, default de plataforma (igual)
   → si no, `None` → 503. **NO re-valida el `model_id` en cada chat** (eso se hizo al guardar; revalidar
   metería una llamada de red por mensaje y rechazaría un modelo descubierto-en-vivo). Un `model_id` ya
   inválido aflora como el error manejado del propio provider (502), no como un fall-through silencioso.
3. **Modelos seleccionables / validez (al GUARDAR, no en caliente)** = `provider` **activo** Y `model_id`
   en `model_prices` vigentes (catálogo, vía `provider_id` o familia `kind → familias LiteLLM`) **∪**
   `config.models` (lo sincronizado). Todo desde la BD, sin red.
4. **Sync bajo demanda** (`POST /admin/llm-providers/{id}/sync-models`, System-Admin): llama **una vez** al
   `/v1/models` del provider (`factory.list_provider_models`, best-effort → `[]` si no hay API/falla) y
   persiste el resultado en `config.models`. Es el **único** punto que toca la red del provider para
   listar.
5. **Factory `kind → LLMProvider`** (`llm_providers/factory.py`): lee la fila + `resolve_provider_config`
   (base_url + secret Vault) y mapea `kind` a la clase concreta de `shared_llm.providers`
   (`ClaudeAgentProvider` / `CopilotProvider` / `AzureFoundryAPIMProvider` / `OllamaProvider`). **Import
   perezoso por kind**; `ImportError` (SDK opcional ausente) o credencial/endpoint faltante ⇒ `None` ⇒ 503.
   El `model_id` del catálogo puede venir LiteLLM-keyed (`ollama/llama3.1`); `to_provider_model_name` quita
   el prefijo de familia antes de llamar (la API quiere el nombre desnudo). Para `azure_foundry` el modelo
   se pasa como `deployment` (la URL APIM fija el modelo en el constructor, no por llamada).
6. **Errores del provider en el chat**: `run_assistant_turn` se envuelve y los `shared_llm.LLMError` se
   mapean a `HTTPException` manejada (`AuthError`→502, `RateLimitError`→429, resto→502). Importa porque una
   500 **sin manejar** sale por fuera del `CORSMiddleware` (sin cabecera CORS) y el navegador la ve como un
   opaco `TypeError: Failed to fetch`; la manejada pasa por CORS y muestra el motivo.
7. **API** (router `/assistant`): Tenant-Admin → `GET/PUT /assistant/model` (override; PUT valida y permite
   limpiar→heredar), `GET /assistant/model/options`; System-Admin → `GET/PUT /assistant/default-model`
   (+`/options`). El sync vive en el router de providers (`/admin/llm-providers/{id}/sync-models`).
8. **UI**: sección "Modelo LLM" en `/admin/assistant/settings` (dropdown provider + modelo, "volver al
   default", y botón "Sincronizar modelos" para System Admin) y control del default de plataforma.

Razones: la herencia plataforma→tenant es el patrón de modelo del proyecto y da un default operativo sin
forzar a cada tenant; el descubrimiento sincronizado refleja la oferta real (clave para Ollama Cloud) sin
meter latencia de red en cada chat ni en cada apertura del desplegable; la factory vive en el api-server
(con import perezoso) para no acoplar el api-server a la cadena pesada del runtime/Claude-SDK; el 503/502
conservador mantiene la garantía de "nunca inventar respuesta sin provider".

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
