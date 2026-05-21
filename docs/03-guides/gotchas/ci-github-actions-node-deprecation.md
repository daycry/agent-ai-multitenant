---
title: GitHub Actions deprecará Node 20 — sube las acciones a Node 24-ready
area: ci
encountered: 2026-05-21
stack: GitHub Actions runners, actions/checkout, actions/setup-*
---

## Síntoma

En cada job del workflow CI:

```
Node.js 20 actions are deprecated. The following actions are running
on Node.js 20 and may not work as expected: actions/checkout@v4.
Actions will be forced to run with Node.js 24 by default starting
June 2nd, 2026. Node.js 20 will be removed from the runner on
September 16th, 2026.
```

No es un fallo del run, solo un warning. Pero la fecha de
remoción es real: a partir del 16-sep-2026, esos workflows
**dejarán de correr**.

## Causa raíz

GitHub Actions migró su runtime JS de Node 20 a Node 24 durante 2025. Las versiones mayores de cada acción usan internamente la
nueva runtime:

| Acción                       | v Node 20 | v Node 24      |
| ---------------------------- | --------- | -------------- |
| `actions/checkout`           | v4        | v5             |
| `actions/setup-python`       | v5        | v6 (sept 2025) |
| `actions/setup-node`         | v4        | v5 (sept 2025) |
| `docker/setup-buildx-action` | v3        | v4 (oct 2025)  |

GitHub solo inspecciona las **versiones explícitamente declaradas
en tu workflow**; por eso el warning lista solo las que has
"pinneado" a una mayor antigua.

## Fix

Sube cada acción a su `vN+1` (mismas APIs, sin breaking changes
visibles para nuestros usos).

```yaml
# Antes:
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
- uses: actions/setup-node@v4
- uses: docker/setup-buildx-action@v3

# Después:
- uses: actions/checkout@v5
- uses: actions/setup-python@v6 # si está disponible cuando lo hagas
- uses: actions/setup-node@v5
- uses: docker/setup-buildx-action@v4
```

En esta fase solo `actions/checkout@v5` apareció en el warning
explícito; las demás se mantienen mientras no avisen
individualmente.

## Workaround temporal (no recomendado)

Para forzar Node 24 sobre acciones viejas sin subirlas:

```yaml
env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"
```

Te quita el warning pero la acción puede fallar si depende de APIs
internas que cambiaron entre Node 20 y 24. Mejor subir la versión.

## Cómo verificar el fix

Tras el push, el run de la próxima ejecución del workflow no debe
incluir el mensaje "Node.js 20 actions are deprecated". Si aparece
con OTRA acción, súbela siguiendo el mismo patrón y documenta la
versión nueva en la tabla de arriba.

## Notas

- Las matrices `vN.x.y` (e.g. `actions/checkout@v4.1.1`) tienen el
  mismo problema; no basta con subir el patch. Hay que cambiar la
  versión mayor.
- Si una versión nueva aún no existe (por estar en pre-release),
  comenta `actions/foo@vN-rc` o espera; nunca uses `@main` en CI
  por motivos de supply-chain.
