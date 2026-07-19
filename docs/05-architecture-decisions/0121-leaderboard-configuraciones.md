---
title: "ADR 0121: Leaderboard de configuraciones de agente (modelo × persona × skills)"
status: accepted
date: 2026-07-19
---

# ADR 0121: Leaderboard de configuraciones de agente

Aprobada por el operador el 2026-07-19 (tanda «adelante con todo»).

## Contexto

Cada run persiste su resultado (status, abort_code, iteraciones, tokens,
coste, veredicto de review) y su configuración efectiva (agente → modelo,
persona, skills). El operador elige configuraciones a ojo; la pregunta «¿qué
combinación converge más y más barato en MI carga real?» es contestable con
datos que ya existen, pero nadie los agrega.

## Decisión

Una vista de análisis en el admin-panel (`/admin/eval-quality` se amplía, o
sección nueva «Rendimiento de agentes») servida por un endpoint de agregación
tenant-scoped: por combinación (modelo, agente/persona, set de skills) sobre
una ventana temporal, mostrar runs totales, tasa de `done` sin escalada,
tasa de abort por código, iteraciones medias, coste medio por task done y
tokens medios. Ordenable; con umbral mínimo de muestras (n≥5) para no
rankear ruido. Solo agregación SQL sobre `executions` + joins de
configuración — sin telemetría nueva; la atribución usa la configuración del
agente EN el momento del run cuando está en el spec persistido, y la actual
como aproximación si no.

## Consecuencias

- Decisiones de configuración basadas en la carga real del tenant, no en
  intuición; cierra el bucle con los evals existentes.
- Sin coste de escritura (solo lectura agregada); el umbral de muestras evita
  conclusiones espurias.
- Limitación honesta: la atribución histórica es aproximada si la config del
  agente cambió después del run — se muestra con esa nota en la UI.
