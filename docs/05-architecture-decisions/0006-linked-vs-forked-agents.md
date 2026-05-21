---
adr: "0006"
title: Linked vs Forked agents
status: accepted
date: 2026-05-21
deciders: System Architect
phase: 01-dominio-minimo
---

# ADR 0006 — Linked vs Forked: dos maneras de añadir un agente a un equipo

## Contexto

El catálogo plataforma trae 11 agentes built-in con prompts y
configuración cuidadas. Los tenants quieren dos cosas a la vez y
están en tensión:

1. **Aprovechar las mejoras del catálogo**: cuando la plataforma
   actualiza el `system_prompt` del _Backend Dev_ para añadir un patrón
   de seguridad nuevo, todos los proyectos que lo usan deberían
   beneficiarse automáticamente.
2. **Personalizar sin tocar a los demás**: un proyecto financiero
   necesita un _Backend Dev_ con prompt específico sobre PCI-DSS
   sin que esa modificación contamine al _Backend Dev_ genérico del
   resto del tenant.

Hacer "copia siempre" cumple (2) pero rompe (1). Hacer "referencia
siempre" cumple (1) pero impide personalización local.

## Decisión

Cada `TeamMember` referencia a un `agent_id`. Existen dos modos de
añadir un agente al equipo y la diferencia no vive en la junction
sino en el **scope** del agente referenciado:

- **Linked**: el `agent_id` apunta a un agente `global_builtin` (del
  catálogo plataforma) o `global_tenant_template` (del catálogo del
  propio tenant). El team comparte la fila con todos los demás; las
  ediciones al origen se ven instantáneamente.
- **Forked**: el flujo `POST /agents/{id}/fork` crea un nuevo agente
  con `scope = project_local`, `parent_agent_id` apuntando al origen
  y una copia profunda de prompts, modelo, herramientas y skills.
  Después se añade ese nuevo `agent_id` al team. El team queda
  desacoplado del origen.

El panel admin lo visualiza con badges (`Linked (built-in)`,
`Linked (tenant)`, `Forked`) y obliga al usuario a elegir mode al
añadir un miembro nuevo.

## Alternativas descartadas

1. **Copia siempre.** Sencilla pero perdemos propagación automática
   de mejoras del catálogo. Para 11 built-ins × N proyectos genera
   N copias huérfanas que envejecen aisladas.
2. **Referencia siempre + override por team.** Permitiría una capa
   de "delta" por team. Rechazado: la API resultante es compleja
   (capa de merge en tiempo de inferencia, debugging difícil) y no
   resuelve el caso "quiero también cambiar las skills del agente".
3. **Branching tipo git.** Un agente con un grafo de versiones, cada
   team apuntando a una commit. Sobre-ingeniería para el alcance
   actual; lo descartamos hasta que aparezca un caso real con N>3
   versiones por agente.

## Consecuencias

Positivas:

- El usuario decide explícitamente la relación con el catálogo
  cuando añade el miembro — sin sorpresas.
- Los built-ins evolucionan y propagan mejoras automáticamente a los
  teams que los referencian.
- Forks expone `GET /agents/{id}/diff` y `POST /agents/{id}/merge`
  para absorber mejoras del origen de forma selectiva (igual que
  `git diff` / `git rebase`).

Negativas / cuidados:

- **RLS multi-scope.** El endpoint `POST /teams/{id}/members` acepta
  built-ins ajenos al tenant porque RLS les da visibilidad cross-
  tenant via `agents_builtin_read`. La membership en sí queda
  tenant-scoped (FK desde `team_members.team_id`).
- **Eliminación de un origen forkeado.** Borrar el origen no
  cascade-elimina forks porque éstos ya tienen estado propio. Se
  rompe el `parent_agent_id` (queda NULL) pero el agente sigue
  funcionando.
- **UI debe ser clara.** El badge de scope es obligatorio en todas
  las vistas donde aparece un agente; sin él el usuario no entiende
  por qué un cambio "no se ve" en otro proyecto.

## Referencias

- Documento maestro, sección 8 (taxonomía de agentes).
- Implementación: `apps/api-server/src/api_server/routers/agents.py`
  (fork/diff/merge), `routers/teams.py` (member CRUD).
- Tests: `tests/integration/test_fork_agent.py`,
  `test_fork_diff.py`, `test_fork_merge.py`,
  `test_linked_vs_forked_invariants.py`.
- Pantalla: `apps/admin-panel/app/admin/teams/[team_id]/page.tsx`
  (dialog "Añadir miembro" con radios Linked/Forked).
