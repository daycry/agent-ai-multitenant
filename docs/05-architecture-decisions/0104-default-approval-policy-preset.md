---
adr: "0104"
title: Preset de aprobación por defecto para proyectos sin human_approval_policy explícita (cierre del fail-open A8b)
status: accepted
date: 2026-07-07
deciders: operador (jmano) — decisión tomada 2026-07-07
phase: auditoria-plataforma-2026-07-03
related: ["0020", "0035", "0102"]
docs_language: es
---

# ADR 0104 — Preset de aprobación por defecto (cierre del fail-open A8b)

> **Estado: `accepted`.** El operador eligió la opción **preset configurable con
> default `development`** el 2026-07-07. Este ADR documenta la decisión y su
> implementación.

## Contexto

La auditoría de los planes prod-XX (2026-07-06, hallazgo **A8b**) confirmó un
agujero **fail-open**: la columna `projects.human_approval_policy` es JSONB
**nullable sin default**. Cuando un proyecto se crea sin política:

1. el worker lee `approval_policy = project.human_approval_policy` = `None`
   (`execution.py`);
2. `_agent_spec` solo inyecta la clave si es truthy → la omite;
3. el runtime hace `approval = ApprovalGate(policy) if policy else None` → el
   gate **ni se instancia**;
4. resultado: **todas** las categorías sensibles (`write_file`, `shell_exec`,
   `git_push`, `send_notification`, `http_*`, `secrets`, `deploy`…) corren en
   auto, sin humano.

El commit `2106ed3` cerró la 1ª parte de A8 (mapa tool→categoría), pero el
comportamiento por defecto quedó como decisión de producto.

## Decisión

Un proyecto **sin** `human_approval_policy` explícita hereda un **preset por
defecto**, resuelto en tiempo de ejecución desde el platform setting
`default_approval_policy_preset` (**default `development`**). Se descartaron:

- **Fail-closed estricto** (todo `human_required`): máxima seguridad pero cada
  proyecto sin política se pararía en la 1ª escritura/commit e inundaría la cola
  de aprobaciones — choca con toda la inversión en "runs no convergen".
- **Sandbox explícito** (todo auto): no cierra el agujero.

El preset `development` (`seeds/builtin_approval_policies.py`) deja en **auto** las
categorías del bucle de coding (`code_changes` — cubre write/delete/shell/run\_\*;
`git_commit`; `external_http_get`) y **gatea** el resto
(`external_communication`, `external_http_post`, `secrets`, `deploy`, `infra`,
`pii`, `user_mgmt`, `git_push`…). Como el push al bare lo hace el **worker** (no el
agente — principio 2), gatear `git_push` a nivel de agente no frena el bucle
autónomo: la convergencia de runs no se degrada.

**Sin brecha de categoría no listada:** todos los presets construyen sus
`decisions` sobre `_all(CATEGORIES, ...)`, así que el mapa cubre las 13 categorías
canónicas — no hace falta una clave `unlisted_category`. Un slug desconocido cae
al preset seguro por defecto vía `preset_decisions()`, **nunca** fail-open a auto.

## Implementación

- `seeds/builtin_approval_policies.py`: `DEFAULT_APPROVAL_POLICY_PRESET =
"development"` + `preset_decisions(slug) -> dict[str,str]` (fallback seguro).
- `workers/execution.py`: `_resolve_effective_approval_policy(session, project)` —
  la política explícita del proyecto gana; si es None/vacía, resuelve el preset por
  defecto (setting configurable, fallback `development`). Cableado en el punto único
  donde se construía `approval_policy` (era `project.human_approval_policy or None`).
- Tests: `tests/unit/test_default_approval_policy.py` (preset + resolver + fallbacks).

## Consecuencias

- **Cierra el fail-open**: un proyecto sin política ya no corre acciones sensibles
  sin humano; hereda el gating de `development`.
- **Configurable**: el preset por defecto se lee de `default_approval_policy_preset`
  (platform setting). Exponerlo como control tipado en el panel de admin requiere
  añadir un tipo de setting `enum`/`str` al registry (`PlatformSettingType` hoy solo
  soporta bool/int/decimal/model_config) + su validación de `choices` + render
  frontend — **seguimiento pendiente**; hoy es overridable vía la API/tabla de
  platform_settings.
- **Riesgo de convergencia contenido**: las tools del bucle de coding siguen en
  auto; solo lo genuinamente peligroso gatea.
