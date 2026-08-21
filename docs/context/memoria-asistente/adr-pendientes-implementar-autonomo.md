---
name: adr-pendientes-implementar-autonomo
description: "Directiva: implementar los ADR proposed de forma autónoma. Aplicada 2026-07-26 — CERO ADR proposed restantes."
metadata:
  node_type: memory
  type: feedback
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

**✅ SEGUNDA APLICACIÓN (2026-07-26).** Barrido de los 4 ADR `proposed` que
quedaban. Los cuatro pasaron a `accepted`. **Cero `proposed` restantes.** Tres los resolví
yo; el **0117** lo paré y pregunté —su propio texto dice que sus decisiones son
de producto y que plataforma no debe tomarlas sola, que es justo el caso de
parada que esta directiva contempla— y el operador eligió las dos
recomendaciones el mismo día.

- **0128** (tools MCP por proyecto) → `accepted`. Sus 5 puntos estaban
  implementados y desplegados; solo mentía el `status`.
- **0110** (hilo conversacional en runs) → `accepted`. Construido en sus DOS
  transportes, flag `WORKERS_RUNTIME_CONVERSATION_THREAD` en OFF. Aceptar el ADR
  no enciende la flag: eso pide validación e2e, y el instrumento para medirla
  (informe de caché por proveedor, `task_wf_63`) existe desde la remediación.
- **0076** (razonamiento profundo + egress) → `accepted` CON la divergencia
  deliberada 3→4 registrada (el owner usa Ollama, no claude_sdk). Su
  **prerequisito de seguridad se resolvió**: ver [[remediacion-workflow-proyectos-en-curso]].
- **0117** → `accepted`. El operador eligió las dos recomendadas. Ver
  [[backlog-fuera-de-remediacion-2026-07-26]]: (c) destapó que la restauración
  completa estaba rota por un servicio fantasma en `restore_app_services`.
  **Preguntar fue lo correcto y además salió barato**: la respuesta llegó en un
  turno y el trabajo se hizo igual.

**✅ HECHO (2026-06-17, commit a45dab9).** Los 5 ADR `proposed` (0057/0058/0059/0060/0061) ratificados a `accepted` por delegación del operador. **0 ADR proposed restantes.** Resumen de decisiones: 0057 (resolución modelo por provider_id) ya implementado/mergeado (f87ca62); 0060 (socket-proxy + ruta sandbox) implementado Fase C; 0061 (Caddy TLS) implementado Fase E; **0058** (protección master) = Opción A (Pro+branch protection) **pero su ejecución es acción del DUEÑO** (plan de pago + admin GitHub, no agente — checklist en el ADR) + Opción C de puente; **0059** (entity-linking recall) = Opción C **diferir** (el propio ADR lo desaconseja; mejora especulativa sin métricas). Si aparecen ADR proposed nuevos, aplica la misma directiva. ⤵️ (Directiva original conservada abajo.)

El operador delegó **explícitamente** (2026-06-17) la decisión de los ADR `proposed`: _"cuando acabes con todas las fases, analiza los ADR pendientes e implementalos de forma autónoma, siempre eligiendo la mejor opción para el sistema."_

**Why:** levanta el gate humano "los ADR proposed requiere decisión del operador" SOLO para estos ADR — el humano YA decidió: que yo elija la mejor opción e implemente. No esperar ratificación para estos.

**How to apply:**

1. Primero terminar **todas** las fases de prod-01 (Fase F tasks 16-20 + cierre del plan a `pending_human_validation`). Esta directiva se ejecuta DESPUÉS.
2. Enumerar TODOS los ADR con `status: proposed` en `docs/05-architecture-decisions/` (no solo los que recuerdo).
3. Por cada uno: analizar opciones, elegir la mejor para el sistema, **implementarla** (con TDD si hay código), y pasar `status: proposed → accepted` documentando la decisión tomada y por qué.
4. ADR ya implementados durante prod-01 → solo ratificar (proposed→accepted), la implementación ya casa con la recomendación:
   - **0060** (daemon Docker + API interna sandbox) — implementado en Fase C.
   - **0061** (reverse proxy TLS Caddy) — implementado en Fase E.
5. ADR que requieren trabajo de implementación real:
   - **0058** (protección de master + medida puente en conventions.md) — de prod-02; revisar qué es accionable por mí (la protección de rama en GitHub es config del repo, posible gate humano; la medida puente sí es código/docs).
   - **0059** (entity-linking en recall de memoria) — del análisis mem0; diseñar + implementar la mejor variante.
6. Mantener el protocolo CLAUDE.md para el resto (un plan in_progress a la vez; no inventar features fuera de ADR). Si un ADR implica decisión de producto NUEVA no cubierta, ahí sí parar y preguntar.

Ver [[estado-trabajo-en-curso]].
