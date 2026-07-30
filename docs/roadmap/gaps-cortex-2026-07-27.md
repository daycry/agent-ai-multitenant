---
title: "Huecos por tarea - auditoria del cortex 2026-07-27"
status: published
created: 2026-07-27
docs_language: es
---

# Huecos por tarea (detalle)

> Anexo de [`auditoria-cortex-2026-07-27.md`](auditoria-cortex-2026-07-27.md).
> Una entrada por casilla que sigue sin marcar, con lo que falta exactamente.
>
> **Se indexa por TITULO, no por linea.** Los numeros de linea envejecen al
> primer parrafo que se anada al plan; el titulo se busca con un `grep` y
> sobrevive. Para localizar una tarea: `grep -n "<titulo>" <fichero del plan>`.
>
> **Un `partial` es una pista, no una sentencia.** Antes de tocar nada, abre el
> fichero: la pasada adversarial dio al menos un falso positivo comprobado (ver
> el caso de `update_mood` en el informe).

## F1 — memoria cognitiva

`docs/roadmap/cortex-f1-memoria-cognitiva.md` — 1 casillas abiertas

### `partial` — Tarea 5 — Resolución cortex.default_model + builder del modelo del córtex (degradación limpia)

Falta el test de INTEGRACIÓN que el plan exige: tests/integration/test*cortex_degradation.py::test_503_when_claude_sdk_missing NO existe (no hay ningún fichero \_degradation* en tests/integration/, y no hay ninguna aserción de status 503 sobre POST /owner/cortex/turns en toda la suite). El criterio «503 honesto, NO 500» solo está cubierto a nivel de unidad sobre el builder; el camino HTTP real (build_cortex_default_model → HTTPException 503) no lo ejercita ningún test.

## F2 — afectivo

`docs/roadmap/cortex-f2-afectivo.md` — 8 casillas abiertas

### `partial` — Modelo ORM `CortexAffectSnapshot`

No existe verificación automática de drift: `alembic check` no aparece en ningún test ni en .github/workflows/ci.yml, y tampoco hay un assert directo sobre `__tablename__` / columnas / pertenencia a `Base.metadata`. El mapeo 1:1 solo queda probado de forma indirecta.

### `partial` — Suite de calibración (interacciones canónicas → rangos PAD esperados)

El plan pide ~8 escenarios canónicos; hay 3 que realmente ejercitan apply_event+update_mood (el cuarto, `test_calibration_cold_farewell_lowers_bonding`:309-313, solo llama a decay_drives, ya cubierto por el test de drives). Faltan ~5 escenarios y el fichero dedicado de regresión.

### `partial` — Settings del worker + registro del módulo

No hay ningún test que compruebe ni que `Settings()` expone esos dos campos ni que "workers.cortex_affect" está en `app.conf.imports`. El único assert de ese estilo en el repo es para otra tarea (tests/integration/test_human_task_escalation.py:535, `workers.human_escalation`); tests/unit/test_cortex_beat_schedule.py:27-41 cubre las tareas de F4, no el distilador.

### `partial` — `GET /owner/cortex/affect/timeseries`

REFUTADO: la evidencia de test es FALSA en un punto concreto. El handler SI esta bien (routers/cortex_mind.py:117-160: filtro explicito owner_user_id==principal.user_id :132, since :133-134, until :135-136, limit :138, DESC + rows.reverse() para orden ASC :138-140, sobre get_admin_sessionmaker BYPASSRLS). Pero el test citado NO verifica since/until/limit: `grep -n 'since|until|limit' tests/integration/test_cortex_mind_endpoints.py` sobre las 367 lineas del fichero devuelve UN solo hit, y es la linea 9 del docstring del modulo — cero ocurrencias en el cuerpo de los tests. test_timeseries_owner_scoped_and_chronological (:263-305) llama GET /owner/cortex/affect/timeseries SIN params y solo asserta len==3, los valences y el orden ascendente. Cross-owner si esta cubierto (inserta 1 snapshot de other_id y asserta que no aparece). El plan pedia explicitamente 'respetando since/until/limit' en su TDD, asi que la parametrizacion del endpoint esta implementada pero SIN test que la ejerza -> partial.

### `partial` — `GET /owner/cortex/episodes?emotion=`

REFUTADO: falta un filtro del contrato del endpoint y el test citado no puede detectarlo. routers/cortex*mind.py:163-209 filtra user_id==owner, scope=='private', deleted_at IS NULL y metadata*->>'cortex'=='true' (:180-187) + emotion opcional por mood*label (:189-190), y devuelve appraisal_reason (:206). Pero el contrato del plan (seccion 'Endpoints / WS') exige ademas 'metadata*.emotion presente', y ESE filtro no existe. Verificado con grep quien mas escribe cortex=True en memory_entries: cortex/memory.py:249 (cortex_remember, hechos que el owner pide recordar), cortex/curiosity.py:169 (kind='learning'), workers/cortex_reflection.py:464 (kind='reflection') y :524 (kind='owner_model') — todas scope='private', user_id=owner, cortex=True y SIN bloque emotion. Fallo concreto: GET /owner/cortex/episodes sin ?emotion= devuelve esas memorias como 'episodios' con valence/arousal/dominance/mood_label/appraisal_reason a null, contaminando el mapa afectivo. El test tests/integration/test_cortex_mind_endpoints.py:314-367 no lo detecta porque solo siembra episodios afectivos (y uno con cortex=False) y asserta len==2. Lo demas de la afirmacion (filtro por emotion, appraisal_reason, aislamiento cross-user) si esta verificado.

### `partial` — Hooks de datos + cliente WS

Faltan los tres helpers que pedía la tarea: `moodLabelColor(label)`, `padToCanvasXY(valence, arousal)` y `trailFromSnapshots(snapshots)` — no existen en ningún fichero (grep → 0), coherente con que el espacio PAD 2D con estela tampoco esté implementado. Tampoco existen los hooks nombrados `useCortexMind` / `useCortexTimeseries` / `useCortexTelemetry` (sustituidos por useQuery/useWebSocket inline, funcionalmente equivalentes para las consultas).

### `partial` — Componente `MindPanel` montado en `app/admin/cortex` (de F1)

Falta el "espacio PAD 2D con estela": no hay canvas ni scatter 2D en ninguna parte (grep de canvas/2D/estela/trail en la página → solo el SVG de la línea de mood). El panel es ES-only: todas las etiquetas están hardcodeadas en castellano (p.ej. :644-647 'Valencia'/'Activación', :727 'Sensaciones (drives)') y el admin-panel no tiene capa i18n, así que el requisito ES+EN no se cumple. No existe el test vitest/RTL pedido (mind-panel.test.tsx).

### `partial` — Suite completa F2 en verde + lint/type

Cuatro huecos frente a la aceptación: (1) no existe ningún chequeo automatizado de `alembic check` / drift — ni test ni paso en .github/workflows/ci.yml; (2) no hay test vitest del panel de mente (solo e2e Playwright); (3) no hay test del registro de la task ni de los settings del worker (ver tarea de la línea 144); (4) la superficie 'espacio PAD 2D con estela' no existe, así que 'copy honesto en TODAS las superficies' se cumple sobre un panel incompleto. Además no pude ejecutar aquí las suites de integración/workers (requieren Postgres+Redis) y el intérprete local ni siquiera resuelve `uuid6`, con lo que tests/unit/test_cortex_affect_policy.py y test_cortex_self_context.py fallan al importar en este sandbox: el 'todo verde' no queda verificado en esta auditoría.

## F3 — identidad

`docs/roadmap/cortex-f3-identidad.md` — 9 casillas abiertas

### `partial` — Modelos ORM CortexIdentity / CortexIdentityHistory

El CÓDIGO es correcto y lo he verificado mejor de lo que lo hace el plan, pero el criterio TDD enumerado NO se cumple: el test no existe y, peor, NINGÚN test del repo nombra las clases. `grep -rn 'CortexIdentity\b' tests/` devuelve CERO resultados: ni tests/unit/test_cortex_identity_model.py (el que pedía el plan) ni ningún otro fichero asserta **tablename**, columnas ni la ausencia de tenant_id. El agente presenta como cobertura equivalente dos cosas que no lo son: (a) tests/integration/test_cortex_identity.py:210-317 sí instancia y mapea los modelos, pero INDIRECTAMENTE (vía ensure_identity/update_identity) y no afirma nada sobre su forma; (b) :144-149 comprueba relrowsecurity=False, que es RLS apagada en la TABLA — no es la defensa que pedía el plan ('comprobar que NO hay tenant_id' en el MODELO): una tabla puede perfectamente tener columna tenant_id y RLS off, así que esa aserción no ejerce el criterio. Lo que sí confirmo del lado del código, ejecutándolo: apps/api-server/src/api_server/db/cortex_identity.py:35 y :79 declaran ambas clases sin TenantScopedMixin (imports en :32 solo Base/TimestampMixin/UUIDPrimaryKeyMixin), **table_args** :45-48 y :89-103 replican los 3 índices, y registrados en apps/api-server/src/api_server/db/models.py:65 + **all**. Importando el módulo real: CortexIdentity cols = [owner_user_id, identity_state, version, updated_by, onboarded_at, id, created_at, updated_at], history cols = [owner_user_id, version, identity_state, diff, updated_by, reason, created_at, id], 'tenant_id' in cols → False; `ruff check` y `mypy` sobre el fichero: limpios. Es decir: la mitad 'modelos importan y mapean; mypy/ruff limpios' del criterio está; la mitad TDD no. Partial, no implemented.

### `partial` — cortex/identity.py — clamp + bound + diff

Tres de los cinco elementos enumerados están impecables, pero la tarea tiene DOS criterios que fallan literalmente, y el propio agente los confiesa en su evidencia (confesarlos no los convierte en cumplidos). (1) FIRMA AUSENTE: el plan enumera `merge_identity_state(current, *, traits=None, baseline=None, narrative=None, ...)` como una de las cinco funciones puras a crear. No existe con ese nombre en apps/api-server/src/api_server/cortex/identity.py (ni en el **all** :483-501). Su función está repartida entre editable_owner_state :276 y apply_reflection_delta :309, ambas puras y correctas, pero es una desviación de firma sobre un entregable explícito. (2) CRITERIO DE PUREZA DEL MÓDULO INCUMPLIDO: el criterio dice literalmente '100% determinista, SIN imports de red/LLM/DB'. El módulo importa sqlalchemy `select` (:34), `IntegrityError` (:35) y `AsyncSession` (:36) más los modelos ORM (:43), y aloja tres corrutinas de acceso a BD: get_identity :134, ensure_identity :140, update_identity :174. Es decir, la capa pura y la capa de persistencia viven en el MISMO fichero — el plan las quería separadas (F3.2 pedía además un `db/cortex_identity_repo.py` que tampoco existe). Las funciones puras lo son; el módulo no. (3) HUECO DE TEST MENOR: el plan pide en tests/unit/test_cortex_identity_dynamics.py cuatro comprobaciones, una de ellas 'compute_diff ignora campos sin cambio'; ese fichero NO importa compute_diff (import :16-23) y no lo prueba: la única aserción del diff está en integración (tests/integration/test_cortex_identity.py:309-311, 'name' not in diff_v2). Lo que SÍ confirmo, ejecutándolo: clamp_traits :234 (a [0,1], sucias→0.5), clamp_baseline :243 (valence/dominance∈[-1,1], arousal∈[0,1]), bounded_update :255 con firma exacta a la pedida (cota BASELINE_MAX_DELTA_PER_REFLECTION=0.05), compute_diff :215; los 25 tests de tests/unit/test_cortex_identity_dynamics.py PASAN (`pytest -q` → 25 passed); ruff limpio; y NO es código muerto: apps/api-server/src/api_server/cortex/affect_store.py:87, apps/workers/src/workers/cortex_affect.py:376-380, apps/workers/src/workers/cortex_curiosity.py:363/517, apps/workers/src/workers/cortex_reflection.py:41-46 y apps/api-server/src/api_server/routers/cortex_mind.py:32-38 lo consumen, y self_context.py:43+:300 usa identity_preamble. Veredicto: comportamiento clamp+bound+diff implementado y vivo, pero la tarea no cubre todos sus criterios de aceptación → partial.

### `partial` — cortex/identity_repo.py — acceso DB con aislamiento explícito

Falta `list_history(session, owner_user_id, limit)`: no existe ninguna función de lectura del histórico (grep 'list_history' solo devuelve db/task_audit_repo.py, otro dominio). El único lector de cortex_identity_history es una query inline dentro del endpoint /journal (routers/cortex_mind.py:231-242), que aplana narrativas y DESCARTA el campo diff — por eso F3.5 tampoco puede exponer el timeline. Además el módulo no está en la ruta que pedía el plan (db/cortex_identity_repo.py) sino mezclado con la capa pura en cortex/identity.py.

### `partial` — cortex/onboarding.py — flujo de autonombrado + valores

Falta la CO-CONSTRUCCIÓN, que era el núcleo de la tarea: el córtex no se autonombra ni propone valores usando el grafo de F1 — no hay ningún turno de propuesta que el owner confirme, solo un formulario que el owner rellena a mano (apps/admin-panel/app/admin/cortex/identity/page.tsx:192-261). Faltan también la función pura propose_identity(turn_result, current_state), el módulo onboarding.py y el endpoint dedicado.

### `partial` — Budget cap + kill-switch en Redis (gobierno ADR 0078)

El criterio 'el bucle NO puede superar el cap' NO se cumple para la reflexión: no existe budget alguno para ella. apps/workers/src/workers/cortex_reflection.py:188-269 (\_reflect_async, el camino del disparo manual desde POST /owner/cortex/reflect) no consulta ni budget ni kill-switch; el camino programado (:151-185) comprueba SOLO el kill-switch (:162) y ningún cap. Es decir, el owner puede pulsar 'Reflexionar ahora' sin tope y el gasto de LLM no se contabiliza en ninguna parte.

### `partial` — Tarea de reflexión workers.cortex_reflect

Cinco huecos frente al enunciado: (a) paso (2) a medias — NO hay chequeo de budget cap en ningún punto (solo kill-switch, y solo en el camino programado :162); (b) paso (3) no usa memorizer.recall(... scopes=['private']) sobre episodios de F1/F2: lee los últimos 20 turnos de cortex*turns con SQL directo (\_load_recent_turns :402-428); (c) paso (4) no usa claude_sdk run_agent(effort=...) sino OllamaProvider.complete() (:113-118 y :289-296) — desviación consciente y documentada (:16-18) pero desviación; (d) paso (8) AUSENTE: no se sacia el drive `coherence` — grep de 'coherence' en el fichero da 0 resultados, no se escribe nada a la Redis de F2; (e) la idempotencia por marca en metadata* de lo ya procesado no existe (dos pasadas seguidas re-sintetizan los mismos 20 turnos).

### `partial` — Router cortex_identity.py (gated)

(a) Falta el endpoint `GET /owner/cortex/identity/history?limit=` — no existe ningún endpoint que devuelva el timeline de versiones con su diff (los 10 verbos del router son mind, affect/timeseries, episodes, journal, curiosity/pursuits, identity GET/PUT, reflect, autonomy GET/PUT); el /journal (:212) lee cortex_identity_history pero deduplica narrativas y descarta el diff. Sin este endpoint el timeline de F3.6 es inconstruible. (b) Contradice el plan en un punto de diseño: el plan exigía 422 al tocar `narrative` (solo la reflexión la muta) y la implementación la hace editable por el owner a propósito (cortex/identity.py:78-84 OWNER_EDITABLE_FIELDS incluye 'narrative'); solo traits/mood_baseline dan 422. (c) Rutas distintas a las diseñadas: PUT /identity en vez de POST /identity/onboarding + PATCH /identity, y POST /owner/cortex/reflect en vez de /identity/reflect-now.

### `partial` — Componente IdentityCard (radar Big-Five + narrativa Markdown + copy honesto)

(a) NO existe apps/admin-panel/app/admin/cortex/identity-timeline.tsx ni ninguna UI de timeline de versiones — ni podría existir: no hay endpoint GET /identity/history que la alimente (ver F3.5). (b) NO existe el helper puro `identityDiffSummary(diff)` (grep en todo apps/admin-panel: 0 resultados) ni su test vitest, que era el único test que esta tarea exigía por nombre. (c) Los rasgos se pintan como barras horizontales (:305-311), no como el radar Big-Five pedido. (d) No es un componente integrado en la segunda columna de apps/admin-panel/app/admin/cortex/page.tsx (la página de F1): es una ruta hermana separada, /admin/cortex/identity.

### `partial` — Promover ADRs y registrar cambios

No hay entrada de changelog de F3: docs/07-changelog/ tiene 43 ficheros y ninguno cubre esta fase (grep de 'Córtex F3'/'cortex-f3' en docs/07-changelog/: 0 resultados); el más próximo, docs/07-changelog/cortex-identidad-real.md:1-8, es el plan POSTERIOR del 2026-07-06 (self-model unificado), no el cierre de F3. Faltan por tanto: promover 0078 a accepted-f3 y arreglar su banner 'proposed', anotar F3 implementada en 0074 (y su banner F1-F5 gated), marcar la Fase 3 en cortex-system-owner.md, corregir mejoras-2026-06-chat-coste-cortex.md, y crear docs/07-changelog/ de F3. El criterio 'estado de los docs coherente con el código' no se cumple: hoy tres documentos afirman que F3 no existe mientras el código está desplegado.

## F4 — autonomía

`docs/roadmap/cortex-f4-autonomia.md` — 12 casillas abiertas

### `partial` — Platform settings de autonomía + budget + circuit-breaker

Faltan 3 de las 7 claves pedidas: CORTEX_CURIOSITY_ENABLED_KEY ('cortex.curiosity_enabled'), CORTEX_CURIOSITY_APPROVAL_GATE_KEY ('cortex.curiosity_approval_gate', que el plan pedía ON por defecto) y CORTEX_CURIOSITY_DAILY_USD_CAP_KEY ('cortex.curiosity_daily_usd_cap'). Grep de 'curiosity_enabled|approval_gate|daily_usd_cap' en todo el árbol .py devuelve cero. El docstring del test dice 'los seis getters' pero solo comprueba cuatro. Sin approval gate no hay Sub-fase 4.0 completa: es la pieza que el propio plan marca como parte del MVP.

### `partial` — Budget gate determinista en Redis (puro, testeable)

Solo existe la dimensión de BÚSQUEDAS; la dimensión de COSTE USD que el plan exige (usd_cap en check_and_reserve, record_spend(cost_usd), campo curiosity_cost_usd_today) no existe en ninguna parte. Consecuencia observable: la columna cortex_curiosity_pursuits.cost_usd nunca se escribe (grep 'cost_usd' en workers/cortex_curiosity.py y en routers/cortex_mind.py → 0), así que el 'coste real de la pasada' del panel es siempre 0 y no hay tope de gasto, solo tope de nº de búsquedas. Vive en cortex/autonomy.py, no en cortex/curiosity/budget.py, y la clave es cortex:budget:{owner}:{kind}:{yyyymmdd} (string) en vez del hash cortex:budget:{owner} — divergencia de forma, no de fondo.

### `partial` — Migración 0092 cortex_curiosity_pursuits

La tabla NO tiene la columna `approved` (Boolean nullable) que la especificación de la tabla exige para el owner-approval gate — ni en la migración 0095 ni en el modelo ORM (grep 'approved' en ambos → 0). Es la causa raíz de que el gate de aprobación no exista ni en el bucle ni en el router. Menor: search_count se declaró Numeric(10,0) en vez de Integer (obliga al int() defensivo en routers/cortex_mind.py:330). El número real es 0095, no 0092, y down_revision es 0094_cortex_identity (renumeración esperada).

### `partial` — Selector de tema determinista (puro)

gather_owner_entities no tiene NINGÚN test propio: grep del símbolo en el repo devuelve solo su definición, el **all** y el uso del worker — no hay el test de integración con caso cross-owner que el plan exige explícitamente. La cobertura es indirecta (el happy path del bucle afirma topic=='rust' y no 'kubernetes' del otro owner). Además, frente a lo pedido: no reusa la normalización de memorizer/recall.py::query_entity_terms (hace su propio strip().lower() en curiosity.py:67) y NO agrega entities de cortex_turns, solo de memory_entries.

### `partial` — Investigador con claude_sdk (egress confiable, degradación limpia) — GATED por ADR 0076

La capacidad existe, los artefactos pedidos no. Falta: (a) el módulo researcher.py con research_topic(provider,\*,topic,model,effort) -> ResearchResult; (b) provider.run_agent con allowed_tools=['WebSearch','WebFetch'] y effort (el punto 3 del ADR, que el propio ADR sigue recomendando cuando haya claude_sdk); (c) la degradación ResearchResult(skipped=True, reason='no_sdk') — hoy no hay rama de provider sin SDK porque nunca se usa el SDK; (d) la contabilidad de Usage.cost_usd: search_count sí se cuenta (:275) pero cost_usd nunca se calcula ni se persiste, así que 'cost_usd>0' de la aceptación es inalcanzable; (e) el test tests/unit/test_cortex_researcher.py con doble de provider.

### `partial` — Escritura de la memoria de aprendizaje (directa, idempotente)

Divergencias de contrato: el módulo es cortex/curiosity.py y no cortex/curiosity/digest_memory.py, la función se llama persist_learning_memory (no persist_learning), devuelve UUID|None (no MemoryEntry) y exige un parámetro tenant_id extra que el plan no contemplaba. Falta el test pedido tests/integration/test_cortex_digest_memory.py y, dentro de él, la mitad cross-owner: ningún test comprueba que la memoria nacida con user_id=owner NO sea visible para otro user bajo RLS — la aceptación 'aislamiento verificado' no tiene prueba.

### `partial` — Tarea Celery workers.cortex_curiosity_loop — GATED por ADR 0078

Falta el paso 7 del plan ENTERO: el owner-approval gate. No hay get_cortex_curiosity_approval_gate, no hay columna `approved`, y ninguna rama deja el pursuit en 'selected' esperando aprobación — la primera búsqueda autónoma sale sin que el owner la apruebe, que era justo la salvaguarda que el ADR 0078 pide como opcional y este plan puso en el MVP. También falta el enable separado cortex.curiosity_enabled (solo se lee autonomy_enabled; el gate extra implementado es web_enabled, no pedido pero razonable) y el record_spend de coste. El caso (f) 'provider sin SDK → skipped no_sdk' no aplica ni se prueba porque no hay camino SDK.

### `partial` — Inyección del tema pendiente en el system_prompt del turno

Solo divergencia de forma: no existe cortex/curiosity/surfacing.py ni las funciones con la firma literal del plan (pending_topic_to_open / augment_prompt_with_curiosity(base_prompt, pursuit, learning_digest, \*, language)); la composición se hizo dentro de self_context.py. La aceptación (se menciona una vez, no se repite, copy bilingüe) está cubierta, aunque ningún test fija la variante EN del label.

### `partial` — Router cortex_curiosity (gated require_system_owner)

De los 4 endpoints pedidos faltan 2: no hay GET /owner/cortex/curiosity/budget independiente (plegado dentro de /autonomy, aceptable) y NO EXISTE POST /owner/cortex/curiosity/pursuits/{id}/approve — grep de 'approve' en cortex_mind.py → 0; sin columna `approved` no hay nada que mover de selected→searching/skipped. El kill-switch es PUT /autonomy en vez de POST /curiosity/kill-switch (divergencia benigna). CortexPursuitItem no expone cost_usd, así que el 'coste' que el plan quería listar no llega a la UI.

### `partial` — UI "Lo que está aprendiendo" (Panel de Mente, F2/F3)

Falta lo verificable: no existe apps/admin-panel/lib/cortex-curiosity.ts ni los helpers puros budgetUsageLabel/pursuitStatusLabel con tests vitest — las etiquetas son un const inline en page.tsx:529 sin ninguna prueba (grep 'pursuit' en lib/cortex.test.ts → 0), así que la tarea TDD queda sin su única pieza testeable. Tampoco hay botón Aprobar/Rechazar (no hay gate ni endpoint). El copy honesto solo se muestra en ES: la API devuelve note_es y note_en (schemas/cortex_autonomy.py:16 y :20) pero la página renderiza únicamente note_es (page.tsx:516) y textos ES fijos (:552), así que 'copy honesto presente en ambos idiomas' no se cumple en la UI. La e2e cortex-mind.spec.ts no toca ninguno de los dos paneles. El coste por pursuit no se muestra (no está en el schema).

### `missing` — Métricas OTEL del bucle (ADR 0078)

No existe ninguna de las 4 métricas pedidas (agentic_cortex_curiosity_runs_total{outcome}, \_cost_usd_total, \_searches_total, \_circuit_open) ni el fichero de test tests/.../test_cortex_curiosity_metrics.py. Nota agravante: aunque se implementase, \_cost_usd_total sería siempre 0 porque el coste nunca se calcula (ver tarea de la línea 104/135). Debería vivir en apps/workers/src/workers/ junto a backup_metrics.py, con su test de render determinista y de no-op si el dir del collector no existe.

### `partial` — Doc + ADR flip

Tres incumplimientos concretos. (1) C:/laragon/python/agent-ai-multitenant/docs/roadmap/mejoras-2026-06-chat-coste-cortex.md:128 sigue diciendo que la Feature 1 tiene 'cero código' (:131) y que 'F1-F5 ... NO implementadas — gated por fase' (:140) — no se actualizó a F4 pese a que el bucle lleva meses en el repo. (2) El ADR 0078 no quedó en 'accepted-f4' sino en 'accepted' a secas, y su cuerpo se contradice con su propio frontmatter: la línea 15 sigue leyendo 'Estado: `proposed`'. (3) Este mismo fichero de fase admite en su banner (línea 28) que los 'Checkboxes de tareas NO re-verificados línea a línea', que es exactamente lo que esta tarea pedía cerrar — de ahí que las 15 casillas sigan sin marcar con el plan declarado pending_human_validation en su frontmatter (línea 3).

## F5 — voz y avatar

`docs/roadmap/cortex-f5-voz-avatar.md` — 12 casillas abiertas

### `partial` — A2. Mapeo puro arousal → speed (afecto → prosodia)

El comportamiento existe y está vivo (voice_affect.py:63-71 y :74-84; invocado en routers/cortex_voice.py:173), pero el criterio de aceptación literal del plan («función pura CUBIERTA AL 100%, monótona y CLAMPEADA») NO se sostiene, y el test citado no ejerce lo que dice ejercer. (1) Cobertura medida, no estimada: `pytest tests/unit/test_cortex_voice_affect.py --cov=api_server.cortex.voice_affect --cov-report=term-missing` → **85.7%, Missing 57, 59** — exactamente las dos ramas de `_clamp` (`if x < lo: return lo` y `if x > hi: return hi`). Ninguna se ejecuta jamás en la suite. (2) `test_out_of_range_arousal_is_clamped` (test_cortex_voice_affect.py:59-67) NO prueba el clamp de la función pura: pasa `arousal=±5.0` a `PADState`, que ya lo recorta a [0,1] en su `__post_init__` (cortex/affective.py:103-108, `object.__setattr__(self, "arousal", _clamp(self.arousal, 0.0, 1.0))`). O sea, `arousal_to_speed` nunca recibe un valor fuera de rango en ese test; lo que se está verificando es el clamp de PADState, no el del mapeo. El propio test lo admite en su comentario (:60-61). (3) El clamp SÍ es alcanzable en producción por la vía del `valence` (lo he ejecutado): `arousal_to_speed(0.0, valence=-1.0)` → raw 0.80 recortado a 0.85; `arousal_to_speed(1.0, valence=1.0)` → raw 1.30 recortado a 1.25. Ningún test conduce a esos extremos (`test_valence_is_a_secondary_modifier_within_band` usa arousal=0.5/valence=±0.8 → 1.01/1.09, dentro de banda). Es decir: la rama que protege la voz de sonar «atropellada» está viva y sin una sola aserción. (4) Menor: `arousal_to_speed` no se importa ni se llama directamente en NINGÚN test (test_cortex_voice_affect.py:18-22 sólo importa SPEED_MIN/SPEED_MAX/voice_params_from_affect); se ejercita sólo de rebote. Y la firma real lleva el kwarg extra `valence` (:63) y punto medio 1.05 en vez del 1.10 del plan — esto último lo doy por bueno porque el plan decía «p.ej.», pero la desviación del kwarg significa que la firma pedida `arousal_to_speed(arousal: float) -> float` no es la real.

### `partial` — B2. Adaptador de turno del córtex para voz (respond + lectura de afecto)

El código está y es correcto y vivo — pero el ÚNICO criterio de aceptación duro que enumera el plan para B2 no tiene test en ninguna parte, ni unitario ni de integración. Verificado que existe: `run_cortex_voice_turn` en cortex/voice_turn.py:100-220 (resuelve tenant :126, crea/reusa hilo :128-132, `append_turn` role="user" :134-140 y role="cortex" :195-211, siempre con `owner_user_id=` explícito), `load_current_affect` :223-250 con fail-open doble (:240 y :248), `affect_frame` puro :253-267. Consumido en routers/cortex_voice.py:62, 172, 188, 230. Los 5+2 tests de tests/unit/test_cortex_voice_turn.py pasan (los he corrido: 24 passed junto con los otros dos ficheros F5). Lo que refuto: (1) Aceptación del plan: «`cortex_respond` persiste EXACTAMENTE UN TURNO por llamada (sin duplicados)». No hay assert de eso en ningún sitio. El unitario (test_cortex_voice_turn.py) no toca `run_cortex_voice_turn` en absoluto — sólo `affect_frame` y `load_current_affect`; su propio docstring (:11-12) delega el pipeline al test de integración. Y el de integración (tests/integration/test_cortex_voice_ws.py) hace `TRUNCATE cortex_turns…` en :133 y **jamás vuelve a consultar la tabla**: cero SELECT, cero COUNT. Si la implementación duplicara filas o no persistiera nada, la suite seguiría verde (el error se tragaría… bueno, no: un fallo daría frame `error`, así que sabemos que el path corre; pero el número de filas es literalmente inobservado). (2) El paso 1 del TDD del plan pedía explícitamente un doble de cerebro scripted a nivel unitario contra el adaptador («un cerebro scripted estilo ScriptedAssistantModel → `cortex_respond` devuelve el texto»). Eso no existe: el `ScriptedAssistantModel` sólo aparece en el test de integración (test_cortex_voice_ws.py:66-68), vía override del WS. (3) Menor, admitido por el propio agente: la firma no es la del plan (`run_cortex_voice_turn(session, model, *, owner_user_id, …)` en vez de `cortex_respond(principal, user_text)`, y `affect_frame(affect, *, language)` en vez de `affect_frame(owner_user_id)`). El plan lo autoriza («si las firmas reales difieren, adaptar el adaptador»), así que no lo cuento como defecto, pero sí implica que la evidencia hay que leerla con el mapeo hecho a mano. (4) Ojo al detalle semántico: la llamada persiste DOS filas (turno `user` + turno `cortex`), no una. Es lo correcto (espeja el chat de F1), pero contradice la letra del criterio y nadie lo ha fijado con un test que decida cuál es la lectura buena.

### `partial` — B3. WS /ws/owner/cortex/voice (gate owner DB-authoritative + frame afectivo)

Gate, frame y registro los confirmo; el TERCER criterio de aceptación del plan es el que no se sostiene, y además el test que se cita como evidencia es VACUO para ese criterio. Confirmado: handler `@router.websocket("/ws/owner/cortex/voice")` en routers/cortex_voice.py:291-330; gate DB-authoritative en :309 (`if not await _is_db_system_owner(principal.user_id): await _reject(ws, "forbidden"); return`) ANTES de construir el cerebro en :313; afecto leído antes de sintetizar en :172-174; `speed` reenviado a la TTS en :212; frame `{type:'affect'}` entre `answer` (:228) y el binario (:232) en :230; `_resolve_voice`/`_SUPPORTED_VOICES`/`_MAX_UTTERANCE_BYTES`/`_reject` importados de assistant_voice en :65-71 y usados en :274; registrado en main.py:50 (import) y :250 (include_router). El módulo importa limpio (lo he importado: `router.routes[0].path == '/ws/owner/cortex/voice'`). Refutación: (1) Aceptación literal: «el `speed` enviado a Kokoro **coincide con `arousal_to_speed(arousal_de_Redis)`**». No hay tal aserción. test_cortex_voice_ws.py:287 sólo comprueba `0.85 <= speed <= 1.25`, que es la BANDA del clamp: cualquier salida de la función la cumple **y el default 1.0 de la TTS también**. Si borrases el cableado afecto→prosodia entero (líneas 172-174 y el `speed=speed` de :212), `HttpTextToSpeech.synthesize` usaría su default `speed=1.0` (voice_clients.py:90) y el test **seguiría verde**. Un test que no puede fallar ante la regresión del criterio no verifica el criterio. El comentario del test (:284-285, «never the forced 1.0 default») afirma justo lo que no asserta. (2) Peor: el camino Redis→prosodia ni siquiera se recorre. El fixture `configured_app` llama a `_flush_redis(test_redis_url)` (:86), así que `read_affect_state` devuelve None y `load_current_affect` cae a BD → sin snapshot → `neutral_affect_state()`. El «arousal_de_Redis» del criterio nunca existe en la prueba; lo que se ejercita es el fail-open, no la modulación. (3) Los otros dos criterios sí los doy por buenos: cross-owner con claim forjado → frame `error` + 1008 y `assert tts.calls == []` (:218-238), y turno completo con las 5 claves del frame (:273-276). Eso es lo que salva la tarea de ser 'missing'; el conjunto queda en 'partial'. (4) Desviación menor no señalada por el otro agente: la voz por defecto sale de `get_settings().cortex_tts_default_voice` (:318, config.py:306), no de `assistant_tts_default_voice` como decía el plan. Es sensato, pero es otra cosa.

### `partial` — C1. affectToVisual puro — PAD → color/expresión

La función pura no gobierna el avatar vivo (duplicación inline en realistic-avatar.tsx, sin test). Falta además el `blinkRate` que pedía el criterio: el parpadeo es un intervalo aleatorio fijo (realistic-avatar.tsx:86, cortex-avatar.tsx:45-51), no depende del arousal; tampoco hay `mouthBias`/`label` en el retorno.

### `partial` — C2. CortexAvatarFace — avatar modulado por afecto

Falta el test de render exigido: no existe `apps/admin-panel/components/cortex/__tests__/` ni ningún \*.test.tsx para cortex-avatar.tsx ni para realistic-avatar.tsx (el único test de la carpeta voice es components/voice/voice-call-shell.test.tsx, que nunca emite un frame `affect`). Además queda un componente duplicado y muerto que conviene borrar o cablear.

### `partial` — C3. CortexVoiceCall — videollamada del córtex con frame afectivo + copy honesto

Falta el test vitest del componente exigido por el TDD (recibir `{type:'affect', valence:-0.6}` → estado + copy honesto; binario → audio): no hay ningún fichero de test para cortex-voice-call.tsx y voice-call-shell.test.tsx no emite frames `affect`. El disclaimer es sólo ES (string fijo); el `mood_label` llega siempre en español porque routers/cortex_voice.py:230 llama `affect_frame(affect)` sin pasar language, aunque el WS ya conoce el idioma de la voz (voice_language(state.voice)).

### `partial` — C4. Página app/admin/cortex integra la videollamada (gated isSystemOwner)

La e2e está ESCRITA PERO ROTA Y NUNCA EJECUTADA. apps/admin-panel/e2e/cortex-voice.spec.ts:23-24 se autodeclara «WRITTEN, NOT run aquí … PENDING HUMAN VERIFICATION», y sus líneas 76 y 80 asertan el testid `cortex-voice-card`, que no existe en ninguna parte de la app (grep en todo admin-panel: sólo aparece en la propia e2e) — el caso del owner fallaría tal cual está. El caso no-owner (:86-95) sí usa testids reales.

### `partial` — D1. compute_retention puro — score de retención

La función existe, es pura y está VIVA (workers/cortex*maintenance.py:190 la importa y :216-222 la llama dentro de `_forget_low_retention`, aplicando soft-delete `row.deleted_at = now` en :225). La protección dura está implementada y testeada. Pero el criterio «score monótono respecto a recencia/frecuencia/**intensidad**» sólo se cumple en 2 de sus 3 dimensiones, y la dimensión de recencia no es la que pide el plan. (1) **La intensidad emocional no está implementada, en absoluto.** El plan pide leer `metadata*.emotion.intensity`; forgetting.py no menciona `emotion`en ninguna línea — usa`metadata*.importance` (`importance_of`, :73-84). No es un renombrado: es otro dato, con otro productor. Y **ningún test de tests/unit/test_cortex_forgetting.py varía la importancia dejando el resto fijo para asserta monotonía**: `test_low_retention_episodic_is_forgotten`(:74) usa importance 0.4 con 365 días y`test_recent_episodic_is_retained`(:88) usa 0.8 con 1 día — dos variables a la vez, no prueban monotonía de nada. El tercer factor del criterio de aceptación queda sin implementar Y sin test. (2) **La recencia se mide desde`created_at`, no desde el último recall** (`recency_factor(created_at, now)`, :60-70, y :117 dentro de `retention_score`). Esto NO es un detalle cosmético forzado por la falta de columna: `\_bump_recall_counters`(cortex/memory.py:150-179) SÍ escribe`metadata*.last_recalled_at`además de`recall_count`(docstring :156,`now_iso`:162) — el dato existe en el JSONB y **nadie lo lee para puntuar**. Consecuencia real: una memoria de hace 2 años recordada ayer sigue puntuando bajo. El caso TDD que el plan exigía («recordada hace poco → score alto») no está cubierto:`test_vieja_no_recallada_cae_y_recallada_se_salva`(:134-158) lo aproxima con`recall_count=5`, que es FRECUENCIA, no recencia de recall. (3) Confirmo que las columnas `last_recalled_at`y`recall_count` **no existen** en packages/shared-db (grep sobre todo el paquete, incluidas alembic/versions/: cero coincidencias) — o sea, D3 está missing, y esa ausencia es la causa raíz de la desviación (2). El agente lo admite para D3 pero lo presenta como neutro para D1; no lo es. (4) Menor: nombre y firma difieren del plan (`retention_score(\*, created_at, now, metadata, recall_frequency)`:103-109 vs`compute_retention(entry) -> float`; `is_protected(metadata)`:122 vs`is_protected(entry)`). Y `PROTECTED_KINDS` (:42) añade reflection/learning sobre el par que pedía el plan — ampliación defendible y testeada (:44-51). Los 10 tests pasan (los he ejecutado: 24 passed en los tres ficheros F5).

### `partial` — D2. Tarea de mantenimiento (Celery beat) — soft-delete/consolidación reversible

El gate NO es el que pide el plan. Sólo se comprueba el kill-switch global `cortex.autonomy_enabled` (cortex_maintenance.py:78-83); nunca se consulta el budget por owner `cortex:budget:{owner}` ni el circuit-breaker de F4, que sí existen (apps/api-server/src/api_server/cortex/autonomy.py:49,:92,:147) y sí los usa la curiosidad (apps/workers/src/workers/cortex_curiosity.py:170-177). Defendible (el sweep no gasta LLM), pero el criterio de aceptación literal no está cumplido ni testeado.

### `missing` — D3. Migración 0092 — columnas de soporte al olvido

El diseño pivotó a JSONB sin dejar constancia en el plan: los contadores viven en `metadata_` (apps/api-server/src/api*server/cortex/memory.py:150-191 `_bump_recall_counters` escribe metadata*.recall*count y metadata*.last_recalled_at con jsonb_set) y el sweep los lee de ahí (cortex_maintenance.py:222). Consecuencias reales: (a) sigue sin existir índice que soporte el barrido — la query se acota a mano con `_FORGET_SCAN_LIMIT = 500` (cortex_maintenance.py:56); (b) `last_recalled_at` se escribe pero NADIE lo lee: forgetting.py calcula la recencia sobre `created_at`. Decidir: escribir la migración (columnas+índice) o cerrar la tarea documentando el diseño JSONB y añadir al menos un índice parcial para el sweep.

### `partial` — E1. Documentación y honestidad

1. Contradicción documental: el CUERPO de ambos ADR sigue diciendo proposed — 0077:15 «> **Estado: `proposed`**» y 0073-modo-voz-asistente-stt-tts-avatar.md:14 «> **Estado: `proposed`.**» frente a `status: accepted` en sus frontmatter (:4). 2) ADR 0073 menciona que el córtex reutiliza la infra de voz (:110) pero NO enlaza el plan F5, y ADR 0077 no tiene ninguna referencia a F5 (grep «F5» → 0 hits). 3) No hay entrada de changelog: C:/laragon/python/agent-ai-multitenant/docs/07-changelog/ sólo tiene cortex-identidad-real.md, ningún cortex-f5\*. 4) «ES+EN cubiertos en el disclaimer» no se cumple: el subtítulo está hardcodeado en español y el mood_label sale siempre en ES porque routers/cortex_voice.py:230 llama affect_frame(affect) sin `language`, pese a que affect_frame lo soporta (cortex/voice_turn.py:253).

### `partial` — E2. Suite completa verde + QA visual

La e2e exigida NO puede estar en verde: apps/admin-panel/e2e/cortex-voice.spec.ts:80 asserta `cortex-voice-card`, testid que no existe en la app (sólo en la propia spec), y su cabecera :23-24 declara «WRITTEN, NOT run … PENDING HUMAN VERIFICATION». Faltan además los tests vitest de C2 y C3. El QA visual humano (avatar en ES+EN, latencia Kokoro, speed confirmado en la imagen pineada) no tiene evidencia registrada; el propio banner del plan (docs/roadmap/cortex-f5-voz-avatar.md:19-28) admite que los checkboxes no se re-verificaron línea a línea.
