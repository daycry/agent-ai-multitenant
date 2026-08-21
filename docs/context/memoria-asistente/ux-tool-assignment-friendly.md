---
name: ux-tool-assignment-friendly
description: 'El operador prioriza ("sobretodo") que la categorización de tools/comandos y la pantalla de asignación a agentes sean UI-amigables e intuitivas'
metadata:
  node_type: memory
  type: feedback
  originSessionId: f7e54214-9978-4552-9197-70ecc3f15b3d
---

El operador insistió ("sobretodo") en que **la categorización de comandos/tools y la asignación a agentes** sea **amigable e intuitiva** en la UI, no un volcado técnico de enums.

**Why:** ya repitió 3+ veces que la UI debe ser moderna/intuitiva; para tools le importa especialmente porque es donde un no-experto configura qué puede hacer cada agente (incl. comandos shell php/composer/phpunit).

**How to apply (admin-panel, Plan [[06.15-agent-tools-assignment]] + catálogo políglota):**

- Grupos con etiquetas humanas (Archivos, Git, Ejecución/Tests, Red, Conocimiento, Notificaciones, Comandos shell), no las categorías crudas; pestañas Básicas/Avanzadas con contador.
- Buscador/filtro, "seleccionar todo" por grupo, toggles claros, estados vacío/cargando/error, accesible.
- `security_level` (safe/sandboxed/privileged) como badge de color CON tooltip que lo explica en lenguaje llano.
- Para `shell_exec`: la allowlist de comandos como chips + **presets por stack** (PHP: php/composer/vendor/bin/phpunit/pest; Node: npm/npx; .NET: dotnet) — clic en preset, no teclear.
- Verificar la calidad UX en el gate de cada plan (usar skill frontend-design si la base del workflow queda sosa), no solo que compile.
