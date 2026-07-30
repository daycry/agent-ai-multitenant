---
name: model-per-agent-inheritance
description: Decisión del operador sobre cómo elegir el modelo LLM por agente — jerarquía heredable + override.
metadata:
  node_type: memory
  type: project
  originSessionId: f7e54214-9978-4552-9197-70ecc3f15b3d
---

El operador decidió (2026-06-03) que la elección de **modelo LLM por agente** funcione como
**default heredable + override**: default global de plataforma → (opcional) default por
proyecto/tenant → **override opcional por agente**; siempre validado contra el catálogo cerrado
**ADR 0021** (`claude_sdk` / `copilot` / `azure_foundry` / `ollama`).

**Why:** evita configurar cada agente uno a uno y permite afinar por rol (p. ej. Arquitecto/Reviewer
en modelo potente, ejecutores en uno más rápido/barato). Hoy no hay UI y un agente creado por UI nace
con `model_config={}` → dispatcharía un spec de modelo vacío (fallo funcional latente).

**How to apply:** materializar en el **ADR 0055** y `task_06_17_10`/`task_06_17_11` del plan 06.17
(validación de `model_config` + selector provider/modelo/temperatura + default operator-configurable
vía `platform_settings`, no hardcode). Aplica también al equipo built-in [[codeigniter4-builtin-team]].
