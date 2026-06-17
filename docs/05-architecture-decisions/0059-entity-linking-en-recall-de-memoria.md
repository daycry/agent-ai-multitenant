---
adr_id: "0059"
title: "Entity linking como señal extra en el recall de memoria de agentes"
status: proposed
date: 2026-06-17
authors: [claude-code-2026-06]
plan_referenced: null
docs_language: es
---

# ADR 0059 — Entity linking en el recall de memoria

> **Estado: `proposed`** — idea capturada, NO en el camino crítico. Surge al
> evaluar la librería [mem0](https://github.com/mem0ai/mem0) (2026-06-17): se
> descartó adoptarla como dependencia, pero una de sus ideas —enlazar entidades
> entre memorias para boostear la recuperación— es la única genuinamente
> aditiva sobre lo que ya existe. Este ADR la registra para no perderla; se
> decide/implementa **solo si la calidad del recall se mide como un dolor
> real**, post-producción.

## Contexto

El subsistema de memoria de agentes ya es maduro (fases 04, 06.7, 06.17;
ADR 0054):

- `memory_entries` con `tenant_id` + RLS FORCE, 4 scopes
  (private/team_shared/project_shared/global), tipo episódica/semántica.
- Escritura: distilación LLM post-ejecución (`workers/memorizer.py` →
  `memorizer/distillation.py`) + tool `remember_about_me` + `POST /memories`.
- Recall híbrido (`memorizer/recall.py`): BM25 (`to_tsvector('es_unaccent')` +
  `ts_rank_cd`) **+** pgvector HNSW (coseno, 768-dim) **fusionados con
  Reciprocal Rank Fusion (RRF, k=60)**.
- Dedup: detector por similitud coseno + merge **gateado por humano**
  (`POST /memories/{id}/merge-into`). Sin auto-delete (decisión conservadora,
  acertada).

**Evaluación de mem0 (2026-06-17):** adoptarla como dependencia se **descartó**
porque (1) no tiene RLS y persiste en su propio almacén (Qdrant) → rompería el
Principio 1 (multi-tenancy con `tenant_id`+RLS en cada fila); (2) sus defaults
(OpenAI) caen fuera del catálogo cerrado de proveedores (ADR 0021); (3) duplica
~90% de lo ya construido. Además, mem0 v3 (abr-2026) abandonó el UPDATE/DELETE
por LLM hacia un modelo ADD-only + ranking en recall, lo que **valida** nuestro
enfoque conservador actual.

**El hueco real** frente a mem0: nuestra memoria es **plana** (content + tags +
embedding). mem0 extrae **entidades** y las **enlaza** entre memorias, usándolas
como señal adicional de recuperación ("entity match") junto a semántico y BM25.

## Decisión

**Propuesta (pendiente de decisión humana + de que el recall sea un dolor
medido):** añadir una capa ligera de entidades, reutilizando el stack actual y
**sin dependencias externas ni segundo almacén**:

1. **Extracción de entidades en la distilación.** Ya hay una llamada LLM en
   `distillation.py`; ampliar su prompt para devolver, por candidato, una lista
   normalizada de entidades (personas, proyectos, componentes, tecnologías…).
2. **Persistencia.** Tabla `memory_entities` (o columna JSONB
   `entities` en `memory_entries`) con `tenant_id` + RLS, índice GIN. Si tabla
   aparte: relación N:M `memory_entry_entities` para permitir el enlace.
3. **Señal de recall.** Añadir una tercera lista rankeada por **coincidencia de
   entidades** (entidades de la query ∩ entidades de la memoria) y fusionarla en
   el RRF existente (ya combina BM25 + vector; pasaría a 3 señales).

### Opciones

- **Opción A (recomendada si se activa):** entidades en JSONB sobre
  `memory_entries` + índice GIN + señal de entity-match en RRF. Mínimo cambio de
  esquema, una migración, reversible.
- **Opción B:** tabla `memory_entities` normalizada + N:M, con un "grafo" de
  co-ocurrencia consultable. Más potente (acerca al graph-memory de mem0) pero
  más superficie y coste; solo si A se queda corta.
- **Opción C (default actual):** no hacer nada. El recall RRF (BM25+vector) ya
  es sólido; la mejora es especulativa hasta tener métricas.

### Condición de activación

NO implementar de forma proactiva. Disparadores válidos:

- Evals de calidad de recall (fase 14 / `prod-*` de evals) muestran fallos de
  recuperación atribuibles a falta de coincidencia léxica/semántica que las
  entidades resolverían, **o**
- feedback de operador de que los agentes "olvidan" hechos relevantes que sí
  están en memoria.

## Alternativas consideradas

- **Adoptar mem0 (librería/self-host):** descartada — rompe RLS/multi-tenancy,
  introduce Qdrant + proveedores fuera de ADR 0021, y duplica lo existente.
- **Reranker BGE para memoria:** ya existe para RAG; podría cablearse al recall
  de memoria como alternativa/complemento a las entidades. Anotado como idea
  hermana; menor prioridad.

## Consecuencias

- **Si se activa (A):** +1 migración (reversible), prompt de distilación más
  rico (coste LLM marginal), recall potencialmente más preciso en consultas con
  entidades nombradas. Mantiene Postgres como única fuente de verdad y respeta
  RLS + ADR 0021.
- **Si no (C):** cero coste; el recall sigue como está. Es el estado por
  defecto y aceptable para producción.
- En ningún caso se añade dependencia externa ni segundo almacén de memoria.
