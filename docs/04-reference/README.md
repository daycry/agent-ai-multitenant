---
title: Referencia técnica
docs_language: es
audience: backend-dev, architect, security, devops, integrator
updated: 2026-06-02
---

# 04-reference — Referencia técnica

Carpeta canónica de **referencia**: el contrato preciso del sistema —
modelo de dominio, RBAC y la referencia de cada subsistema. A diferencia
de las [guías](../03-guides/) (orientadas a tareas) y de los
[runbooks](../06-runbooks/) (orientados a operación), aquí vive el
**qué es exactamente cada cosa**: tablas, enums, endpoints, contratos de
seguridad. El código y los ADRs son la fuente de verdad; estas páginas
los reflejan.

## Núcleo del dominio

| Documento                                | Cubre                                                                                                                                                                                 |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [domain-model.md](./domain-model.md)     | Esquema relacional: agentes (IA + humanos), skills, tools, teams, proyectos, planes, tareas, entidades nuevas (human agents, llm_providers, marketplace, presupuestos…), enums y RLS. |
| [training-model.md](./training-model.md) | Modelo de capacitación (SABER/RECORDAR/SER/HACER), verbo único "Asignar/Quitar", tabla de niveles y el contrato del Hub `GET /{entity}/{id}/capabilities`.                            |
| [rbac.md](./rbac.md)                     | Matriz de roles por endpoint (el **contrato** de los gates), incl. la superficie platform-global del System Admin.                                                                    |

## Subsistemas

| Documento                                | Cubre                                                                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| [guardrails.md](./guardrails.md)         | Motor de guardrails declarativos por capas, tipos de acción, eventos y observabilidad.                                    |
| [pricing.md](./pricing.md)               | Catálogo de precios de modelos, sincronización LiteLLM (limitada a proveedores activos), snapshot por llamada, FX y RBAC. |
| [marketplace.md](./marketplace.md)       | Listings (skill/tool/mcp_server), niveles de confianza, instalación, publicación oficial vs privada.                      |
| [mcp-servers.md](./mcp-servers.md)       | Catálogo de MCP servers verificados.                                                                                      |
| [auth-sso.md](./auth-sso.md)             | SSO empresarial (OIDC/SAML), MFA (TOTP + WebAuthn) y endpoints de autenticación.                                          |
| [public-api.md](./public-api.md)         | API pública v1, tokens por scope y webhooks entrantes/salientes.                                                          |
| [notifications.md](./notifications.md)   | Notificaciones multicanal y asistente personal.                                                                           |
| [evals-stats.md](./evals-stats.md)       | Evals de calidad y dashboard de estadísticas por tenant / cross-tenant.                                                   |
| [backup-restore.md](./backup-restore.md) | Bundle de backup, destinos, modos de restore, monitorización y knobs.                                                     |

## Instalación y portal

| Documento                            | Cubre                                            |
| ------------------------------------ | ------------------------------------------------ |
| [installation.md](./installation.md) | Parámetros y artefactos de instalación.          |
| [dev-portal.md](./dev-portal.md)     | Portal de desarrollador y documentación pública. |

## Cómo se relacionan con el resto

- Las **decisiones** que dan forma a estas tablas/endpoints viven en
  [05-architecture-decisions](../05-architecture-decisions/) (ADRs).
- Para una visión transversal del sistema, ver
  [arquitectura end-to-end](../context/architecture-overview.md) y el
  [glosario](../context/glossary.md).
- Las **tareas** sobre estos subsistemas viven en
  [03-guides](../03-guides/); la **operación** en
  [06-runbooks](../06-runbooks/).
