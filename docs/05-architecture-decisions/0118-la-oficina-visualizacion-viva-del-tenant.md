---
title: "ADR 0118: La Oficina — visualización 2D en vivo del tenant sobre telemetría real"
status: accepted
date: 2026-07-19
---

# ADR 0118: La Oficina — visualización 2D en vivo del tenant

Aprobada por el operador el 2026-07-19 («adelante con todo», tanda de features
inspirada en el patrón _miniverse_ — github.com/ianscott313/miniverse — «pero
infinitamente mejorado»).

## Contexto

Los miniverses genéricos visualizan agentes como personajes en un mundo 2D,
pero sus estados son auto-reportados (webhooks del propio agente): son teatro.
Esta plataforma tiene lo que un miniverse no tiene: **telemetría real y
semántica** de cada instante del trabajo — `steps_log` en vivo por Redis/WS
(perceive/recall/plan/act/observe/reflect/self_review, `mcp_wire`,
`tool_call` con args), estados de tareas/planes, eventos de dispatch, review y
escalada (`ask_human`, `needs_human_review`), y el afecto persistido del
córtex (valence). El Kanban responde «qué»; nada responde «¿QUIÉN está
haciendo qué AHORA MISMO y quién está atascado?» de un vistazo.

## Decisión

Una vista nueva del admin-panel («La Oficina», `/admin/office`) que renderiza
el tenant como un piso 2D donde **todo lo visible mapea 1:1 a un evento real
— cero estados inventados**:

| Elemento visual                     | Fuente real                                                    |
| ----------------------------------- | -------------------------------------------------------------- |
| Personaje (agente)                  | fila `agents` del tenant                                       |
| Mesa/sala (proyecto)                | fila `projects`                                                |
| Sentarse en una mesa                | execution `running` de una task de ese proyecto                |
| Burbuja de diálogo                  | `summary` del último step del run (WS de ejecuciones)          |
| Reviewer acercándose a una mesa     | run de review `running` sobre la task                          |
| Personaje en «la puerta del humano» | `awaiting_human_approval` / `needs_human_review` / `ask_human` |
| Dar vueltas (mareo)                 | abort_code de bucle (`repetitive_loop_detected`, read-churn)   |
| Ánimo del córtex                    | valence del snapshot de afecto persistido                      |
| Dormir                              | agente sin runs activos                                        |

Interacción: clic en un personaje → su ficha/run real (la Oficina es una
_lente_ sobre las pantallas existentes, no una app paralela).

Implementación por fases: **v1** presencia + estados + burbujas reales
(canvas 2D propio, sin dependencias de motor de juego; sprites CSS/emoji
simples); **v2** interacciones review/escalada animadas; **v3** córtex y
asistente como personajes. El mapeo evento→estado-visual vive en un módulo
puro testeable (`lib/office/mapping.ts`) que también consume el Replay de
runs (ADR 0119) — una sola verdad para ambas superficies.

## Consecuencias

- Observabilidad gerencial instantánea y honesta; demo-abilidad alta.
- Coste contenido: ~90% de los datos ya se emiten; lo nuevo es frontend.
- El mapeo puro compartido con el Replay evita divergencia de semántica.
- Riesgo controlado: solo-lectura, sin superficie de mutación nueva; si el WS
  cae, la vista degrada a snapshot por polling (misma API REST existente).
