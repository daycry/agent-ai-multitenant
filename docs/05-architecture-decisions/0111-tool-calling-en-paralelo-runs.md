---
title: "ADR 0111: Tool-calling en paralelo (batch read-only) en el agent-runtime"
status: accepted
date: 2026-07-12
---

# ADR 0111: Tool-calling en paralelo en el agent-runtime

## Contexto

F36 descarta llamadas concurrentes: si el modelo emite varios tool calls se
ejecuta solo el primero. Cada lectura cuesta una iteración del presupuesto
(50 max): batch-leer 4 ficheros = 4 turnos.

## Decisión

Permitir ejecutar en un mismo turno un LOTE de tool calls READ-ONLY
(read_file/list_files/search/rag_search/memory_recall), cap N=4, resultados
agregados en una sola observación. Los mutadores siguen siendo de a uno
(semántica de una-acción, loop-detection y guardas de novedad aplicadas por
elemento del lote).

## Consecuencias

(+) Menos iteraciones quemadas en research; convergencia más rápida.
(-) Toca loop-detection (fingerprint por lote), safeguards de novedad,
presupuesto de tool_calls y el shape de observe.
