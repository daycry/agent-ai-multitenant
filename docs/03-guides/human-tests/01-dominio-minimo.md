# Plan 01 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 01 (Dominio Mínimo).
Validan el catálogo de plantillas seed bilingüe (agentes / equipos /
proyectos), el comportamiento linked vs forked de agentes, el
aislamiento multi-tenant de las nuevas entidades de dominio y la
claridad del doble Kanban.

> **Estado del plan**: `completed` (mergeado a `master`,
> `completed_at: 2026-05-22`). Esta guía es el **registro histórico**
> de los tests humanos con los que se cerró el plan; el frontmatter del
> roadmap recoge que los 4 dieron `result: pass` el 2026-05-21. Queda
> para regresión cuando se toquen el catálogo seed, el modelo
> linked/forked o el doble Kanban.

## TL;DR

El Plan 01 **no tiene** `scripts/demos/setup_demo_01.py` ni launcher
dedicado: las plantillas seed se cargan en el arranque del stack y el
resto del recorrido es navegación de UI. Setup manual:

```powershell
.\scripts\dev\up.ps1                 # api-server :8001 + admin-panel :3000 + postgres + redis
# luego: abre http://localhost:3000/admin  y recorre los checklists de abajo
```

> Para los tests linked/forked y de aislamiento conviene tener al menos
> dos proyectos y dos tenants. Puedes apoyarte en
> `scripts/demos/setup_demo_project.py` (crea proyecto + agente compartidos)
> para tener contexto visible, pero no es obligatorio: las plantillas
> seed ya existen tras `up.ps1`.

## Pre-requisitos

| Requisito                           | Por qué                                                        |
| ----------------------------------- | -------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)         | api-server + admin-panel + postgres + redis                    |
| Sesión `system_admin`               | Para el selector de tenant del header y mutar agentes globales |
| Toggle de idioma ES/EN en el header | Para validar el catálogo bilingüe                              |
| `curl`/Postman (opcional)           | Para forzar el cruce de tenant en `human_01_03`                |

---

## `human_01_01` — el catálogo de plantillas seed es funcional y bilingüe

**Qué prueba**: tras instalación fresca, el catálogo de agentes /
equipos / proyectos plantilla está completo y disponible en ES y EN.

**Precondiciones**: stack arriba; toggle de idioma visible en el header.

**Pasos**:

1. Abre el catálogo de **agentes plantilla** en el admin-panel.
2. Cuenta los agentes y verifica que cada uno tiene descripción en es y
   en (usa el toggle ES/EN del header).
3. Cambia el idioma del proyecto a `en` y comprueba que los
   `system_prompts` visibles cambian de idioma.
4. Abre el catálogo de **equipos plantilla**: añade una de las 5 a un
   proyecto en pocos clicks (vía el wizard).
5. Revisa las **8 plantillas de proyecto**: ¿cubren los casos típicos
   sin empezar de cero?

**Resultado esperado**:

- Los **11 agentes plantilla** aparecen con descripción en es y en.
- Cambiar el idioma a `en` cambia los `system_prompts` visibles.
- Las **5 plantillas de equipo** se añaden a un proyecto en pocos clicks.
- Las **8 plantillas de proyecto** cubren los casos típicos.

**Checklist**:

- [ ] Los 11 agentes plantilla están en el catálogo con descripción en
      es y en.
- [ ] Cambiar idioma del proyecto a `en` cambia los system_prompts
      visibles.
- [ ] Las 5 plantillas de equipo se pueden añadir a un proyecto en
      pocos clicks.
- [ ] Las 8 plantillas de proyecto cubren los casos típicos.

**Pitfalls conocidos**:

- La asignación de equipo a proyecto se valida vía el **wizard**
  (hereda `team_id` de la plantilla); la edición posterior del team
  desde el detalle de proyecto se difiere a Plan 02.

---

## `human_01_02` — linked vs forked se comporta correctamente

**Qué prueba**: un agente en modo `linked` recibe las actualizaciones
del agente global; uno en modo `forked` queda desacoplado pero puede
absorber mejoras selectivamente vía diff.

**Precondiciones**: dos proyectos (A y B) y un agente global (p. ej.
Backend Dev). Sesión system_admin para mutar el agente global.

**Pasos**:

1. En el **proyecto A**, añade el agente Backend Dev en modo **linked**.
2. En el **proyecto B**, añade el **mismo** agente en modo **forked** y
   cambia su `system_prompt`.
3. Vuelve al proyecto A y verifica que su agente conserva el prompt
   original (el cambio en B no le afectó).
4. Como System Admin, **actualiza el agente global**.
5. Verifica que el proyecto A (linked) recibe el cambio
   automáticamente, y el proyecto B (forked) **no**.
6. Desde el proyecto B, abre el **diff** con el global y absorbe
   mejoras selectivamente.

**Resultado esperado**:

- A (linked) conserva el prompt original tras el cambio en B.
- Actualizar el global propaga a A, no a B.
- El diff de B contra el global permite absorber mejoras a la carta.

**Checklist**:

- [ ] Proyecto A añade agente Backend Dev en modo linked.
- [ ] Proyecto B añade el MISMO agente en modo forked y cambia su
      system_prompt.
- [ ] En proyecto A el agente sigue con su prompt original.
- [ ] Actualizar el agente global propaga a A (linked), no a B (forked).
- [ ] Desde B se ve el diff con el global y se absorben mejoras
      selectivamente.

**Pitfalls conocidos**:

- Las invariantes linked/forked están cubiertas por 21 tests de
  integración (`test_fork_*.py`,
  `test_linked_vs_forked_invariants.py`); este test humano valida el
  diálogo linked/forked del detalle de equipo en la UI.
- El recorrido completo de gestión de agentes por proyecto se afina en
  Plan 02.

---

## `human_01_03` — aislamiento multi-tenant es real para las nuevas entidades

**Qué prueba**: las nuevas entidades de dominio (equipos, agentes,
plantillas) respetan el aislamiento cross-tenant ya validado en Fase 0.

**Precondiciones**: dos tenants (A y B), cada uno con sus equipos y
agentes. Selector de tenant del header (system_admin) o tokens de
Tenant Admin de cada tenant.

**Pasos**:

1. Como Tenant A, intenta ver los equipos de Tenant B aunque conozcas
   su UUID (por UI y por `GET` directo).
2. Intenta asignar un agente de Tenant B a un equipo de Tenant A.
3. Comprueba que las **plantillas built-in** SÍ son visibles a todos
   los tenants (es el caso correcto).
4. Crea una **plantilla custom** en Tenant A y verifica que Tenant B
   **no** la ve.

**Resultado esperado**:

- Tenant A no ve los equipos de Tenant B ni conociendo el UUID.
- No se puede asignar un agente de B a un equipo de A.
- Las plantillas built-in son visibles a todos.
- Las plantillas custom de A no son visibles a B.

**Checklist**:

- [ ] Tenant A no ve los equipos de Tenant B aunque conozca su UUID.
- [ ] Tenant A no puede asignar agentes de Tenant B a sus equipos.
- [ ] Las plantillas built-in son visibles a todos los tenants.
- [ ] Las plantillas custom de Tenant A NO son visibles a Tenant B.

**Pitfalls conocidos**:

- Verificado con el selector de tenant del header (superadmin) y
  reforzado por `test_isolation.py` y
  `test_superadmin_cross_tenant.py`: un tenant user no escapa de su
  scope ni con el header `X-Tenant-Id`.

---

## `human_01_04` — doble Kanban es claro de usar

**Qué prueba**: la vista de Planes (gerencial) y la de Tareas
(operativa) son entendibles para un usuario novato.

**Precondiciones**: al menos un proyecto con un plan y algunas tareas.

**Pasos**:

1. Abre la vista de **Planes**: ¿se entiende a un golpe de vista qué
   iniciativas están activas?
2. Haz click en un plan → debe abrir el detalle con el Kanban de
   **Tareas filtrado** por ese plan.
3. Mueve manualmente una tarjeta de **Backlog** a **Ready** y verifica
   que funciona.

**Resultado esperado**:

- La vista de Planes comunica el estado de las iniciativas activas.
- Click en un plan abre el Kanban de Tareas filtrado.
- El drag&drop entre columnas funciona.

**Checklist**:

- [ ] Desde la vista de Planes se entiende qué iniciativas están
      activas a un golpe de vista.
- [ ] Click en un plan abre el detalle con el Kanban de Tareas
      filtrado.
- [ ] Mover una tarjeta de Backlog a Ready manualmente funciona.

**Pitfalls conocidos**:

- El breadcrumb `Proyecto > Planes > [Plan X] > Tareas` **NO** aplica
  en Plan 01: aún no existe pantalla de detalle de proyecto (el Tablero
  es top-level). Ese ítem se trasladó a los tests humanos de Plan 02
  junto con el detalle de proyecto.

---

## Cierre del plan

El plan ya está `completed` (`2026-05-22`); los 4 tests dieron `pass`
el 2026-05-21. Esta guía es el registro histórico para regresión.

## Troubleshooting

Los errores transversales del stack dev (JWT secret mismatch, asyncpg,
sticky cache de TanStack al cambiar de tenant, problemas Docker en
Windows) viven en `docs/03-guides/gotchas/`.
