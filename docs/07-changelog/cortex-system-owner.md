---
plan_id: cortex-system-owner
title: "Córtex del Owner + rol system_owner (diseño maestro + F0)"
completed_at: null
status: pending_human_validation
docs_language: es
docs_note: "El detalle de cada fase vive en su propia entrada; aquí van el diseño maestro, F0 y sus prerequisitos."
---

# Córtex del Owner — diseño maestro y cimiento F0

## Resumen

Documento de diseño del córtex del `system_owner` (una "mente sintética" para el
dueño del despliegue, distinta del asistente de tenant), sus cinco ADR de
gobierno y el plan por fases. Lo que este documento **entrega como código** es la
**Fase 0**: el rol, la cadena de autorización y sus prerequisitos. Las fases
F1-F5 tienen plan y changelog propios (índice en
[cortex-fases](cortex-fases.md)).

## Cambios — Fase 0 (rol `system_owner`)

- **Migración** `20260623_0091_system_owner_f0.py`: `users.is_system_owner`
  (Boolean NOT NULL, `server_default false`) con **UNIQUE parcial
  `WHERE is_system_owner`** — el singleton lo garantiza una constraint, no una
  convención.
- **Cadena de auth**: claim `own` en `encode_jwt`/`get_principal`,
  `AuthPrincipal.is_system_owner`, `require_system_owner` y
  `require_admin_or_owner` (compuesta nueva; `require_system_admin` se dejó
  **intacto** — decisión 4 de las abiertas, la de menor radio de impacto).
- **Revocación estricta** (decisión 6): las dependencias del córtex verifican
  `is_system_owner` **contra la BD por request**, no solo el claim. Un token con
  el claim forjado no pasa.
- **Bootstrap** del primer usuario como owner; propagación por login/MFA/SSO con
  **guardrail estructural**: las vías de minteo SSO no fijan `is_system_owner`
  (default false) y el gate consulta la BD, así que **SSO nunca concede
  ownership**.
- **`/me`** expone `is_system_owner`; el frontend lo consume vía
  `use-current-user` y gatea el grupo de navegación "Córtex" con
  `systemOwnerOnly`.
- **Fix bloqueante del diseño, hecho**: `ClaudeAgentProvider.run_agent` acepta
  `effort` y lo propaga a `_build_options`. Era el "el effort se ignora en
  silencio" que el ADR 0076 marcaba como bloqueante.
- **Prerequisito de seguridad, hecho**: la credencial de `claude_sdk` ya no
  aterriza en `os.environ` global — vive en la instancia, y el propio módulo
  lleva el comentario que explica por qué. Cuatro tests afirmaban lo contrario
  y pasaban en verde mientras la fuga existía (ADR 0076); ese es el caso que
  documenta `verificar-antes-de-implementar.md` §2.
- **ADRs de gobierno redactados**: 0074 (rol + tablas tenant-less sobre
  BYPASSRLS, excepción consciente al Principio 1), 0075 (modelo afectivo), 0076
  (razonamiento profundo + egress confiable), 0077 (olvido y consolidación) y
  0078 (bucles de fondo). Hoy **los cinco están `accepted`**: el 0074 llevó
  `accepted-f0` entre 2026-06-22 y 2026-08-27, por haberse aprobado en dos tiempos
  (cimiento F0 primero, excepción a RLS después), y ese valor se normalizó a
  `accepted` — la traza de la aprobación en dos tiempos vive en el banner del ADR.

## Decisiones abiertas del diseño: cómo quedaron

| #   | Decisión                    | Cómo quedó                                                                                                                                                                        |
| --- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Fuente del appraisal        | Distilador asíncrono post-turno (Ollama local, fail-open) — implementado en F2 tal cual.                                                                                          |
| 2   | Estructura de la identidad  | Tabla singleton con blob JSONB — implementado en F3 tal cual.                                                                                                                     |
| 3   | Drives en el MVP            | Incluidos desde F2 como estado observable; disparan comportamiento en F4.                                                                                                         |
| 4   | Acceso del owner al admin   | `require_admin_or_owner`, con `require_system_admin` intacto.                                                                                                                     |
| 5   | Búsqueda web sin claude_sdk | **DIVERGIÓ**: se implementó el camino degradado (tool web propia con anti-SSRF, ADR 0067) porque el stack de dev usa Ollama. El ADR 0076 lo registra como divergencia deliberada. |
| 6   | Revocación del claim `own`  | Verificación contra BD por request — implementado.                                                                                                                                |
| 7   | Política de olvido          | Soft-delete reversible con `PROTECTED_KINDS`, gated por kill-switch OFF — implementado en F5 (ADR 0077 `accepted`).                                                               |

## Lo que el diseño dejó fuera y sigue fuera

El **anexo del grafo de memoria** (`entities` + `memory_edges` tipadas en
PostgreSQL, con visualización y export Obsidian-compatible opt-in) pedía su
propio ADR cuando se priorizara. No se ha priorizado: `memory_edges` no existe en
el código (grep sobre `apps/` y `packages/`: cero). No es deuda oculta — el
propio documento lo dejaba condicionado.

## Estado de cierre

F0 está entregada y desplegada. Lo que impide cerrar este documento son las
fases que describe: F2-F5 tienen casillas abiertas con hueco identificado
(inventario en
[gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md)).

La parte **documental** de ese cierre sí se hizo el 2026-07-30: el banner del ADR
0074 ya no gatea F1-F5, el diseño maestro
([cortex-system-owner.md](../roadmap/cortex-system-owner.md)) declara el estado de
cada una de las seis fases con su plan y su changelog, y ya no afirma que los ADR
0075-0078 estén `proposed`. Quedaba una decisión, no un trabajo: **normalizar el
`accepted-f0` del frontmatter del 0074 a `accepted`** o dejarlo como registro de
la aprobación en dos tiempos. Esta entrega eligió dejarlo y **escribir la razón en
el ADR**, porque inventar estados por fase (`accepted-f5`) rompería la coherencia
de un corpus donde los otros ADR usan `accepted`.

**Resuelto el 2026-08-27: normalizado a `accepted`.** El operador lo aprobó en el
mismo cambio que reparó el cuerpo del ADR, y lo que inclinó la decisión fue
comprobar que el valor **no tenía un solo consumidor** —fuera del vocabulario de
estados del repo, `AdrMeta.status` es texto libre y ningún `.py`/`.yml`/`.sh`/`.ts`
lo lee—, así que no gateaba nada: sólo obligaba a diez documentos a explicar por
qué existía. La traza de la aprobación en dos tiempos se queda en el banner del ADR,
que la cuenta con fecha y con más detalle del que cabe en un frontmatter.

## PR

- _pendiente_
