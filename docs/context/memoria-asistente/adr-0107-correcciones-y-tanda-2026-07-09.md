---
name: adr-0107-correcciones-y-tanda-2026-07-09
description: "ADR 0107 (rechazo→correcciones mismo plan) ENTREGADO+e2e vivo; prod-12 TODO hecho (mkt_01/net_01/docs_01); cola: manual+voz"
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

Tanda 2026-07-08/09 (rama `plan/runs-visor-trabajo`), todo desplegado en dev:

**ADR 0107 — rechazo con correcciones en el MISMO plan (hallazgo #11) ENTREGADO**:
arista `rejected→in_progress`; `POST /plans/{id}/generate-corrections` (LLM, idempotente
por sesión, kit provider de criteria) + `POST /plans/{id}/accept-corrections` (sync
selection + transición + corrections→accepted en UNA txn, anti-rebote del reconciler);
`chat/plan_corrections.py` + `chat/corrections_llm.py`; `PlanSpecification.corrections`
(campo nuevo — sin él el PUT lo descartaba); CorrectionsSection en el detalle del plan +
badge «corrección» + rejection_reason en GET review-session. Commits 6f01531/71fb169/
0eeafea/d5b22bb. **Fix crítico descubierto**: el reaper C8 F41 soft-borraba la sesión
terminal destruyendo veredicto+motivo → ahora conserva la fila y solo vacía
container_ids (e2055ac). Guías: `03-guides/validacion-humana-de-planes.md` y
`03-guides/app-review-images.md` (ejemplos por stack).

**E2e VIVO del ciclo con el plan CI4** (019f1397-afaf, tenant demo c5e446e7, proyecto
019f1384-311d): sesión review re-forzada (celery compose_review_runtime, imagen
ci4-preview:latest) → verdict rejected re-emitido por URL firmada (motivo verbatim del
operador) → generate (gpt-oss:120b propuso fix-1..fix-4 bien encadenadas) → accept →
fix-1/2/3 done con review IA; **fix-4 blocked por cuota claude_sdk 429** (resets 00:20
UTC) — retry programado vía human-action. OJO: el chat del proyecto resolvía a
claude_sdk (modelo del equipo) y el api-server NO lleva el SDK → 500; fix legítimo:
`chat_model_config` del proyecto → ollama-cloud por PUT /projects (provider_id
019e83cd-bb5c). Token e2e: sesión Redis + encode_jwt DENTRO del contenedor api-server
(patrón tests; user demo 11111111-1111).

**prod-12 COMPLETO → pending_human_validation**: mkt_01 (análisis estático en PRIMERA
instalación vía `analyze_for_install` — artefacto ausente = skip honesto no_artifact,
03f7251), net_01 mitad marketplace (sandbox bridge SIEMPRE internal, open = attach
registry-proxy + HTTP(S)\_PROXY, offline sin proxy; consent help en UI; 8ed513f),
docs_01 (81711d2). Solo F1 (registries privados por-proyecto) sigue diferida (decisión
operador, ADR 0067 B0.2).

**Why:** el operador pidió (mensajes 2026-07-08/09, "todo autónomo sin aprobaciones"):

1. terminar marketplace a medias ✅; 2) actualizar TODA la documentación con ejemplos
   (review images php/go/...) — hecho D1-D3; 3) MANUAL de usuario con pantallazos y textos
   muy detallados (300 págs OK) — pipeline EXISTENTE `docs/manuals/specs/*.manual.ts` +
   `./scripts/dev/generate-manuals.ps1` (-SkipBuild sirve: imágenes :manuals ya
   reconstruidas); 4) DESPUÉS: STT/TTS córtex+asistente PERFECTO con voces hombre/mujer
   (hoy roto — ver [[bug-asistente-voz-no-funciona]], infra ADR 0073) y modo voz tipo
   videollamada elegante estilo Teams con avatar más real («sorpréndeme»).

**How to apply:** TODO CERRADO 2026-07-09: manuales regenerados (14 specs ampliados,
manual-completo.pdf 210 págs, 091e78d); ciclo CI4 completo (fix-1..4 done → plan en
pending_human_validation, espera QA del operador); **VOZ ARREGLADA Y REDISEÑADA**
(dc58060 backend + 6fa4b51 frontend): causas raíz eran (1) api-server SIN WITH_CLAUDE=1
→ 503 del cerebro claude_sdk (¡mis rebuilds lo omitían! SIEMPRE `--build-arg
WITH_CLAUDE=1` en api-server:manuals), (2) close reason >123B (RFC 6455) → 1006 mudo,
(3) AudioContext suspendido (autoplay) → TTS en silencio, (4) default af_heart inglés
pisando la elección ES. Ahora: \_reject con frame error+reason recortado; \_extract_text
honesto (SttResponseError); filename con extensión; defaults ef_dora + setting
cortex_tts_default_voice; videollamada fullscreen compartida (voice-call-shell.tsx) con
PTT pointer-capture, voz persistida (localStorage agentic.voice.\*) que MANDA sobre el
ready, avatar realista (realistic-avatar.tsx) cuyo aspecto sigue el género de la voz y
el afecto del córtex. Verificado e2e: WS asistente Y córtex (turno completo con WAV
real, respuesta del cerebro + TTS) + sonda Chromium headless con micro falso (login→
overlay→llamada→PTT→ciclo, 0 errores consola). tareas.txt del operador = cola vieja
no pedida. Ver [[tanda-hallazgos-prod12-2026-07-08]].
