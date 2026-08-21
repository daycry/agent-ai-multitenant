# Plan 04 — tests humanos

Esta guía cubre los **5 tests humanos** del Plan 04 (Memoria, RAG,
KBs). Plan 04 NO trae scripts dedicados: los caminos críticos los
cubren los demos del Plan 04.5 (`human_04_5_01` cubre el ciclo de
memoria, `human_04_5_02` cubre el de RAG); el resto se prueba
manualmente por UI o queda bloqueado por features posteriores.

> **Estado del plan**: `completed`. Esta guía queda para regresión
> de la página `/admin/memories`, el flujo de ingestión KB y los
> scopes de memoria cuando se toque alguna de esas piezas.

## TL;DR

```powershell
.\scripts\dev\up.ps1                                        # stack arriba (1ª vez)
.\.venv\Scripts\python.exe scripts\demos\setup_demo_project.py    # proyecto + agente compartidos
.\.venv\Scripts\python.exe scripts\demos\setup_demo_04_5.py       # KB destino + Document sembrado (cubre 04_01/04_02)
.\scripts\dev\run-human-tests.ps1 -Only 04_5                # demos cubren los caminos automáticos
# luego: pruebas manuales para 04_02 completo, 04_03, 04_04, 04_05
```

## Pre-requisitos

| Requisito                                     | Por qué                                                                      |
| --------------------------------------------- | ---------------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                   | Postgres + Redis + api-server + admin-panel + Vault + MinIO + docling-serve. |
| Migración 0022 aplicada                       | Tabla `knowledge_bases` + `chunks` + índice HNSW. `up.ps1` lo hace.          |
| `setup_demo_project.py` ejecutado al menos 1× | Proyecto + agente compartidos.                                               |
| `docling-serve` corriendo (puerto 5001)       | Necesario para `human_04_02` (ingestión real). El compose lo arranca.        |

---

## `human_04_01` — Memoria mejora tareas repetidas

✅ **Cubierto por** `demo_human_04_5_01.py` (ver guía
[`04.5-agent-runtime-integration.md`](./04.5-agent-runtime-integration.md)).

El test del roadmap pide ejecutar la misma tarea dos veces y validar
que la 2ª referencia memorias de la 1ª; el demo de Plan 04.5 lo
simplifica sembrando una Execution `done` + Memorizer + recall.

**Pass**: ejecuta `demo_human_04_5_01.py` y verifica los 6 `[OK]`.

---

## `human_04_02` — RAG funciona con corpus realista

⚠️ **Parcial**. La parte "10 docs (PDF/.docx/.md/audio)" requiere
ingestión real por la UI; `demo_human_04_5_02.py` cubre el camino con
un Document sembrado en BD.

**Protocolo manual completo** (10 docs reales):

1. `http://localhost:3000/admin/projects` → elige un proyecto.
2. Entra en **Knowledge Bases** del proyecto. Crea una KB si no la
   tienes; pulsa **Subir documento**.
3. Sube 10 archivos: 3 PDFs, 3 .docx, 3 .md, 1 audio (mp3/wav).
4. Espera 2-5 min a que pasen `pending → processing → indexed`. En
   `/admin/documents/<doc_id>/ingestion` ves el progreso por doc; si
   alguno cae a `failed`, Docling registra el motivo ahí.
5. Comprueba `/admin/dashboard`: `docling-serve` y `egress-proxy` en
   `ok`.

**Checklist**:

- [ ] 10 docs en `indexed` (al menos los PDF y .md — audio puede tardar
      más por el modelo Whisper).
- [ ] Al menos una query RAG devuelve hits (vía
      `POST /internal/agent/rag-search` desde un container).
- [ ] `/admin/dashboard` muestra `docling-serve` y `egress-proxy` `ok`.

**Pitfalls conocidos**:

- `docling-serve unhealthy` → confirma con
  `curl http://localhost:5001/health`. Reinicia con
  `docker compose restart docling-serve`.
- Audio cae a `failed` con "audio backend not configured" → el modelo
  Whisper no está descargado. Es opcional — el resto (PDFs, .docx,
  .md) basta para el pass.

---

## `human_04_03` — Docling en el chat (pegar PDF)

❌ **Bloqueado** por chat-file-upload (Plan 07). La página
`/admin/projects/{id}/chat` no tiene `<input type="file">` ni handler
de paste-from-clipboard. **Saltar** hasta Plan 07 — se quedará como
follow-up de esta guía cuando llegue el upload de archivos en el chat.

---

## `human_04_04` — Scopes de memoria respetados

✅ **Ejecutable hoy** desde `/admin/memories`.

**Protocolo manual**:

1. `http://localhost:3000/admin/memories` → **Nueva memoria**.
2. Crea las 4 con scopes distintos:

   | scope            | content                               |
   | ---------------- | ------------------------------------- |
   | `private`        | "Mi nota privada"                     |
   | `team_shared`    | "Nota del equipo X"                   |
   | `project_shared` | "Nota del proyecto Y"                 |
   | `global`         | "Nota global" (requiere tenant_admin) |

3. Con el dropdown de **scope filter** confirma que ves las 4.
4. (Cross-team) Loguéate como usuario de otro team del mismo tenant.
   En `/admin/memories` ves `project_shared` y `global` pero NO la
   `team_shared` ni la `private` del primer usuario.

**Checklist**:

- [ ] 4 memorias creadas con scope distinto.
- [ ] El filtro de scope las muestra correctamente.
- [ ] Usuario de otro team NO ve `team_shared` ni `private` del primer
      usuario.
- [ ] La memoria `global` requiere rol `tenant_admin` (un `tenant_user`
      recibe 403 al intentar crearla — Plan 06.8 lo enforce).

---

## `human_04_05` — Cambio del modelo de embeddings de la plataforma

Este test estuvo marcado ❌ **Bloqueado** esperando un selector por KB que el
**ADR 0155** decidió no construir: la plataforma indexa con un único modelo, y
el campo por KB es el **sello** de con cuál se generaron sus vectores. Así que
lo que hay que probar no es cambiar el campo, sino cambiar el modelo de la
plataforma y ver que el sistema lo dice en vez de degradarse en silencio.

1. Con una KB **con documentos indexados**, cambia
   `API_SERVER_EMBEDDING_MODEL` a otro embedder de 768 dims (p. ej.
   `granite-embedding:278m`, previo `ollama pull`) **en api-server y workers**, y
   reinicia los dos.
2. En Admin → Knowledge Bases, la KB aparece marcada **«Reindexado pendiente»**,
   y su ficha nombra el modelo con el que se indexó y el activo.
3. Sube un documento nuevo a esa KB: termina en **failed**, y el motivo en la
   ficha nombra los dos modelos. (Antes se indexaba tan feliz con vectores de
   otro espacio semántico y nadie se enteraba.)
4. Busca en el RAG algo que sólo esté en esa KB: sigue apareciendo por texto
   (BM25) pero ya no compite por vector.
5. Reindexa sus documentos
   (`POST /knowledge-bases/{kb}/documents/{doc}/reindex`) y comprueba que el
   aviso desaparece y la búsqueda vectorial vuelve.
6. Vuelve a dejar `API_SERVER_EMBEDDING_MODEL` como estaba (y reindexa otra vez
   si habías reindexado).

---

## Volver a empezar

```powershell
.\scripts\dev\down.ps1 -Docker
Remove-Item scripts\demos\.demo_state.json -ErrorAction SilentlyContinue
docker exec agentic-platform-postgres-1 psql -U migrations_user -d agentic_platform -c `
  "TRUNCATE memory_entries CASCADE; TRUNCATE chunks, documents, kb_projects, knowledge_bases RESTART IDENTITY CASCADE;"
.\scripts\dev\up.ps1
```

## Troubleshooting

Para errores transversales (asyncpg connection, docling-serve down,
favicon 404, …) ver
[`run-demo-human-tests.md`](../run-demo-human-tests.md#troubleshooting).
