---
plan_id: cortex-f1-memoria-cognitiva
title: "Córtex F1 — córtex conversacional con memoria persistente"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex F1 — córtex conversacional con memoria persistente

## Resumen

Primera fase con código del córtex del `system_owner` (diseño en
[cortex-system-owner.md](../roadmap/cortex-system-owner.md), gobierno en el
[ADR 0074](../05-architecture-decisions/0074-rol-system-owner-y-cortex-singleton.md)):
un chat con **hilo persistente entre turnos** —que el asistente de tenant no
tiene—, **recall híbrido** sobre la memoria privada del owner y **degradación
honesta** cuando no hay modelo. Implementado entre 2026-06-24 y 2026-07-06 en
la rama `plan/runs-visor-trabajo`; el plan se quedó con el banner "GATED" puesto
mientras el código aterrizaba, y esa contradicción se corrigió el 2026-07-06.

Esta entrada se escribió **verificando el código**, no el plan: 11 de las 12
casillas están `[x]` y la que faltaba (Tarea 5) tenía un hueco real que hoy
está cerrado — ver abajo.

## Cambios

- **Tablas** `cortex_conversations` + `cortex_turns` (migración
  `20260623_0092_cortex_threads.py`), **tenant-less** sobre BYPASSRLS con
  filtro `owner_user_id` explícito en todo SQL — la excepción consciente al
  Principio 1 que el ADR 0074 autoriza, con su test cross-owner obligatorio.
- **Persistencia del hilo**: `cortex/threads.py`.
- **Turn-loop** `cortex/graph.py` (`run_cortex_turn`), clon del loop
  `decide→run_tools→decide→answer` del asistente con los mismos topes.
- **Recall híbrido real**: reutiliza `memorizer/recall.py` (BM25 + vector +
  entidad fusionados con **RRF**, Cormack 2009, `k=60`) restringido a
  `scope='private'` + `user_id=owner` + `metadata_.cortex=true`.
- **Tools del córtex** (`cortex/tools.py`): `cortex_remember`,
  `cortex_recall_more`, `web_search`, `web_fetch`, `browse_request`,
  `browse_result`.
- **Resolución del modelo**: `cortex/model_config.py` sobre la clave
  `cortex.default_model` de `platform_settings`, con
  `CortexModelUnavailableError` → **503 honesto, nunca 500**.
- **Router** `/owner/cortex/*` con gate `require_system_owner`
  (DB-authoritative, no solo el claim JWT): `POST /turns`, `GET /turns`,
  `GET /conversations`, `GET/PUT /model`, `GET /model-options` y las
  `/browse-sessions` que llegaron con el ADR 0080.
- **Frontend**: `app/admin/cortex/page.tsx` (chat persistente con preview
  Markdown) y el grupo de navegación "Córtex" `systemOwnerOnly`.

## Divergencias respecto al plan

- **La búsqueda web no salió por donde el plan mandaba.** El plan exigía el
  camino del ADR 0076 punto 3: WebSearch/WebFetch **nativas del Claude Agent
  SDK**. Lo implementado es una tool web **provider-agnóstica**
  (`cortex/web.py` + `web_safety.py`, SearXNG/Brave a través del egress-proxy)
  bajo el [ADR 0067](../05-architecture-decisions/0067-tools-web-search-y-fetch-con-egress-guardrails.md),
  porque el owner del stack de desarrollo usa gpt-oss/Ollama y no tiene
  `claude_sdk`. Es el "camino degradado" del punto 4 del ADR 0076, con
  anti-SSRF obligatorio (`ssrf_guard`) y kill-switch `cortex.web_enabled`. El
  propio ADR 0076 registra la divergencia como deliberada y mantenida.
- La Tarea 6 estaba marcada `[x]` describiendo el camino del SDK; lo que está
  vivo es el otro. La casilla no miente sobre "hay web", miente sobre "por
  dónde".

## Hueco que había, y su cierre

La única casilla sin marcar era la **Tarea 5** (resolución de
`cortex.default_model` + degradación limpia). La auditoría del 2026-07-27
([gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md)) la dejó en
`partial` con un motivo concreto: el criterio "503 honesto, NO 500" solo estaba
cubierto a nivel de unidad sobre el builder; el camino HTTP real no lo
ejercitaba nadie, y `tests/integration/test_cortex_degradation.py` **no
existía**.

Hoy existe, y cubre las dos ramas del 503:
`test_503_when_claude_sdk_missing` (el nombre exacto que enumeraba el plan) y
`test_503_when_no_model_configured`, ambas contra `POST /owner/cortex/turns`
con `assert resp.status_code == 503`.

## Tests

`test_cortex_threads.py`, `test_cortex_threads_migration.py`,
`test_cortex_turns_endpoint.py`, `test_cortex_cross_owner.py`,
`test_cortex_recall.py`, `test_cortex_recall_in_chat.py`,
`test_cortex_degradation.py` (integración); `test_cortex_graph.py`,
`test_cortex_model_factory.py`, `test_cortex_web*.py` (unidad).

## Estado de cierre

Falta lo humano: el QA del chat en navegador con el owner real (hilo que
sobrevive al refresco, recall que trae lo que se le contó dos turnos antes,
503 legible cuando no hay modelo) y el merge del PR.

## PR

- _pendiente_
