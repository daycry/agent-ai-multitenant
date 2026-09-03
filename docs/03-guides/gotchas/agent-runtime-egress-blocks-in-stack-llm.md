---
title: "El agent-runtime no llega al LLM in-stack (egress-proxy lo bloquea)"
tags: [egress, ollama, agent-runtime, tinyproxy, sandbox]
---

# El agent-runtime no llega al LLM in-stack (egress-proxy lo bloquea)

## Síntoma

Una ejecución real falla nada más empezar (iterations=0) con:

```
agent-runtime error: ReadError:
```

…cuando el agente está configurado contra un **Ollama in-stack** (p.ej.
`base_url=http://ollama:11434/v1`, modelo `llama3.2:1b`). El worker resuelve
bien el modelo (`workers.model_resolved kind=ollama base_url=http://ollama:11434/v1`),
así que el fallo NO es de resolución de provider: es la primera llamada HTTP al
modelo la que se corta.

## Causa raíz

El `agent-runtime` enruta **todo** su tráfico HTTP saliente por el
`egress-proxy` (tinyproxy) vía `HTTP(S)_PROXY` — es el límite de seguridad del
sandbox (ADR 0019). tinyproxy corre con `FilterDefaultDeny Yes`: si el `Host:`
del destino no casa ningún regex del allowlist (`docker/egress-proxy/filter.txt`),
la conexión se rechaza → el cliente ve un `ReadError`/reset.

El allowlist traía solo los proveedores LLM **externos** (`api.anthropic.com`,
`ollama.com`, …). El host **interno** `ollama` no estaba, porque el comentario
del fichero asumía que un Ollama in-stack se alcanza **directo** por la red
`agentic-agents` (sin proxy). Pero el agent-runtime proxifica TODO, también la
llamada al `ollama` interno → bloqueada.

## Fix

Permitir el host interno en `docker/egress-proxy/filter.txt` y reconstruir el
egress-proxy:

```
^ollama(:[0-9]+)?$
```

```bash
docker compose ... build egress-proxy && docker compose ... up -d --force-recreate egress-proxy
```

Es seguro: `ollama` es un nombre de servicio interno, no un dominio de
exfiltración. Aplica igual en prod si el operador despliega un Ollama in-stack
con ese nombre.

> **Si el host que falta es el de un MCP remoto** (`mcp.atlassian.com` y
> compañía), no se edita este fichero a mano: hay un ajuste de plataforma y un
> procedimiento con su paso de aplicación y su comprobación contra el proxy —
> [egress-mcp-allowlist.md](../../06-runbooks/egress-mcp-allowlist.md) (ADR 0165).

> Alternativa más limpia (no aplicada aún): que el agent-runtime lleve
> `NO_PROXY=ollama` para alcanzar el LLM in-stack directo y no gastar allowlist
> del egress. Si se hace, documentarlo aquí.

## Cómo verificarlo

Lanza una tarea `ready` asignada a un agente con `model_config.provider=ollama`
y observa la ejecución: debe pasar a `done` con `iterations>=1` y el output debe
ser la respuesta real del modelo (no un `ReadError`).
