# Córtex F2 — test humano (una sola cosa, cinco minutos)

Esta guía existe porque la casilla de cierre de
[`docs/roadmap/cortex-f2-afectivo.md`](../../roadmap/cortex-f2-afectivo.md)
(FASE I, «Suite completa F2 en verde + lint/type») decía estar bloqueada por
**dos** cosas humanas y en realidad sólo lo estaba por **una**. La otra —
`alembic upgrade head` / `downgrade` / `alembic check` — se ejecutó el
2026-08-20 y sus números están abajo, en «Lo que NO tienes que comprobar»: si
los repites, pierdes el tiempo.

Lo que sigue siendo tuyo es **un solo gesto**: comprobar que el dial PAD del
Panel de Mente **se mueve solo**, empujado por el WebSocket de telemetría, unos
segundos después de que le hables al córtex. Ningún test automático puede
acreditarlo, porque lo que se afirma es que un dato viaja hasta un píxel.

> **El córtex es del System Owner.** Si tu cuenta no es el `system_owner` del
> despliegue no verás ninguna de estas pantallas (verás `Córtex no disponible`),
> y eso también es correcto: la barrera real es `require_system_owner` en el
> backend, no la UI.

---

## TL;DR

```
Pestaña A →  http://localhost:8080/admin/cortex/mind     (Panel de Mente)
Pestaña B →  http://localhost:8080/admin/cortex          (chat del córtex)
```

En B escribes un mensaje con carga emocional evidente. En A, **sin recargar**,
los cuatro diales y/o la etiqueta de mood cambian en 1-20 s. Eso es todo.

Repite el vistazo con el idioma en **EN** (botón `EN` de la cabecera) para
acreditar el ES+EN que pide la aceptación.

---

## Pre-requisitos

| Requisito                                             | Por qué                                                                                                                    |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Stack arriba (`docker compose … up -d`)               | api-server, workers, redis, postgres y **ollama**                                                                          |
| Tu usuario es el `system_owner`                       | Todas las rutas `/owner/cortex/*` y el WS están gated; un no-owner recibe 403 / cierre 1008                                |
| `agentic-platform-workers-1` sano                     | El distilador afectivo es una task Celery en la cola `default`                                                             |
| Ollama con el modelo del distilador                   | `docker exec agentic-platform-ollama-1 ollama list` tiene que listar **`llama3.2:1b`** (`WORKERS_CORTEX_AFFECT_LLM_MODEL`) |
| Un proveedor LLM configurado para el cerebro del chat | El turno del córtex tiene que responder; el afecto se distila DESPUÉS del turno                                            |

> **Si el stack lo levantas con `scripts/dev/up.ps1`** en vez de con el compose
> containerizado, el panel vive en `http://localhost:3000` y la api en
> `http://localhost:8001`; sustituye el host en las URLs de arriba. El `:8080`
> es el origen único de Caddy (`docker/caddy-manuals/Caddyfile`), que es como
> está levantado el stack de esta máquina.

### ⚠️ Antes de empezar, mira qué versión tienes desplegada

Comprobado el **2026-08-20** en el stack de esta máquina, y explica por
adelantado algo que si no parecería un fallo:

| Qué                             | Estado real                                              |
| ------------------------------- | -------------------------------------------------------- |
| Imagen del `admin-panel`        | construida el **2026-08-13** (una semana atrás)          |
| Imagen de la `api-server`       | construida el **2026-08-13**                             |
| `alembic_version` de la BD viva | **`0139_executions_steps_rollup`** — la cabeza es `0143` |

Consecuencia concreta, medida haciendo `grep` sobre el bundle del contenedor:

- **`/admin/cortex/mind` SÍ está desplegado** con sus diales (`pad-valence`…) y
  su aviso honesto. **Este test humano se puede hacer tal cual, hoy mismo.**
- **La segunda columna del chat NO está desplegada**: `cortex-second-column` y
  `cortex-mind-panel` (el `MindPanel` que se montó junto al hilo el 2026-08-19)
  **no aparecen** en el bundle del contenedor. Si abres
  `http://localhost:8080/admin/cortex` esperando diales a la derecha del chat,
  **no los vas a ver**, y no porque estén rotos: es que esa imagen es anterior.

Así que: **el test de abajo usa `/admin/cortex/mind`, que sí está**, y no
necesita redespliegue. Si además quieres validar la segunda columna del chat,
reconstruye el panel primero (`docker compose … build admin-panel && … up -d
admin-panel`).

---

## Procedimiento

1. **Abre el Panel de Mente** en una pestaña:
   `http://localhost:8080/admin/cortex/mind`.
   Tienen que estar a la vista, en este orden: el **Aviso de honestidad**
   («Modelo computacional de afecto, no sentimientos reales…»), el bloque
   **Emoción (PAD) y mood** con cuatro diales (Valencia, Activación,
   Dominancia, Intensidad) y el bloque **Sensaciones (drives)** con cuatro
   barras (Curiosidad, Vínculo, Coherencia, Competencia).

   Si en vez de diales lees «Aún no hay estado afectivo que enseñar», no pasa
   nada: el primer turno lo crea. Anota que partías de vacío.

2. **Comprueba que el socket está abierto** (opcional pero es lo que distingue
   «se movió por el WS» de «se movió por el polling»): DevTools → pestaña
   **Network** → filtro **WS** → tiene que aparecer
   `/ws/owner/cortex/telemetry` con estado **101**. Si no está, lo que veas
   moverse puede ser el refetch periódico de `/owner/cortex/mind`, que es otro
   camino y no es lo que esta prueba acredita.

3. **En una segunda pestaña abre el chat**:
   `http://localhost:8080/admin/cortex`, y escribe un mensaje con carga
   afectiva clara. Dos que funcionan bien porque empujan en direcciones
   opuestas:

   - ES: `Me ha encantado cómo has resuelto lo de ayer, gracias.`
   - EN: `I'm frustrated: this has been broken for three days.`

4. **Vuelve a la pestaña del Panel de Mente sin recargarla.** Entre 1 y 20
   segundos después de que el córtex responda, los diales y/o la etiqueta de
   mood tienen que cambiar de valor.

   El retardo es de diseño: el appraisal sale del hot-path (Celery + Ollama
   local) para no hacer esperar al owner — ADR 0075. Si tarda más de ~30 s,
   mira los logs (abajo).

5. **Cambia el idioma a EN** con el botón `EN` de la cabecera y repite el
   vistazo. Los rótulos tienen que pasar a `Valence / Arousal / Dominance /
Intensity` y `Drives (needs)`, y el aviso honesto a «Honesty notice: …».

---

## Criterio de aceptación

| ✅ Pasa si                                                                               | ❌ Falla si                                                                               |
| ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Algún dial PAD cambia de valor (o el mood cambia de etiqueta) **sin recargar la página** | Nada se mueve tras 30 s y sí hay un turno respondido                                      |
| El aviso de honestidad está visible todo el rato, antes y después del cambio             | El aviso desaparece en algún estado, o se ve un dial sin aviso                            |
| Los rótulos y el aviso están traducidos al pulsar `EN`                                   | Algo se queda en castellano con el idioma en EN                                           |
| En Network aparece `/ws/owner/cortex/telemetry` con estado 101                           | El WS no llega a abrir (entonces lo que se movió fue el polling, no el WS: **no cuenta**) |

---

## Si falla, dónde mirar (en este orden)

```bash
# 1. ¿Falló el encolado tras el turno?
#    OJO: la api-server sólo escribe si el encolado FALLA (es fire-and-forget,
#    `enqueue_cortex_distill_affect`), así que aquí el SILENCIO es buena noticia.
docker logs --tail 300 agentic-platform-api-server-1 | grep -i "cortex_affect\|celery"

# 2. ¿La ejecutó el worker, y con qué resultado?
#    Los eventos se llaman `cortex_affect.*`; los que importan:
#      cortex_affect.appraisal_failed_open  → Ollama no contestó ⇒ delta 0 ⇒ el dial NO se mueve
#      cortex_affect.appraisal_unparseable  → contestó pero no con el JSON esperado
#      cortex_affect.already_distilled      → re-entrega del mismo turno (idempotencia, es correcto)
#      cortex_affect.failed                 → excepción de verdad, con traza
docker logs --tail 500 agentic-platform-workers-1 | grep -i cortex_affect

# 3. ¿Se escribió el snapshot? (lo que alimenta el frame del WS)
docker exec agentic-platform-postgres-1 psql -U postgres -d agentic_platform \
  -c "SELECT created_at, mood_label, valence, arousal, appraisal_reason
        FROM cortex_affect_snapshots ORDER BY created_at DESC LIMIT 5;"

# 4. ¿Responde Ollama con el modelo del distilador?
docker exec agentic-platform-ollama-1 ollama list
```

Dos diagnósticos frecuentes y qué significan:

- **`cortex_affect.appraisal_failed_open` y un snapshot con `appraisal_reason`
  NULL**: el distilador no pudo hablar con Ollama y aplicó delta 0. El turno
  respondió igual — eso es lo correcto, es el fail-open del ADR 0075 — pero el
  dial no se mueve. Arregla Ollama y repite; **no** es un fallo del Panel de
  Mente.
- **El snapshot nuevo existe pero el dial no cambió**: entonces sí es el tramo
  WS→pantalla, que es exactamente lo que este test cubre y ningún otro. Apúntalo
  como rojo del test humano.

> **Dato de contexto medido el 2026-08-20** en la BD de este stack: hay 53
> snapshots afectivos, 40 con `appraisal_reason`, y el más reciente es del
> **2026-07-23**. O sea que el lazo funcionó de verdad en su día y lo que falta
> es que alguien vuelva a hablarle al córtex, no que el mecanismo esté muerto.

---

## Lo que NO tienes que comprobar (ya está hecho, con números)

La nota de la casilla decía que `alembic check` «no es ejecutable (la imagen de
runtime no trae alembic)». **Era falso**, y comprobarlo costó cinco minutos:
`alembic` 1.18.4 está instalado en el venv del repo (`.venv/Scripts/alembic.exe`)
y hay tests de integración que lo invocan por API. Ejecutado el 2026-08-20
contra una BD desechable (`agentic_cortex_f2`, puerto 15432 — **nunca** contra
la BD del stack):

| Comprobación                                          | Resultado real                                                                                                                                               |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `alembic upgrade head`                                | ✅ 143 migraciones, exit 0                                                                                                                                   |
| `alembic downgrade 0092_cortex_threads` (ida)         | ✅ 51 downgrades, exit 0, 11 s — `cortex_affect_snapshots` desaparece (`to_regclass` → NULL)                                                                 |
| `alembic upgrade head` (vuelta)                       | ✅ 51 upgrades, exit 0, 12 s — la tabla vuelve con sus 14 columnas y sus 3 índices                                                                           |
| Drift de autogenerate sobre `cortex_affect_snapshots` | ✅ **0 items** (y 0 sobre cualquier tabla `cortex_*`), sobre un diff global de 178                                                                           |
| `alembic check` (el comando literal)                  | ❌ exit 1 — **y no por drift de F2**: revienta con `NoReferencedTableError` porque `migrations/env.py` sólo importa `api_server.db.models` (34 de 84 tablas) |

El último renglón es un hueco **anterior y ajeno a F2**, documentado en
[`tests/integration/test_alembic_autogenerate_clean.py`](../../../tests/integration/test_alembic_autogenerate_clean.py)
(«y una nota sobre `env.py`, que es un hallazgo aparte»), donde además se dejó
escrito que arreglarlo cambia el autogenerate para todo el mundo y merece su
propio cambio. Mientras siga así, `alembic check` no puede dar verde para
**ningún** plan, y exigírselo a F2 es pedirle que cierre deuda de otro.

Tampoco tienes que comprobar a mano, porque hay test que lo cubre:

| Afirmación de la casilla                                 | Quién la acredita                                                                                            |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| El modelo ORM y la migración 0093 no divergen            | `tests/unit/test_cortex_affect_model.py` (compara DDL contra DDL, sin BD)                                    |
| El UNIQUE parcial por turno impide el snapshot duplicado | `tests/integration/test_cortex_affect_task.py::test_distill_is_idempotent_per_turn` (contra PostgreSQL real) |
| El `downgrade()` de la 0093 se ejecuta de verdad         | `tests/integration/test_cortex_threads_migration.py` (baja hasta `0091_system_owner_f0`)                     |
| La RLS de la tabla y el aislamiento cross-owner          | `tests/integration/test_cortex_owner_rls.py` (5 tablas, ENABLE+FORCE+policy, round-trip)                     |
| El copy honesto viaja pegado a los diales, en ES y EN    | `components/cortex/mind-panel.test.tsx` (9 tests) + `mind/honesty-i18n.test.tsx`                             |

> **Una corrección al enunciado del plan, para que nadie la reintroduzca**: la
> FASE A pedía afirmar que la tabla **no** tiene RLS (`pg_class.relrowsecurity
= false`). Hoy eso es **falso a propósito**: la migración `0140_cortex_owner_rls`
> (ADR 0156, 2026-08-19) le puso `ENABLE` + `FORCE` + policy de eje _owner_ a
> `cortex_affect_snapshots` y a otras cuatro tablas del córtex. Un test escrito
> al pie de la letra del plan saldría rojo, y tendría razón el código.

---

## Relacionado

- [`docs/roadmap/cortex-f2-afectivo.md`](../../roadmap/cortex-f2-afectivo.md) — el plan.
- [`docs/03-guides/human-tests/cortex-f5-voz-avatar.md`](./cortex-f5-voz-avatar.md) — el otro test humano del córtex (avatar de voz).
- ADR 0075 (modelo afectivo), ADR 0074 (córtex tenant-less), ADR 0156 (aislamiento estructural del córtex).
