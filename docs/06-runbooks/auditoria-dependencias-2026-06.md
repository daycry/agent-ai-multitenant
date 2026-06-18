---
title: "Auditoría de dependencias (2026-06)"
audience: "Mantenedores de la plataforma"
status: vigente
date: 2026-06-18
---

# Auditoría de dependencias — 2026-06

Revisión de qué dependencias se pueden actualizar y qué mejoras aportan, pedida
por el operador. **Conclusión:** la mayoría de lo desactualizado son **majors con
breaking changes**; un upgrade masivo a ciegas rompería el sistema en marcha y el
CI (que es sensible al pinning — ver gotchas de prettier/ruff). La actualización
de majors debe ser un **esfuerzo dedicado y gateado por CI**, paquete a paquete,
no un sweep. Esta auditoría categoriza y recomienda.

> Lo que SÍ se hizo en esta sesión sin riesgo: eliminar el `DeprecationWarning`
> de **nuestro** código (`HTTP_422_UNPROCESSABLE_ENTITY` → `…_CONTENT`). Los
> warnings restantes son **internos de librerías** (ver "deprecations" abajo).

## Python — desactualizados (selección)

| Paquete                                                       | Actual  | Última      | Tipo         | Riesgo   | Nota                                                                                     |
| ------------------------------------------------------------- | ------- | ----------- | ------------ | -------- | ---------------------------------------------------------------------------------------- |
| fastapi                                                       | 0.136.1 | 0.137.2     | minor        | bajo     | pin de starlette/pydantic; probar                                                        |
| starlette                                                     | 1.0.0   | 1.3.1       | minor        | medio    | lo pina fastapi; subir con fastapi                                                       |
| pydantic_core                                                 | 2.46.4  | 2.47.0      | patch        | bajo     | con pydantic                                                                             |
| cryptography                                                  | 48      | 49          | major        | medio    | revisar APIs deprecadas                                                                  |
| **langgraph**                                                 | 0.6.11  | **1.2.5**   | **MAJOR**    | **alto** | 0.x→1.x: API del grafo cambia; es el bucle del agente                                    |
| langchain-core                                                | 0.3.86  | 1.4.7       | MAJOR        | alto     | con langgraph                                                                            |
| **redis**                                                     | 5.3.1   | **8.0.0**   | **MAJOR**    | **alto** | redis-py 5→8; cliente async                                                              |
| **paramiko**                                                  | 3.5.1   | **5.0.0**   | **MAJOR**    | alto     | SSH (backups)                                                                            |
| **protobuf**                                                  | 6.x     | **7.x**     | **MAJOR**    | alto     | con otel/grpc                                                                            |
| opentelemetry-\*                                              | 1.27    | 1.42        | minor×       | medio    | **cierra** el `pkg_resources` deprecated; subir api+sdk+exporters+instrumentation JUNTOS |
| mcp                                                           | 1.23.3  | 1.28.0      | minor        | medio    | protocolo MCP; probar tools                                                              |
| **pytest**                                                    | 8.4.2   | **9.1.0**   | **MAJOR**    | alto     | framework de test                                                                        |
| **pytest-asyncio**                                            | 0.26    | **1.4.0**   | **MAJOR**    | alto     | API de fixtures async cambia                                                             |
| **mypy**                                                      | 1.20    | **2.1.0**   | **MAJOR**    | alto     | nuevos checks → muchos errores nuevos                                                    |
| **ruff**                                                      | 0.5.0   | **0.15.18** | salto enorme | alto     | nuevas reglas → lint nuevo en todo el repo                                               |
| black                                                         | 24      | 26          | major        | medio    | reformatearía todo (CI pinea 24)                                                         |
| pre-commit                                                    | 3.8     | 4.6         | major        | medio    | hooks                                                                                    |
| boto3/botocore/s3transfer                                     | …       | …           | minor        | bajo     | AWS SDK, seguro                                                                          |
| certifi/idna/anyio/greenlet/click/hiredis/jsonschema/coverage | …       | …           | patch        | muy bajo | leaf libs                                                                                |

## Frontend (admin-panel) — desactualizados

| Paquete                         | Actual | Última     | Riesgo    | Nota                                          |
| ------------------------------- | ------ | ---------- | --------- | --------------------------------------------- |
| @tanstack/react-query           | 5.100  | 5.101      | muy bajo  | dentro de rango                               |
| @playwright/test                | 1.60   | 1.61       | bajo      | dentro de rango                               |
| **react / react-dom**           | 18.3   | **19.2**   | **MAJOR** | migración React 19 (Server Components, hooks) |
| **next**                        | 14.2.5 | **16.2.9** | **MAJOR** | Next 14→16: dos majors, gran migración        |
| **tailwindcss**                 | 3.4    | **4.3**    | **MAJOR** | Tailwind v4: nuevo motor/config               |
| **typescript**                  | 5.9    | **6.0**    | **MAJOR** | posibles errores de tipos nuevos              |
| **eslint / eslint-config-next** | 8 / 14 | 9 / 16     | MAJOR     | flat config                                   |
| **vitest**                      | 2.1    | **3.2**    | MAJOR     | API de test                                   |
| lucide-react                    | 0.400  | 1.21       | major     | iconos                                        |
| react-markdown                  | 9      | 10         | major     | con react 19                                  |

## Deprecations de librerías (no de nuestro código)

- **`pkg_resources is deprecated`** ← `opentelemetry` 1.27 lo importa. Se cierra
  subiendo otel a ≥1.30 (último 1.42). Requiere subir api+sdk+exporters+
  instrumentation a la misma versión; riesgo medio (cambios de exporters).
- **`allowed_objects` (LangChainPendingDeprecationWarning)** ← `langgraph`
  interno. Es **pending** (futuro, aún no activo); se cierra con langgraph 1.x
  (major, alto riesgo — toca el bucle del agente). No urgente.

## Recomendación

1. **Ahora (seguro):** nada que mutar en caliente. Lo de nuestro código (422) ya
   está. Los patches leaf y los minors AWS son seguros pero de valor nulo aislados.
2. **Plan dedicado, gateado por CI** (sugerido: ampliar `prod-13` o nuevo plan):
   por olas, con la suite + CI en verde tras cada una:
   - Ola A (bajo riesgo): fastapi+starlette+pydantic minor, otel (cierra
     pkg_resources), boto3, mcp, leaf patches.
   - Ola B (tooling, requiere arreglar lint/type nuevos + re-pin CI): ruff, mypy,
     black, pytest+pytest-asyncio+pytest-cov, pre-commit.
   - Ola C (frameworks, migración mayor): langgraph/langchain 1.x, redis 8,
     paramiko 5, protobuf 7; React 19 + Next 16 + Tailwind 4 + TS 6 en el front.
3. **No** hacer un `pip install -U` / `npm update` masivo: rompe el sistema y el
   el CI. Cada ola con su PR, sus tests y su verificación de runner.

## Mejoras a aprovechar (motivación de cada ola)

- **langgraph 1.x**: checkpointing/durabilidad estable, mejor API de grafos.
- **redis 8 (redis-py)**: mejoras de cliente async + RESP3.
- **pydantic/fastapi recientes**: validación más rápida, menos deprecations.
- **React 19 / Next 16**: Server Actions estables, mejor caché; pero gran salto.
- **otel 1.42**: sin `pkg_resources`, mejores exporters OTLP.
