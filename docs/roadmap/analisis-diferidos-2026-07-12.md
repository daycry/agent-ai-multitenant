---
title: "Análisis de lo diferido — ADRs proposed + restos de propuestas"
date: 2026-07-12
status: delivered
author: claude-code (verificado contra el código real con 4 exploraciones paralelas, no de memoria)
---

# Análisis de lo diferido (2026-07-12)

Inventario razonado de TODO lo diferido/`proposed`, verificado contra el
código: qué existe ya (con evidencia fichero:línea), qué falta exactamente,
tamaño restante (S <1 día / M 1-3 días / L >3 días), riesgo y veredicto.

## Hallazgo transversal: 3 ADR más con status obsoleto

Además de 0075/0078 (ratificados hoy), hay **tres ADR `proposed` cuyo
contenido está implementado y desplegado**:

- **ADR 0063** (autoarranque review-runtime): las partes A **y B** están
  implementadas y testeadas — el orquestador encola `compose_review_runtime`
  al ganar la transición (`orchestrator/dispatch.py:509-534`), el reconciler
  cubre el evento perdido (`reconciler.py:581-620`), B1 resuelta como opción
  (c) operador-configurable (`review_autostart.py:77-103`) y B2 con worktree
  a nivel de plan (`review_runtime_task.py:254-303`). Solo falta ratificar y
  actualizar el texto.
- **ADR 0073 fase F1** (voz del asistente): el asistente de tenants **ya
  tiene voz desplegada** — WS `/ws/assistant/voice`
  (`routers/assistant_voice.py:285`), `VoiceSession` STT→LLM→TTS, shell UI
  compartida con el córtex (`voice-call-shell.tsx`) + avatar con lip-sync por
  amplitud, servicios `stt` (faster-whisper) y `tts` (Kokoro) en el compose.
  F1 ≈ completa; el ADR merece nota de estado (F2/F4 pendientes).
- **ADR 0077** (olvido córtex): el olvido por `retention_score` es **real y
  está cableado** — `cortex/forgetting.py` (score, half-life 30d, kinds
  protegidos, umbral 0.1) + soft-delete auditable en
  `cortex_maintenance.py:178-226`, beat diario gated por
  `cortex.autonomy_enabled` (OFF). Falta solo la consolidación merge-into.

**Acción recomendada**: mini-commit de ratificación documental (S), como el de
hoy con 0075/0078.

## Resumen ejecutivo y orden recomendado

| Orden | Ítem                                      | Qué falta de verdad                                                                                                          | Tamaño  | Riesgo     | Veredicto                                                     |
| ----- | ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------- | ---------- | ------------------------------------------------------------- |
| 1     | **ADR 0101** discovery MCP                | persistir `input_schema` en el import (hoy se descarta → tools MCP con args inservibles)                                     | **S**   | bajo       | **Bugfix, hacer ya**                                          |
| 2     | Ratificación documental 0063/0073-F1/0077 | actualizar status+texto                                                                                                      | **S**   | nulo       | Hacer ya                                                      |
| 3     | **ADR 0103** restos SAFE                  | firma de símbolos G10 + `safeguard_stats` en el visor; **ratificar G8-B** (está en código y el ADR exige firma del operador) | **S**   | bajo       | Hacer pronto + pedir ratificación G8-B                        |
| 4     | **ADR 0098** beat de fetch                | `sweep_project_git_remotes` con kill-switch (patrón fx_fetch calcado)                                                        | **S-M** | bajo       | Hacer pronto                                                  |
| 5     | **ADR 0114** ask_human                    | tool + park (reutiliza aprobaciones) + inbox + respuesta→preámbulo                                                           | **M**   | medio      | El mayor valor de producto pendiente                          |
| 6     | **KB Q4** panel único grants              | unificar EDICIÓN (la lectura combinada ya existe en `kb-assignments-dialog.tsx`) + grant agente desde KB + pliegue Avanzado  | **S-M** | bajo       | Mini-tanda UI                                                 |
| 7     | **KB Q2** añadir conocimiento             | dropzone + KB implícita lazy find-or-create (backend Q1 ya está)                                                             | **M**   | bajo       | Mini-tanda UI                                                 |
| 8     | **A2 fase 2 / 0073-F2** token-a-token     | `stream()` en los 4 providers + `run_assistant_turn_streamed` + deltas por WS/SSE                                            | **M**   | medio      | Cuando toque asistente; barge-in aparte (L)                   |
| 9     | **ADR 0110+0097** hilo persistente        | HTTP: hilo en memoria + compactación; SDK: `ClaudeSDKClient` persistente + spike deny-sin-interrupt OBLIGATORIO              | **L**   | alto       | Tanda dedicada con telemetría antes/después                   |
| 10    | **ADR 0102** guardrails completo          | 3 hooks restantes + enforce + transporte config (`to_dict` inexistente) + on_error                                           | **L**   | medio-alto | Por fases: D3 transporte primero, enforce tras semana en warn |
| 11    | **ADR 0099** visor diffs código           | servicio+endpoint+vista; anticipo barato: persistir contexto del conflicto (S)                                               | **L**   | medio      | Diferir; hacer solo el anticipo                               |
| 12    | **ADR 0100** marketplace                  | provenance (S/M) → materialización → des-materialización                                                                     | **L**   | medio      | GATED operador (0 instalaciones hoy)                          |
| 13    | **ADR 0077** consolidación                | merge-into de memorias similares                                                                                             | **M**   | medio      | Tras encender autonomía y ver datos del olvido                |
| 14    | **ADR 0112 fase 2** mini-turno reflexión  | solo si telemetría `nudge:self_check` muestra que el sticky no basta                                                         | S-M     | medio      | GATED telemetría (~1-2 semanas de runs)                       |
| 15    | **ADR 0080** Playwright córtex            | todo; 4 preguntas abiertas al operador                                                                                       | **L**   | alto       | BLOQUEADO por decisión del operador                           |

---

## Detalle por ítem

### 1. ADR 0101 — Discovery MCP: persistir el `input_schema` (S, bugfix)

El discovery server-side YA existe (`routers/mcp.py:231` `discover_tools`,
usado por test-connection) y el runtime valida args contra el schema VIVO
(`mcp_tools.py:371,430,433`). Pero `import_mcp_tools` **descarta el schema**:
`ImportMcpToolsRequest` solo lleva `tool_names` (`mcp.py:283`), `Tool(...)`
(`mcp.py:373-382`) no fija `input_schema` → queda `'{}'::jsonb` y el upsert
(`:384-389`) tampoco lo refresca. **Consecuencia**: toda tool MCP con
argumentos se anuncia al LLM con `parameters: {}` y el pre-guard la rechaza —
inservible. Fix quirúrgico: re-discover en import + fijar
`input_schema`/`description` + extender upsert. Sin migración. Filas ya
importadas se sanean re-importando. Badge UX «requiere import» (T6) es FE
ortogonal.

### 2. ADR 0114 — ask_human no terminal (M)

**La plataforma YA tiene el mecanismo**: el gate de aprobación hace
«park + re-dispatch» — el run parquea (`STATUS_AWAITING_APPROVAL`), el worker
crea `ApprovalRequest` y libera el agente (`execution.py:1199-1217`), al
aprobar la task vuelve a BACKLOG (`routers/approvals.py:78`); reaper y
reconciler ya respetan el estado. El rail para inyectar la respuesta también
existe (preámbulos comments/prior_failure, `run_spec.py:182-188`).

Restante: tool `ask_human` como capacidad del loop (patrón `update_plan`),
rama de park con payload `{question, options}`, inbox de pregunta + endpoint
de respuesta (o generalizar `ApprovalRequest` con `kind='question'`),
respuesta → preámbulo `HUMAN ANSWER`, UI, tests. El «reloj pausado» sale
gratis (re-dispatch = presupuesto propio por run). Cambio de producto: pide
OK del operador al diseño, pero la implementación es M con reutilización alta.

### 3. ADR 0110 + 0097 — hilo conversacional persistente (L, tanda dedicada)

El cliente de modelo vive todo el run (`__main__.py:778`) → hilo en memoria
viable sin persistencia cruzada para los kinds HTTP. El coste real: (a)
compactación propia bien testeada, (b) presupuestos con input creciente
(500k/250k), (c) SOLAPE con los mecanismos de continuidad ya estabilizados
(condensado P1-5, stickies, digests, scratchpad, batch 0111) que habría que
reconciliar, (d) la mitad claude_sdk (**0097 = L por sí sola**: hoy sesión
nueva por turno con `max_turns=1` (`providers.py:1172,1349`), deny CON
interrupt (`claude_agent.py:553-563`), `ClaudeSDKClient` sin usar en el repo;
el propio ADR exige un **spike previo** de deny-sin-interrupt+tool_result
inyectado antes de comprometer). gpt-oss/Ollama no tiene pricing de caching
(sí KV-cache server-side) → el retorno económico depende del provider.
Recomendación: tanda dedicada, mitad HTTP primero con flag por-provider,
instrumentación de relecturas/coste antes-después.

### 4. ADR 0102 — guardrails: del slice LOG al motor completo (L, por fases)

Hecho: D1 post_tool LOG (act `graph.py:678-680,711-716` + recall/KB
`:397-409`), D4 persistencia RLS (`execution.py:728-774`), D7 inyección
(`AgentDeps.guardrails`). Falta el grueso: D2 (pre_llm/post_llm/pre_tool +
enforce que cambie STATUS), D3 transporte de config por spec (el serializer
`to_dict` NO existe en shared-guardrails — prerrequisito), D5 `on_error` por
check (pipeline.run sin try/except hoy), D6 truncado 50k. Orden sensato:
D3+`to_dict` (desbloquea configs por-tenant/proyecto reales) → pre_tool LOG →
semana de calibración en warn → enforce. El flip a enforce toca la máquina de
estados y la cola humana: no rushear.

### 5. ADR 0103 — restos SAFE + ratificación G8-B (S)

SAFE casi completo: G2 (`graph.py:798-802`), G3b (`:774-785` +
`tool_classification.py:94-116`), G4a (`tool_classification.py:20-22,60-62`),
G5 lado runtime (`graph.py:894,903-908`). Falta: G10 firma de símbolos (el
digest toma 1.ª línea significativa, no 1.ª `def`/`class`; presupuesto ya
300 chars) y G5 lado frontend (exponer `safeguard_stats` en el visor de runs
— hoy solo viaja en steps_log). **Atención**: G8-opción-B está implementado
(`loop_detection.py:35-46`) siendo un ítem GATED cuyo criterio de aceptación
exige firma del operador — pedir ratificación explícita (el pin de
loop-detection se relajó, no se borró).

### 6. ADR 0098 — beat periódico de fetch (S-M)

T3 push incremental cableado (`execution.py:602-616` best-effort tras commit),
T5 merge directo retirado de la UI (`git-config-section.tsx:292-298`, enum
conservado), T6 sync manual (`POST /projects/{id}/git/sync`,
`projects.py:382-409`). Falta SOLO lo nuevo del ADR: task
`sweep_project_git_remotes` + entrada de beat con cron configurable +
kill-switch `git_fetch_enabled` (default OFF) — patrón calcado de
`FX_FETCH_BEAT_ENTRY` (`beat_schedule.py:298-308`). Webhook de push y merge
directo real siguen gated por diseño.

### 7. ADR 0099 — visor de diffs de código (L; anticipo S)

El mínimo anti-invisibilidad está: `rebase_conflict` clasificado
(`plan_git.py:393-399`, `execution.py:515-522,636-660`) y escalado al panel
(`plans.py:1177-1184`). Falta el núcleo: no hay servicio/endpoint de diff de
CÓDIGO ni vista FE (solo el de docs, `docs_viewer.py:312` +
`doc-diff-renderer.tsx`, reutilizable), y el contexto del conflicto NO se
persiste estructurado (solo texto en `execution.output`; sin ficheros/shas no
se pueden mostrar «ambos lados»). **Anticipo recomendado (S)**: persistir
`{ficheros_en_conflicto, plan_branch, shas}` en el marcador — deja los datos
listos y mejora ya el panel de escaladas. Cuidados del visor futuro: candado
de traversal/ref-injection al levantar la restricción `.md`, paginado de
diffs grandes.

### 8. ADR 0100 — materialización marketplace (L; pieza 1 S/M)

El gate de análisis estático del install fresco corre
(`marketplace.py:921-932` → `analyze_for_install`, 422 si hallazgo). Pero el
install sigue creando SOLO `MarketplaceInstallation` (`:950-962`): sin
materialización Tool/Skill (hallazgo M1 intacto), sin provenance
(`source_listing_id`… no existen en `domain.py`), sin des-materialización.
Con 0 instalaciones reales el impacto es nulo → sigue GATED. Si se avanza:
pieza 1 (migración provenance, aditiva/reversible) primero; la
materialización exige idempotencia de re-install, colisión con
`uq_tools_tenant_name` y soft-delete transaccional en revoke.

### 9. ADR 0073 F2 / A2 fase 2 — token-a-token y barge-in

F1 voz está desplegada para asistente y córtex (ver hallazgo transversal).
Token-a-token NO existe (`assistant.py:300` SSE por rondas; sin
`provider.stream()` en ningún provider). Trabajo: `stream()` en los 4
providers (patrón complete-tools/stream-final; caveat claude_sdk streaming
con tools), `run_assistant_turn_streamed`, deltas por WS/SSE, tests ×4 = M.
Barge-in (VAD + interrupción; hoy push-to-talk) = aparte, L. Visemas/avatar
GLB (F4) = no empezado.

### 10. ADR 0077 — consolidación de memoria córtex (M, tras datos)

Olvido hecho y gated OFF. La consolidación merge-into (fusionar memorias
similares en una resumida que las referencia) no existe. Recomendación:
encender la autonomía (decisión del operador), dejar correr el olvido unas
semanas, y decidir la consolidación con datos (volumen de memorias, calidad
del recall). Verificar de paso que exista UI de inspección/restauración de lo
olvidado (soft-delete lo permite).

### 11. ADR 0080 — navegador Playwright córtex (L, bloqueado)

Nada implementado para el córtex (los hits de Playwright son de la feature de
QA de agentes). Hoy el córtex «busca y lee» (`cortex/web.py` con anti-SSRF +
egress-proxy). El ADR trae 4 preguntas abiertas al operador y es la mayor
superficie de ataque nueva del sistema — correctamente bloqueado. Reutilizable
si se aprueba: egress-proxy y el patrón de runtimes efímeros.

### 12. KB Q2 y Q4 (mini-tanda UI)

**Q4 (S-M)**: la lectura combinada YA existe (`kb-assignments-dialog.tsx`
muestra grants de proyectos Y agentes con revoke). Falta unificar la EDICIÓN:
integrar el alta de ambos grants en el panel (hoy `KbGrantDialog` solo
proyectos; el grant a agente solo se crea desde la ficha del agente → falta
endpoint/picker desde el lado KB) + pliegue «Avanzado».
**Q2 (M)**: dropzone «Añadir conocimiento» en la ficha del proyecto +
find-or-create idempotente de la KB implícita «Documentos de {proyecto}»
(el backend duro ya está: Q1 auto-grant + upload + ingesta).

### 13. ADR 0112 fase 2 — mini-turno dedicado (GATED a telemetría)

La fase 1 (self-check sticky, coste cero) se desplegó hoy instrumentada
(`nudge:self_check`). Decidir la fase 2 SOLO con datos: si tras 1-2 semanas
hay runs que se estancan pese al self-check, el mini-turno dedicado con
escalado determinista se justifica; si no, ahorrado.

---

## Decisiones que siguen siendo del operador

1. Ratificar G8-opción-B (ya en código; el ADR 0103 exige su firma).
2. Aprobar el diseño de `ask_human` (ADR 0114) — cambio de producto.
3. Encender `cortex.autonomy_enabled` (activa olvido 0077 + bucles 0078).
4. Marketplace (0100): ¿se materializa ya o sigue gated?
5. Navegador Playwright (0080): 4 preguntas abiertas del ADR.
6. Merge directo real y webhook de push (0098): siguen gated por diseño.
7. Tanda dedicada 0110+0097 (hilo persistente): cuándo priorizarla.
