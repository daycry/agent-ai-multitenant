---
name: textareas-markdown-preview
description: Requisito del operador — TODOS los textareas de la herramienta deben previsualizar Markdown.
metadata:
  node_type: memory
  type: feedback
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

El operador pidió (2026-06-21) que **todos los `<textarea>` de la herramienta
permitan previsualizar Markdown** (ejemplo citado: el **chat de proyecto** no lo
hacía).

**Why:** consistencia de UX — el usuario escribe markdown y quiere ver cómo queda
antes de enviar/guardar.

**How to apply:**

- Ya existe el componente `apps/admin-panel/components/ui/markdown-textarea.tsx`
  (`MarkdownTextarea`, tabs Editar/Vista previa, usa `renderPlanDraft` de
  `lib/plan-draft-md`). Para textareas SIMPLES (notas, descripciones), sustituir
  el `<textarea>` por `<MarkdownTextarea>`.
- **Caso especial chat**: el composer del chat de proyecto tiene lógica de
  menciones `@` atada al `<textarea>` crudo (cursor/onChange) → NO sustituir por
  el componente con tabs; en su lugar **añadir un toggle Editar/Vista previa que
  conserve el textarea** (hecho ya en `app/admin/projects/[id]/chat/page.tsx`,
  commit 2026-06-21). Mismo patrón para cualquier textarea con lógica de cursor.

**Estado (2026-06-21):** sweep mayormente HECHO (commits 07a8080, 061c449, f97e5e3).
Migrados a `MarkdownTextarea`: chat de proyecto (toggle), agents/[id] descripción,
projects/new descripción, approvals motivo, assistant/settings system prompt
(maxLength en onChange), agents/page prompt EN, teams descripción, inbox
justify-dialog, inbox submit-dialog. Para los 3 últimos (e2e-coupled) se
actualizaron los selectores e2e a `-edit` (team-edit, human-inbox,
human-task-submit) — **PENDIENTE correr Playwright para confirmar** (no ejecutable
sin navegador).

- **DIFERIDO**: `components/capability/persona-section.tsx` (PromptLangField se
  reutiliza con idPrefix new-agent/edit-agent/persona → toca muchos testids en
  varios e2e + estado confuso con new-agent-system-prompt; requiere trabajo e2e
  coordinado, hacerlo donde se pueda correr Playwright).
- **SKIP correcto** (no markdown, dejar crudo): git-config clave SSH, mcp-servers
  args, notifications JSON, marketplace manifest YAML, SAML XML + 3 certs/key.
  Relacionado: [[cola-tarea-asistente-voz]].
