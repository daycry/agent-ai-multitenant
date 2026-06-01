# Plan 07 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 07 (Documentación
Estructurada y Visor Cross-Proyecto). Validan que la documentación se
genera sola al cierre de plan, que el guardrail estructural bloquea PRs
malformados, y que el **visor cross-proyecto** (`/admin/docs`) navega,
renderiza, busca y respeta los permisos RBAC.

> **Estado del plan**: `pending_human_validation`. Las 20 tareas y sus
> tests automáticos están en verde (bootstrap de las 7 carpetas, lint
> estructural + Markdown, validador de idioma, Technical Writer agente,
> sync `/docs` ↔ `kb_internal_docs`, visor Next.js). Varios e2e del
> visor (`docs-viewer-*.spec.ts`) están escritos pero requieren
> navegador — estos 4 tests humanos son el último paso antes de pasar a
> `completed`.

## TL;DR

No hay `setup_demo_07.py` ni launcher dedicado para este plan. El setup
es manual:

```powershell
.\scripts\dev\up.ps1                 # api-server :8001 + admin-panel :3000 + postgres + redis
```

Luego abre el visor en el admin-panel:

```
http://localhost:3000/admin/docs
```

El visor lee directo del filesystem persistente (los `/docs/` de cada
proyecto materializados por los worktrees de la Fase 6) y de la KB
interna `kb_internal_docs`. Para los tests de generación automática y
guardrail necesitas un proyecto con repo git y un plan que cerrar; para
los de visor/permisos necesitas al menos un tenant con varios proyectos
y docs sembrados.

## Pre-requisitos

| Requisito                                       | Por qué                                                                        |
| ----------------------------------------------- | ------------------------------------------------------------------------------ |
| Stack dev arriba (`up.ps1`)                     | api-server + admin-panel + postgres + redis                                    |
| Al menos un proyecto con repo git materializado | El visor lee `/docs/` del worktree y la generación post-plan commitea ahí      |
| Un plan cerrable (tareas done)                  | `human_07_01` verifica el workflow del Technical Writer al pasar a `completed` |
| Un usuario con acceso a 1 solo proyecto         | `human_07_04` valida el filtro RBAC del visor                                  |
| `curl` (opcional)                               | Para el step que comprueba el 403 a la URL directa de un doc ajeno             |

---

## `human_07_01` — La documentación se genera automáticamente al cierre de plan

**Qué prueba**: al cerrar un plan, el Technical Writer agente genera la
entrada de changelog, los ADRs si hubo decisiones nuevas y los updates a
`/docs/04-reference/` si se tocaron APIs o schemas — todo en el idioma
configurado del proyecto.

**Precondiciones**:

- Un plan con todas sus tareas en estado `done`, listo para cerrar.
- El proyecto tiene un idioma configurado (`es` o `en`).

**Pasos**:

1. Cierra el plan desde la UI (o dispara el workflow de cierre que
   ejecuta el Technical Writer).
2. Inspecciona el repo del proyecto / `/docs`:
   - Comprueba que se ha creado `/docs/07-changelog/{plan_id}.md` con
     **cabecera (frontmatter) + resumen + lista de tareas**.
   - Si el plan tomó decisiones de arquitectura nuevas, comprueba que
     hay un ADR nuevo en `/docs/05-architecture-decisions/` numerado
     secuencialmente.
   - Si el plan tocó APIs o schemas, comprueba que `/docs/04-reference/`
     refleja el cambio.
3. Abre cada archivo generado y verifica que está en el **idioma
   configurado del proyecto** (no mezcla es/en).

**Resultado esperado**: el changelog del plan existe con su estructura
canónica; los ADRs/reference se generan solo cuando aplica; todo el
contenido está en el idioma del proyecto.

**Checklist**:

- [ ] Entrada `/docs/07-changelog/{plan_id}.md` generada con cabecera +
      resumen + tareas.
- [ ] Si hubo decisiones nuevas, ADR generado en
      `/docs/05-architecture-decisions/`.
- [ ] Si se tocaron APIs/schemas, `/docs/04-reference/` actualizado.
- [ ] Todo en el idioma configurado del proyecto.

**Pitfalls conocidos**:

- Si no se genera nada, comprueba que el workflow post-plan del
  Technical Writer se disparó (revisa los logs del worker que ejecuta el
  agente).
- El idioma se lee de la config del proyecto, no del tenant — un
  proyecto en `en` genera docs en inglés aunque el resto del tenant esté
  en `es`.

---

## `human_07_02` — El guardrail estructural bloquea PRs malformados

**Qué prueba**: si un PR borra una de las 7 carpetas canónicas
obligatorias de `/docs/`, el guardrail estructural bloquea el merge con
un error claro y el feedback llega al equipo.

**Precondiciones**:

- Un proyecto con su `/docs/` ya bootstrapeado (7 carpetas presentes).

**Pasos**:

1. Crea una rama y elimina una de las 7 carpetas obligatorias (por
   ejemplo `docs/06-runbooks/`) o sus contenidos hasta dejarla ausente.
2. Abre un PR con ese cambio.
3. Observa el resultado del guardrail estructural sobre el push/PR.

**Resultado esperado**: el sistema **bloquea el merge** con un mensaje
claro que indica qué carpeta falta; el feedback aparece para el equipo
(chat o comentario en el PR).

**Checklist**:

- [ ] El sistema bloquea el merge con error claro (carpeta faltante
      identificada).
- [ ] El feedback se muestra al equipo (chat o comentario en el PR).

**Pitfalls conocidos**:

- El guardrail valida las 7 carpetas canónicas (`01-overview` …
  `07-changelog`). Borrar archivos sueltos dentro de una carpeta que aún
  existe no dispara el bloqueo estructural (eso lo cubre el lint de
  Markdown, otro check).

---

## `human_07_03` — El visor funciona con un tenant grande

**Qué prueba**: el rendimiento del visor con un tenant de ~5 proyectos y
~200 docs: navegación fluida, render rápido de Markdown con Mermaid,
búsqueda full-text y semántica dentro de los umbrales.

**Precondiciones**:

- Un tenant con **5 proyectos** y **~200 docs** en total.
- Al menos un `.md` complejo con un diagrama Mermaid.

**Pasos**:

1. Login en el admin-panel y abre `http://localhost:3000/admin/docs`.
2. Navega el sidebar (árbol proyectos → carpetas → archivos): debe ser
   fluido, sin lags perceptibles al expandir nodos.
3. Abre un `.md` complejo con Mermaid y cronometra el render: debe
   completarse en **menos de 2s**.
4. Lanza una búsqueda full-text con un término que aparezca en varios
   docs: los resultados rankeados con snippets deben llegar en **menos
   de 500ms**.
5. Lanza una búsqueda semántica (sobre `kb_internal_docs`) con una
   consulta en lenguaje natural: resultados en **menos de 1s**.

**Resultado esperado**: navegación fluida, render Mermaid < 2s,
full-text < 500ms, semántica < 1s.

**Checklist**:

- [ ] Navegación fluida en el sidebar.
- [ ] Render de un `.md` complejo con Mermaid en menos de 2s.
- [ ] Búsqueda full-text devuelve resultados en menos de 500ms.
- [ ] Búsqueda semántica devuelve resultados en menos de 1s.

**Pitfalls conocidos**:

- La búsqueda semántica depende de que `kb_internal_docs` esté indexado
  (sync `/docs` ↔ KB al mergear PR, task_07_09/07_10). Si devuelve vacío,
  comprueba que la reindexación incremental corrió tras el último merge.
- El render de Mermaid se cachea; la **primera** carga de un doc pesado
  puede acercarse a los 2s, las siguientes deben ser instantáneas.

---

## `human_07_04` — Permisos RBAC respetados en el visor

**Qué prueba**: un usuario con acceso solo a Proyecto A no ve, ni
encuentra por búsqueda, ni accede por URL directa a docs de Proyecto B.

**Precondiciones**:

- Un tenant con al menos dos proyectos: A (accesible al usuario) y B (no
  accesible).
- Un usuario cuyo acceso esté limitado a Proyecto A.

**Pasos**:

1. Login con el usuario limitado a Proyecto A.
2. Abre `/admin/docs`: el sidebar debe mostrar **solo Proyecto A**.
3. Busca un término que sepas que solo aparece en docs de Proyecto B:
   la búsqueda **no** debe devolver esos resultados (ni full-text ni
   semántica).
4. Copia la URL directa de un `.md` de Proyecto B (averíguala como
   admin) y pégala como el usuario limitado:
   - En la UI debe dar un error de acceso.
   - Por API debe devolver **403**:
     ```bash
     curl -s -o /dev/null -w "%{http_code}\n" \
       "http://localhost:8001/docs/<proyecto_b>/<ruta>.md" \
       -H "Authorization: Bearer $TOKEN_USUARIO_A"
     # → 403
     ```

**Resultado esperado**: el sidebar solo lista Proyecto A; la búsqueda no
filtra docs de B; la URL directa a un doc de B devuelve 403.

**Checklist**:

- [ ] El sidebar solo muestra Proyecto A.
- [ ] La búsqueda no devuelve resultados de Proyecto B aunque haya
      match.
- [ ] La URL directa de un `.md` de Proyecto B devuelve 403.

**Pitfalls conocidos**:

- Si el usuario ve Proyecto B, comprueba que su membership/grant está
  correctamente acotado y que el JWT activo refleja el scope (logout +
  login para refrescar).
- Un `system_admin` ve todos los proyectos por diseño — usa un usuario
  `tenant_user`/`tenant_member` con acceso a un solo proyecto para este
  test.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/07-documentacion-visor.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/07-documentacion-visor.md`](../../07-changelog/).
3. Verifica que el PR `plan/07-documentacion-visor` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                  | Causa probable                                              | Fix                                                                            |
| ---------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `/admin/docs` muestra el árbol vacío     | El proyecto no tiene `/docs/` materializado en su worktree  | Bootstrapea las 7 carpetas y commitea; el visor lee del filesystem persistente |
| Búsqueda semántica siempre vacía         | `kb_internal_docs` sin indexar tras el último merge         | Re-dispara la sync `/docs` ↔ KB (reindexación incremental, task_07_09/07_10)   |
| El changelog no se genera al cerrar plan | El workflow post-plan del Technical Writer no se disparó    | Revisa los logs del worker del agente; reintenta el cierre del plan            |
| Render de Mermaid roto                   | El bloque ` ```mermaid ` tiene sintaxis inválida            | Valida el diagrama; el visor usa remark-mermaid + rehype-highlight             |
| Un usuario limitado ve proyectos ajenos  | Su membership/grant no está acotado o el JWT está desfasado | Revisa el scope; logout + login para refrescar el claim                        |

Errores transversales viven en `docs/03-guides/gotchas/`.
