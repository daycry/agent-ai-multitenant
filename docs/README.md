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

## Carpetas de ESTE monorepo que no son de la plantilla

Las 7 numeradas son el contrato que valida el guardrail estructural. Este repo de
plataforma tiene además estas, que **no** forman parte de la plantilla y que un
proyecto nuevo no debe copiar (inventariadas en prod-15, `task_gov_cabeceras_07`,
para que dejen de parecer huérfanas):

| Carpeta             | Qué es                                                                                                                                                                                                                                                                                                                                        |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `context/`          | Contexto de desarrollo del propio sistema: `architecture-overview.md`, `conventions.md`, `glossary.md`, `tech-stack.md`, memoria del asistente.                                                                                                                                                                                               |
| `roadmap/`          | Los planes de construcción de la plataforma (ver [su índice](./roadmap/README.md)).                                                                                                                                                                                                                                                           |
| `manuals/`          | Manuales de usuario: fuente Markdown + PDF generado (entregables versionados a propósito, con carve-out en `.pre-commit-config.yaml`).                                                                                                                                                                                                        |
| `provider-example/` | **Código de referencia, no documentación**: el esqueleto de `llm_layer/` que sirvió de base a `packages/shared-llm` y que el ADR [0021](./05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md) cita explícitamente. Se queda donde está para no romper esa referencia; no se mueve a una carpeta canónica porque no es prosa. |
| `superpowers/`      | Skills y material de apoyo del entorno de agentes.                                                                                                                                                                                                                                                                                            |

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

**En un proyecto generado** —el caso que describe esta plantilla— el idioma se configura por proyecto: `es` o `en`. Todo el `/docs` debe estar en el idioma declarado del proyecto. Mezclar idiomas dispara el linter de idioma.

**En este repositorio de plataforma la regla es otra, y no la contradice**: su documentación es **bilingüe**, con el inglés como canónico, porque sus lectores son quienes contribuyen y quien encuentre el repo público — no un único cliente con un idioma declarado. La convención (`foo.md` inglés canónico, `foo.es.md` castellano), qué está bilingüe hoy, qué no lo está a propósito y la guarda que lo vigila están en [03-guides/bilingual-docs.md](./03-guides/bilingual-docs.md) ([castellano](./03-guides/bilingual-docs.es.md)). Decisión del operador del 2026-08-21.

## Mantenimiento

Al cerrar cada plan, el agente **Technical Writer** del equipo del proyecto:

1. Genera entrada en `/docs/07-changelog/{plan_id}-{slug}.md`.
2. Si el plan tomó decisiones nuevas, genera ADR en `/docs/05-architecture-decisions/`.
3. Si el plan tocó APIs, schemas o configuración, actualiza `/docs/04-reference/`.
4. Si el plan introduce nuevos procedimientos operativos, añade runbook en `/docs/06-runbooks/`.

El plan no pasa a `completed` hasta que esta documentación está actualizada.
