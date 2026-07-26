---
title: "Hay DOS vías de resolver un proveedor LLM (por `provider_id` y por `kind`) y mezclarlas trae los modelos del proveedor equivocado"
area: llm-providers, api-server, orchestrator
encountered: 2026-06-20
stack: shared-llm (ADR 0021/0082), llm_providers
---

## Síntoma

Se pulsa «Sincronizar modelos» sobre el proveedor `ollama-cloud` y la lista que
llega es la de `ollama-local`. Igual con «Probar conexión»: contesta el proveedor
que no es. Nada falla ni loguea error — simplemente responde otro.

## Causa raíz

El catálogo `llm_providers` admite **varias filas del mismo `kind`**
(`ollama-local` + `ollama-cloud`; para eso existe la columna `slug`). Y hay dos
resolvedores distintos, con semánticas distintas:

| Vía                                   | Qué resuelve                                     | Quién la usa                                                         |
| ------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------- |
| **Por `provider_id`** (fila concreta) | esa fila: su `base_url` y su `secret_vault_path` | `sync-models`, `test-connection`, el asistente, el córtex, los evals |
| **Por `kind`** (el más nuevo ACTIVO)  | `rows[0]` de ese kind                            | el **dispatch de agentes** (`model_config.provider` es un kind)      |

El bug del PR #46 fue exactamente esto: `build_llm_provider(provider_id)` resolvía
internamente **por kind**, así que sincronizar la fila cloud traía los modelos de
la local por ser más nueva y estar activa.

## Fix

Una operación que el operador dirigió a **un proveedor concreto** debe usar esa
fila, nunca el resolver por kind. El resolver por kind es solo para el dispatch,
donde `model_config.provider` es genuinamente un kind y la política «el más nuevo
activo» es la deseada.

Consecuencia operativa que sorprende en dev: si `ollama-local` queda activo y es
más nuevo, **los agentes resolverán a local** aunque el asistente use cloud. Para
que los agentes usen cloud hay que desactivar la local o fijar el proveedor en el
agente (ADR 0082 permite `provider_id` por agente).

## Cómo verificar el fix

Con dos filas del mismo kind activas, `POST /llm-providers/{id}/sync-models` sobre
la MENOS reciente devuelve los modelos de esa, no los de la otra.
