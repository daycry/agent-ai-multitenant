# Verificar antes de implementar

Guía de **prácticas de trabajo**, no de toolchain. Las trampas del toolchain
viven en [gotchas/](./gotchas/); esto es la otra mitad: los modos de fallo que no
producen un error, sino trabajo perdido o —peor— confianza injustificada.

Todo lo de aquí salió de fallos reales en este repositorio, y cada apartado dice
cuál.

---

## 1. Un plan «pendiente» miente más de lo que parece

Los checkboxes y los `status:` del roadmap envejecen. La causa es estructural:
cuando un ADR posterior resuelve o **rechaza** un item, el plan que lo listaba
rara vez se actualiza.

Medido el 2026-07-26: de **21 tareas sin marcar** en tres planes `in_progress`,
la mayoría estaban hechas, y dos estaban **explícitamente rechazadas** por el
ADR 0103 (`G1` abre un hueco de seguridad; `G9` se desaconseja a sí misma).
Implementarlas «porque estaban en el plan» habría metido una regresión y trabajo
que nadie quería.

**Práctica**: antes de implementar una tarea de un plan, comprobar contra el
código y contra los ADR posteriores que (a) sigue sin hacer y (b) sigue siendo
buena idea. El recon de premisas de la remediación de 2026-07-25 dio **9 de 10
premisas `partly`**.

## 2. Un test puede fijar el defecto

El modo de fallo más caro que hemos tenido: un test que **documenta el
comportamiento observado** sin preguntarse si es el correcto convierte un fallo
en contrato, y encima lo protege de futuros arreglos.

Casos reales:

- **Cuatro tests** afirmaban que la credencial de `claude_sdk` aterrizaba en
  `os.environ`. Pasaban en verde mientras la fuga existía (ADR 0076).
- En dos de mis tres regresiones de julio, **el test que escribí bendecía la
  generalización equivocada** en vez de cuestionarla.

**Práctica**: al escribir un test sobre comportamiento existente, preguntarse
«¿esto es lo que DEBE pasar, o solo lo que pasa?». Y al tocar un test que ya
existe, leer por qué se escribió antes de adaptarlo a lo nuevo — si hay que
cambiar la aserción para que pase, o el código está mal o el test lo estaba.

## 3. Cuidado con generalizar una inferencia que solo valía en un caso

Las tres regresiones de julio de 2026 tienen la MISMA forma:

| Inferencia                              | Dónde falla                       |
| --------------------------------------- | --------------------------------- |
| «sin ejecución ⇒ el run no arrancó»     | falso en la ruta de agente humano |
| «sin restricción ⇒ puede llamarlo todo» | falso cuando no hay `tool_specs`  |
| «el lector honra la cobertura»          | falso si el cliente la escribe    |

**Práctica**: ante una premisa del tipo «si no hay X entonces Y», buscar
activamente el caso donde **X falta por diseño**. Suele existir y suele ser el
que rompe.

## 4. Una guarda que no puede fallar no es una guarda

Un test estático que busca infractores pasa **vacíamente** el día que el
descubrimiento deja de encontrar nada: cero coincidencias, cero infractores,
verde. Envejece sin avisar.

**Práctica**: todo test de inventario o guarda estática lleva una aserción de
que **encontró algo**:

```python
assert seen >= 3, f"la guarda dejó de encontrar los constructores (vio {seen})"
assert not offenders, f"...: {offenders}"
```

Y el corolario: **una suite que siempre falla tampoco es una suite**. Los cuatro
rojos crónicos de `tests/security/` tapaban dos hallazgos reales; tres eran un
único falso positivo.

## 5. El patrón dominante de esta base: mecanismo entregado, cero llamantes

La auditoría de 2026-07-25 concluyó que **no falla el diseño, falla el cableado
del último tramo**. Una y otra vez, el mecanismo estaba construido entero y no lo
llamaba nadie, o producía un dato que ninguna pantalla leía:

- el subsistema de evals: 7 módulos, 7 tablas, 18 endpoints, dashboard… y las
  siete tablas vacías, porque no había productor;
- `record_shadow_eval`: completo desde el Plan 14, sin un solo llamante;
- `EvalRun.subject_prompt_version`: nadie lo poblaba, así que la calidad se medía
  sin poder atribuirla a un cambio;
- `eval_results`, `compute_plan_progress`, `pr_url`: escritos y nunca leídos.

**Práctica**: una feature no está hecha cuando el mecanismo existe, sino cuando
alguien lo llama y alguien ve el resultado. Al cerrar una tarea, seguir el dato
de punta a punta: quién lo produce, quién lo persiste, qué pantalla lo enseña.

## 6. Una divergencia «solo documental» puede tener aristas ejecutables

El ADR 0117 (c) estimaba «coste: cero código» para consolidar el frontend y
borrar `apps/web-app`, una carpeta que solo tenía `.gitkeep`.

Escondía un fallo de recuperación ante desastres:
`Settings.restore_app_services` incluía `web-app`, un servicio que no existe en
ningún compose. `docker compose stop` devuelve ≠ 0 ante un servicio desconocido y
la restauración **elevaba en el paso 3, antes de restaurar nada**. Solo se
manifiesta ejecutando la restauración de verdad.

**Práctica**: al retirar algo «que no existe», hacer `grep` de su nombre en TODO
el repo, no solo en la documentación. La lista de servicios de un runbook es
código, aunque parezca prosa.

## 7. Verificar también la premisa de la prudencia

La opción conservadora también se apoya en supuestos, y también hay que
comprobarlos. Iba a dejar `GET /agents/model-options` deprecado en vez de
borrarlo «para no romper un SDK de ahí fuera»; al comprobarlo, los SDK se generan
**solo del OpenAPI v1** y esa ruta vive fuera de `/api/v1`. No había contrato que
proteger, y mantenerla conservaba una forma de elegir el proveedor equivocado.

**Práctica**: «por si acaso» es una hipótesis. Si es barata de comprobar,
compruébala antes de pagar su coste.

## 8. Leer el contrato antes de escribir el adaptador

Escribí un juez de evals con un método síncrono que devolvía tuplas. El
`Protocol` del motor era **async** y devolvía dataclasses. Media hora perdida en
algo que estaba escrito treinta líneas más arriba en el mismo fichero.

**Práctica**: abrir el `Protocol` / la clase base / el doble de test existente
antes de escribir la implementación. El doble de test (`ScriptedJudgeModel`) es la
mejor documentación del contrato que hay, porque compila.

---

## Relacionado

- [gotchas/](./gotchas/) — trampas del toolchain, con síntoma + causa raíz + fix.
- [`docs/context/conventions.md`](../context/conventions.md) — convenciones de
  código y commits.
- `CLAUDE.md` — protocolo de roadmap y principios rectores.
