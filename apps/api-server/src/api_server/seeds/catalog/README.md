# Corpus del catálogo de KBs built-in

Este directorio contiene el **corpus curado** de las 6 _knowledge bases_
built-in que la plataforma siembra bajo `PLATFORM_TENANT_ID` (ver
[`builtin_kbs.py`](../builtin_kbs.py)). Hay **un fichero `.md` por KB**, y el
nombre del fichero coincide exactamente con el `slug` estable de la KB:

| Fichero                         | Slug de la KB built-in       |
| ------------------------------- | ---------------------------- |
| `python-fastapi-conventions.md` | `python-fastapi-conventions` |
| `node-express-conventions.md`   | `node-express-conventions`   |
| `php-symfony-conventions.md`    | `php-symfony-conventions`    |
| `postgresql-best-practices.md`  | `postgresql-best-practices`  |
| `api-rest-guidelines.md`        | `api-rest-guidelines`        |
| `react-nextjs-conventions.md`   | `react-nextjs-conventions`   |

## Para qué sirve

Las KBs built-in son **contenedores navegables** del catálogo (Plan 06.12,
ADR 0029): cualquier tenant puede concederlas a un proyecto. Hasta que tengan
chunks indexados, conceder una no aporta contenido al RAG. Este corpus es lo
que el seed de ingesta (task_06_13_02) parsea y persiste como
`documents` + `chunks` bajo `tenant_id = PLATFORM_TENANT_ID`.

## Cómo se ingiere

El contenido **no** se carga por la UI ni por un cron: es un **seed de
build-time idempotente**. El proceso de ingesta de catálogo
(task_06_13_02):

1. Lee cada `.md` de este directorio.
2. Lo trocea con un _chunker_ de markdown ligero (corta por encabezados y
   párrafos) — **no** depende de `docling-serve` (servicio HTTP externo,
   caído en CI).
3. Calcula embeddings con un _embedder_ inyectable (Ollama en producción; un
   _fake_ determinista en tests, sin red).
4. Persiste documents + chunks con **ids estables por hash del contenido**,
   de modo que re-sembrar **no duplica**.

## Cómo editar / ampliar el corpus

- **Edita el `.md`** correspondiente. Mantén el contenido **conciso y curado**
  (unas pocas centenas de líneas por fichero como máximo), no un volcado
  masivo ni texto de relleno.
- **Estructura con encabezados markdown** (`#`, `##`, `###`): el chunker corta
  por ellos, así que cada sección debe ser autocontenida y útil por sí sola.
- Para **añadir una KB nueva**: añade su entrada en `builtin_kbs.py` y crea el
  `.md` con el mismo `slug` como nombre de fichero.
- Tras editar, re-corre el seed de ingesta. Como los chunk ids son estables
  por hash, sólo se reescriben los chunks cuyo contenido cambió.

## Idioma

El corpus está en **español** (con la terminología técnica en inglés donde es
el uso habitual), en coherencia con las descripciones de las KBs del catálogo.
