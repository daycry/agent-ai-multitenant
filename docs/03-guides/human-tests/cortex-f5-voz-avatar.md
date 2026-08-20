# Córtex F5 — test humano: la videollamada y el avatar afectivo

Esta guía cubre lo **único** que queda de un humano en
[`docs/roadmap/cortex-f5-voz-avatar.md`](../../roadmap/cortex-f5-voz-avatar.md):
el **QA visual del avatar** en navegador, en castellano y en inglés (casillas
**C3** y **E2**). Todo lo demás de esas dos casillas —la suite `tests/unit` +
`tests/integration`, el vitest del componente y la e2e Playwright— es ejecutable
por máquina y está ejecutado; los números están al final, en «Lo que NO tienes
que comprobar».

Se despacha en unos cinco minutos si el stack está arriba. Lo que se acredita es
lo que ningún test automático puede: que la cabeza se mueve, que la boca va
sincronizada con el audio, que el color sigue al afecto y que el aviso honesto
nunca se va.

> **El córtex es del System Owner.** Con cualquier otra cuenta verás `Córtex no
disponible` y ni siquiera el botón de voz. La barrera real está en el backend
> (`_is_db_system_owner`, cierre WS 1008), no en la UI.

---

## TL;DR

```
http://localhost:8080/admin/cortex   →  botón «Modo voz»  →  «Iniciar llamada»
```

Mantén pulsado el botón de hablar, di una frase con emoción evidente, suelta.
Mira la cara mientras responde. Repite con el idioma en **EN**.

---

## Pre-requisitos

| Requisito                                               | Por qué                                                                                     |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Stack arriba, con los contenedores `stt` y `tts`        | `agentic-platform-stt-1` (Whisper) y `agentic-platform-tts-1` (Kokoro :8880) sanos          |
| Tu usuario es el `system_owner`                         | El WS `/ws/owner/cortex/voice` cierra con 1008 para cualquier otro                          |
| **Micrófono** y permiso del navegador para el origen    | El bucle es push-to-talk real; sin micro no hay turno                                       |
| Altavoces / auriculares con el audio audible            | La sincronía boca-audio es literalmente lo que se juzga                                     |
| Ollama con `llama3.2:1b`                                | El frame afectivo que colorea el avatar sale del distilador (F2); sin él el color no cambia |
| Un proveedor LLM configurado para el cerebro del córtex | Si el turno no responde, no hay ni voz ni afecto que mirar                                  |

> Con `scripts/dev/up.ps1` el panel está en `http://localhost:3000`. El `:8080`
> es el origen único de Caddy, que es como corre el stack de esta máquina.

### ✅ Esto SÍ se puede hacer con lo que hay desplegado hoy

Comprobado el **2026-08-20** haciendo `grep` sobre el bundle del contenedor del
panel (imagen del **2026-08-13**, una semana atrás; la BD viva está en la
migración `0139` y la cabeza es `0143`):

| Marcador buscado en `/app/.next`       | ¿Está?     |
| -------------------------------------- | ---------- |
| `cortex-voice-toggle` (botón Modo voz) | ✅         |
| `cortex-avatar-mood` (píldora de mood) | ✅         |
| `Afecto simulado` / `Simulated affect` | ✅ los dos |

O sea que **el modo voz y su aviso honesto bilingüe están desplegados y este QA
se puede despachar hoy sin reconstruir nada.** (Lo que NO está en esa imagen es
la segunda columna del chat con los diales PAD, que es de F2 y del 2026-08-19 —
ver [`cortex-f2-afectivo.md`](./cortex-f2-afectivo.md). No afecta a este test.)

---

## Procedimiento

1. Abre `http://localhost:8080/admin/cortex`. Arriba a la derecha hay un botón
   **«Modo voz»**. Púlsalo: se abre la videollamada a pantalla completa.

2. **Antes de conectar, mira el subtítulo.** Tiene que decir, literal:

   > _Afecto simulado (modelo computacional) — no son sentimientos reales_

   Ese rótulo es obligatorio (ADR 0075 §6) y **no puede desaparecer en ningún
   estado de la llamada**. Si en algún momento no está, el test es rojo aunque
   todo lo demás funcione.

3. Elige una voz en el selector. Para el pase en castellano usa **Dora**
   (`ef_dora`, femenina ES) o **Alex** (`em_alex`, masculina ES).

4. Pulsa **Iniciar llamada** y acepta el permiso de micrófono. El anillo
   alrededor del avatar cambia de color por estado: ámbar (conectando), azul
   (listo), rojo (grabando), violeta (pensando), verde (hablando).

5. **Mantén pulsado el botón de hablar**, di una frase con carga emocional
   evidente, y suelta. Por ejemplo:

   - ES: `Me ha encantado cómo has resuelto lo de ayer, gracias.`
   - ES: `Estoy harto, esto lleva tres días roto.`

6. **Mira la cara mientras responde.** Cuatro cosas que juzgar, y son las cuatro
   que pide la aceptación:

   | Qué mirar         | Qué debe pasar                                                                                                       |
   | ----------------- | -------------------------------------------------------------------------------------------------------------------- |
   | **Boca**          | Se abre y cierra **al ritmo del audio** que oyes, no a un ritmo fijo ni con retraso perceptible                      |
   | **Cabeza**        | Se balancea (sway) despacio en reposo y **más rápido** cuando el afecto tiene activación alta                        |
   | **Color**         | Frase agradable → tono **verdoso/cálido**; frase de queja → tono **rojizo/frío y más apagado**                       |
   | **Etiqueta mood** | Bajo el avatar aparece una píldora `<mood> · simulado` — nunca un mood a secas que se pueda leer como un sentimiento |

   El color viene de `avatarStyleFromAffect`: valencia `-1…1` → tono rojo…verde
   (0-130 en la rueda), activación `0…1` → saturación 45 %…90 % y balanceo de
   3,4 s a 1,8 s por ciclo. Si dos frases opuestas dan **el mismo** color, el
   frame afectivo no está llegando (mira el apartado de fallos).

7. **Mide la latencia de Kokoro a ojo** (la incertidumbre que el ADR 0073 dejó
   abierta): cuenta cuánto pasa desde que sueltas el botón hasta que empieza a
   sonar la voz. Anota el número; si pasa de ~5 s la experiencia deja de ser una
   conversación y eso es un hallazgo, no un aprobado con reparos.

   > **Contexto para interpretar el número**: las dos imágenes del stack son de
   > **CPU** — `ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2` y
   > `fedirz/faster-whisper-server:latest-cpu` (comprobado el 2026-08-20 con
   > `docker inspect`). Lo que midas es el peor caso razonable, sin GPU: si sale
   > aceptable aquí, sale aceptable en cualquier despliegue. Si sale malo, la
   > conclusión NO es «el modo voz no sirve», es «este perfil necesita GPU» — y
   > eso es una decisión de despliegue, no un defecto de F5.

8. **Repite en inglés.** Cierra la llamada, pulsa `EN` en la cabecera, vuelve a
   entrar y elige una voz inglesa (**Heart** `af_heart`, **Michael**
   `am_michael`, **Emma** `bf_emma` o **George** `bm_george`). El subtítulo tiene
   que estar traducido:

   > _Simulated affect (computational model) — these are not real feelings_

   y la píldora de mood tiene que decir `· simulated`, no `· simulado`.

---

## Criterio de aceptación

| ✅ Pasa si                                                                                   | ❌ Falla si                                                     |
| -------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| La boca sigue al audio de forma reconocible                                                  | La boca se mueve sin audio, o el audio suena con la boca quieta |
| Dos frases de valencia opuesta dan **colores distintos**                                     | El color no cambia entre frases opuestas                        |
| El aviso honesto está visible en **todos** los estados (lobby, grabando, pensando, hablando) | Desaparece en alguno                                            |
| El subtítulo y la píldora de mood están traducidos con el idioma en EN                       | Algo se queda en castellano                                     |
| La voz empieza a sonar en un tiempo conversacional (anota el número)                         | Tarda tanto que deja de parecer una conversación                |

---

## Si falla, dónde mirar

```bash
# ¿Se rompió el turno de voz? Los eventos se llaman `cortex_voice.*`
#   cortex_voice.turn_failed   → el cerebro del córtex falló en ese turno
#   cortex_voice.socket_error  → se cayó el socket
# El cierre 1008 (no eres el owner) NO deja evento: lo ves en el propio navegador.
docker logs --tail 300 agentic-platform-api-server-1 | grep -i cortex_voice

# ¿Kokoro sintetizó, y con qué velocidad (speed viene de arousal_to_speed)?
docker logs --tail 200 agentic-platform-tts-1

# ¿Whisper transcribió?
docker logs --tail 200 agentic-platform-stt-1

# ¿Hay estado afectivo del que sacar el color? (si está vacío, el avatar sale neutro)
docker exec agentic-platform-postgres-1 psql -U postgres -d agentic_platform \
  -c "SELECT created_at, mood_label, valence, arousal FROM cortex_affect_snapshots
       ORDER BY created_at DESC LIMIT 3;"
```

Un caso concreto que confunde: **el avatar sale neutro y el color no cambia**.
Si `cortex_affect_snapshots` no tiene filas recientes, el frame `{type:'affect'}`
viaja con el baseline y el avatar hace lo correcto. Eso es un fallo del
distilador de F2 (normalmente Ollama caído → `ok:fail_open`), no del avatar: mira
[`cortex-f2-afectivo.md`](./cortex-f2-afectivo.md) antes de apuntar un rojo aquí.

---

## Lo que NO tienes que comprobar (ya está hecho, con números)

Medido el **2026-08-20** en esta máquina:

| Comprobación que pedía la casilla                                                  | Resultado real                                                                                                                                                                                                                   |
| ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| vitest del componente de videollamada (C3)                                         | ✅ `components/cortex/cortex-voice-call.test.tsx` en verde, dentro de **99 tests / 13 ficheros** de la superficie del córtex                                                                                                     |
| e2e Playwright `e2e/cortex-voice.spec.ts` (E2)                                     | ⚠️ **2 tests, verdes uno a uno**, pero la pasada completa es inestable **en local bajo `next dev`** — ver la nota de abajo                                                                                                       |
| `pytest tests/unit tests/integration` (E2)                                         | `tests/unit` **completo: 4774 tests, cero fallos**. De integración, `test_cortex_voice_ws.py` **completo: 8/8 verde**. La suite entera NO cabe en una pasada local (es un job de CI con 4 shards) — ver la nota de la casilla E2 |
| Tests dirigidos de F5 (`voice_affect`, `voice_turn`, `voice_prompt`, `forgetting`) | ✅ **51 passed** en 14 s                                                                                                                                                                                                         |

**La inestabilidad de la e2e en local es del arnés, no del producto**, y conviene
que quede escrito para que nadie la persiga otra vez: `playwright.config.ts`
arranca `next dev`, que **compila cada ruta la primera vez que se pide**. En una
máquina cargada esa compilación se come el presupuesto de 30 s del test y la
aserción falla contra una página que aún dice «Cargando…». La captura de fallo lo
demuestra sola: en la imagen que Playwright guarda al agotar el tiempo la página
está **entera y correcta**, con el botón «Modo voz» a la vista. En dos corridas
seguidas falló primero un test y luego el otro, que es la firma de una carrera de
tiempos y no la de un defecto.

CI no tiene ese problema porque **no usa `next dev`**: hace `npm run build` y
sirve con `E2E_WEBSERVER_CMD="npm run start"` (`.github/workflows/ci.yml`), sin
compilación bajo demanda. Para reproducir el veredicto de CI en local:

```bash
cd apps/admin-panel
NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build
E2E_WEBSERVER_CMD="npm run start" npx playwright test e2e/cortex-voice.spec.ts
```

---

## Relacionado

- [`docs/roadmap/cortex-f5-voz-avatar.md`](../../roadmap/cortex-f5-voz-avatar.md) — el plan.
- [`docs/03-guides/human-tests/cortex-f2-afectivo.md`](./cortex-f2-afectivo.md) — el otro test humano del córtex (dial PAD vía WS).
- ADR 0073 (modo voz STT/TTS/avatar), ADR 0075 (modelo afectivo), ADR 0077 (política de olvido).
