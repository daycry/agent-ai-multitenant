---
title: "ADR 0110: Hilo conversacional persistente en los runs (threading + prompt caching)"
status: proposed
date: 2026-07-12
---

# ADR 0110: Hilo conversacional persistente en los runs

## Contexto

Cada `decide()` del agent-runtime reconstruye `[system, user]` desde cero
(`providers.py::_decide_messages`); claude_sdk corre `max_turns=1`. El modelo
nunca ve un historial real assistant/tool: la continuidad depende de la ventana
de 8 items + stickies + el condensado (P1-5), y se anula el prompt-caching de
una conversación creciente (coste y latencia). Relacionado: ADR 0097 (sesión
SDK persistente, proposed).

## Decisión (propuesta)

Pasar el loop a un HILO acumulado por run: mensajes assistant (tool_calls) +
tool (resultados) reales, con compactación explícita al superar un umbral
(resumen de los turnos más viejos). En claude_sdk, sesión persistente del SDK
(ADR 0097); en HTTP, lista de mensajes creciente + prompt caching del proveedor
donde exista.

## Consecuencias

(+) Continuidad real de razonamiento; caching = menos coste/latencia por turno.
(-) Cambia el contrato de los 4 adaptadores y el modelo de coste (contexto
creciente); exige compactación bien testeada y presupuesto de tokens revisado.
Es EL cambio de mayor retorno del bucle y también el más invasivo — de ahí ADR.

## Alternativas descartadas

Mantener single-turn + más stickies: es el statu quo ya mejorado (P0/P1); no
recupera el caching ni la continuidad fina.

## Nota de planificación (2026-07-12)

Evaluado para implementación y DIFERIDO deliberadamente: exige tocar el
contrato de los 4 adaptadores, compactación propia y revisar el modelo de
presupuestos, y solaparía con los mecanismos de continuidad recién
estabilizados (condensado P1-5, stickies, scratchpad P1-6, batch read-only
ADR 0111 — que ya recorta iteraciones de research). Abordarlo requiere una
tanda dedicada con QA e2e propio, idealmente junto al ADR 0097 (sesión SDK
persistente) que es su mitad claude_sdk.

## Estado de implementación (2026-07-13)

MITAD HTTP IMPLEMENTADA como EXPERIMENTAL flag-OFF: `_ProviderModelClient` acumula el hilo en memoria por run (el cliente vive todo el run) — primer turno = rebuild historico; siguientes = [system] + hilo real (assistant con sus tool calls renderizados) + un TURN UPDATE compacto (observacion + stickies); compactacion honesta "EARLIER TURNS" al superar `_THREAD_MAX_MESSAGES` (20). Se activa con `WORKERS_RUNTIME_CONVERSATION_THREAD` (worker) -> `spec.model.conversation_thread` -> `_with_thread_flag` en el builder; solo providers HTTP (claude_sdk guarda ademas por \_advertises_submit_result). OFF por defecto: byte-a-byte el comportamiento historico, pineado por tests. PENDIENTE para ratificar: validacion e2e con runs reales (coste/convergencia antes-despues) y la mitad claude_sdk (ADR 0097, spike deny-sin-interrupt obligatorio).
