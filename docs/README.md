# Estructura Canónica de /docs

Esta carpeta es una **plantilla**. Cuando crees un proyecto en el sistema (real, no este monorepo de plataforma), copia esta estructura como punto de partida del `/docs/` del proyecto.

La estructura tiene **7 carpetas numeradas obligatorias** que un guardrail estructural valida en CI:

| Carpeta                      | Contenido                                                            |
| ---------------------------- | -------------------------------------------------------------------- |
| `01-overview/`               | Introducción al proyecto, arquitectura, decisiones de alto nivel     |
| `02-getting-started/`        | Instalación, configuración, primer arranque, primer uso              |
| `03-guides/`                 | Guías how-to por tarea concreta (orientadas a problemas)             |
| `04-reference/`              | Referencia técnica: API, schemas, modelo de dominio, configuración   |
| `05-architecture-decisions/` | ADRs numerados secuencialmente (0001, 0002, ...)                     |
| `06-runbooks/`               | Procedimientos operativos: backup, restore, troubleshooting, upgrade |
| `07-changelog/`              | Una entrada por plan completado: `{plan_id}-{slug}.md`               |

Esta estructura es un Diátaxis adaptado: amplía las 4 categorías originales (tutorials, how-to, reference, explanation) a 7 más operativas para proyectos software profesionales.

## Reglas de Formato

Cada archivo Markdown:

1. **Frontmatter YAML obligatorio**:

   ```yaml
   ---
   title: Título del Documento
   last_updated: 2026-05-20
   plan_id: 01H7K
   related_tasks: [task_001, task_007]
   status: published
   ---
   ```

2. **Headers jerárquicos**: H1 único (título), H2 secciones, H3 subsecciones. No saltar niveles.

3. **Bloques de código** con language tag obligatorio.

4. **Diagramas** con Mermaid embebido (no imágenes externas para diagramas).

5. **Enlaces internos** relativos (`./02-architecture.md`), no absolutos.

6. **Cada documento abre** con un párrafo de 2-3 líneas que lo resume.

## Idioma

El idioma se configura por proyecto: `es` o `en`. Todo el `/docs` debe estar en el idioma declarado del proyecto. Mezclar idiomas dispara el linter de idioma.

## Mantenimiento

Al cerrar cada plan, el agente **Technical Writer** del equipo del proyecto:

1. Genera entrada en `/docs/07-changelog/{plan_id}-{slug}.md`.
2. Si el plan tomó decisiones nuevas, genera ADR en `/docs/05-architecture-decisions/`.
3. Si el plan tocó APIs, schemas o configuración, actualiza `/docs/04-reference/`.
4. Si el plan introduce nuevos procedimientos operativos, añade runbook en `/docs/06-runbooks/`.

El plan no pasa a `completed` hasta que esta documentación está actualizada.
