# Plan 14 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 14 (Sistema de
Evaluación de Calidad y Estadísticas). Validan lo que no se puede
automatizar de forma aislada: que el **eval en CI bloquea una regresión**
introducida a propósito, que el **shadow eval al 5 % no afecta a
producción**, que las **estadísticas del tenant son accionables** (con
outliers y sugerencias), y que la **exportación PDF/CSV funciona**.

> **Estado del plan**: `pending_human_validation`. Las 16 tareas
> (`task_14_01`..`task_14_16`) y sus tests automáticos están en verde
> (modelos EvalDataset/EvalRun/EvalResult/EvalCriterion, promote-to-
> dataset, CRUD de datasets y criterios, LLM-as-judge con modelo
> distinto, métricas estándar pass-rate/p50-p95/coste/tokens, diff entre
> runs, eval en CI al cambiar prompt, bloqueo de merge por regresión,
> shadow eval 5 %, detección de drift, dashboards de calidad y de
> estadísticas del tenant, outliers + alertas, exportación, comparativa
> cross-tenant solo System Admin, docs + changelog). Estos 4 tests
> humanos son el último paso antes de pasar a `completed`.

> **Nota sobre PDF (task_14_14)**: el formato **PDF está degradado
> honestamente** — la imagen del api-server no incorpora un renderizador
> PDF nativo, así que `format=pdf` devuelve un documento `text/html`
> listo para imprimir ("Guardar como PDF" del navegador) en vez de un
> PDF binario. CSV y XLSX se entregan completos. Tenlo presente al
> validar `human_14_04`.

## TL;DR

No hay `setup_demo_14.py` ni launcher dedicado para este plan: los tests
necesitan un repo/CI real para el eval-on-prompt-change, tráfico de
producción simulado para el shadow eval, y un tenant con histórico para
que las estadísticas sean accionables. El setup es manual:

```powershell
.\scripts\dev\up.ps1     # api-server :8001 + admin-panel :3000 + postgres + redis + (workers para shadow eval)
```

Las pantallas del admin-panel implicadas:

```
http://localhost:3000/admin/evals                       # datasets, eval runs, comparativa de runs (diff)
http://localhost:3000/admin/evals/quality                # dashboard de calidad por agente/proyecto/release
http://localhost:3000/admin/tenant-stats                 # estadísticas del tenant + outliers + exportación
http://localhost:3000/admin/projects/{id}/consumption    # dashboard de consumo del proyecto (13.7)
http://localhost:3000/admin/projects/{id}/runs           # explorador de runs del proyecto (13.8)
```

El eval en CI vive en `.github/workflows/eval-on-prompt-change.yml`.

## Pre-requisitos

| Requisito                                     | Por qué                                                   |
| --------------------------------------------- | --------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                   | api-server + admin-panel + postgres + redis + workers     |
| Un usuario `tenant_admin`                     | Datasets, evals y estadísticas son del tenant             |
| Un usuario `system_admin`                     | La comparativa cross-tenant es solo para System Admin     |
| Un golden dataset poblado                     | `human_14_01` corre el dataset contra el prompt cambiado  |
| CI funcional (push dispara el workflow)       | `human_14_01` valida el bloqueo de merge en CI            |
| Workers de Celery arriba + tráfico real       | `human_14_02` necesita tareas reales para samplear el 5 % |
| Un tenant con histórico (idealmente ~3 meses) | `human_14_03` revisa tendencias temporales y outliers     |
| Un canal de notificación del admin (Plan 10)  | `human_14_02` espera alerta de drift al admin             |

---

## `human_14_01` — Eval CI bloquea regresión

**Qué prueba**: al modificar un prompt de un agente para empeorarlo a
propósito y hacer push, el CI corre el eval automáticamente, marca rojo
y bloquea el merge si la métrica cae más del umbral, con un reporte
detallado de qué tareas del dataset fallaron.

**Precondiciones**:

- Un golden dataset poblado para el agente que vas a tocar.
- Un branch/PR sobre el que hacer push (CI activo, el workflow
  `eval-on-prompt-change.yml` configurado).
- Conocer el umbral de regresión configurado (`task_14_08`).

**Pasos**:

1. En un branch, **modifica el prompt** de un agente global para
   **empeorarlo a propósito** (p.ej. elimina las instrucciones de
   formato/calidad).
2. **Haz push** del cambio: el CI debe **correr el eval automáticamente**
   (job de `eval-on-prompt-change.yml`).
3. Observa el resultado: si la métrica (pass rate) **cae más del
   umbral**, el CI marca **rojo** y **bloquea el merge**.
4. Abre el **reporte detallado** del job: debe listar **qué tareas del
   dataset fallaron** (no solo el agregado).

**Resultado esperado**: el CI corre el eval al hacer push, marca rojo y
bloquea el merge si la métrica cae sobre el umbral, con reporte detallado
de fallos.

**Checklist**:

- [ ] CI corre eval automáticamente al hacer push del cambio.
- [ ] Si la métrica cae más del umbral, CI marca rojo y bloquea merge.
- [ ] Reporte detallado de qué tareas del dataset fallaron.

**Pitfalls conocidos**:

- El **LLM-as-judge usa un modelo distinto** al evaluado (Decisión Clave)
  para evitar sesgo: el eval consume tokens del modelo juez. Si el job
  falla por presupuesto/credenciales, revisa la config del proveedor LLM
  del juez.
- El **bloqueo de merge** se materializa como check requerido en la rama:
  si el PR se puede mergear pese al rojo, comprueba la protección de rama
  en GitHub (el check de eval debe ser obligatorio).
- Si el eval no dispara, el workflow solo corre **cuando cambia un
  prompt** (`task_14_07`): un cambio que no toca el prompt no lo activa,
  es el comportamiento esperado.

---

## `human_14_02` — Shadow eval no afecta producción

**Qué prueba**: con el shadow eval activado al 5 %, las tareas reales se
ejecutan normalmente sin retraso, el 5 % sampleado se replica en
background con el revisor, los resultados aparecen en el dashboard de
calidad, y si se detecta drift llega alerta al admin.

**Precondiciones**:

- Shadow eval activado al 5 % (default).
- Workers de Celery arriba y tráfico real de tareas (ejecuta varias
  tareas para que el 5 % muestree alguna).
- Un canal de notificación del admin configurado (Plan 10) para la
  alerta de drift.

**Pasos**:

1. **Activa el shadow eval** al 5 % y deja que corra tráfico real de
   tareas.
2. Comprueba que las **tareas reales se ejecutan normalmente sin
   retraso**: el shadow NO bloquea la ejecución real (Decisión Clave).
3. Verifica que el **5 % sampleado se replica en background** con el
   agente revisor especializado (revisa la cola/worker de shadow evals).
4. Abre el **dashboard de calidad** (`/admin/evals/quality`): los
   resultados del shadow deben **aparecer** ahí.
5. Si la calidad cae sostenidamente, la **detección de drift**
   (`task_14_10`) debe **alertar al admin** por su canal.

**Resultado esperado**: las tareas reales corren sin retraso, el 5 % se
replica en background, los resultados aparecen en el dashboard, y un
drift dispara alerta al admin.

**Checklist**:

- [ ] Las tareas reales se ejecutan normalmente sin retraso.
- [ ] El 5 % sampleado se replica en background con el revisor.
- [ ] Los resultados aparecen en el dashboard de calidad.
- [ ] Si drift detectado, alerta al admin.

**Pitfalls conocidos**:

- El shadow eval **NO bloquea ejecución real, solo registra resultado**
  (Decisión Clave): si ves que las tareas reales se ralentizan, repórtalo
  — el shadow debe correr en background.
- El 5 % es **muestreo aleatorio**: con poco tráfico puede que no
  muestree nada en una ventana corta. Lanza suficientes tareas para que
  el 5 % capture al menos una.
- La alerta de drift llega por el **canal del admin** (Plan 10): sin
  canal configurado, el drift se registra pero no se notifica.

---

## `human_14_03` — Estadísticas del tenant son accionables

**Qué prueba**: un tenant con histórico revisa sus stats y obtiene tasa
de éxito por agente con tendencia temporal, identificación de outliers
(mejores y peores), coste medio por tipo de tarea, y sugerencias
automáticas de mejora.

**Precondiciones**:

- Un tenant con histórico suficiente (idealmente ~3 meses de uso) para
  que las tendencias tengan datos.
- Login como `tenant_admin` de ese tenant.

**Pasos**:

1. Como `tenant_admin`, ve a `/admin/tenant-stats`.
2. Revisa la **tasa de éxito por agente** con su **tendencia temporal**
   (gráfica a lo largo del tiempo).
3. Comprueba la **identificación de outliers**: agentes que destacan
   (mejores) y que flaquean (peores).
4. Mira el **coste medio por tipo de tarea**.
5. Lee las **sugerencias automáticas** (p.ej. "el agente X está bajando,
   considera revisar su prompt").

**Resultado esperado**: el dashboard muestra tasa de éxito por agente con
tendencia, outliers mejores/peores, coste medio por tipo de tarea y
sugerencias automáticas accionables.

**Checklist**:

- [ ] Tasa de éxito por agente con tendencia temporal.
- [ ] Identificación de agentes outliers (mejores y peores).
- [ ] Coste medio por tipo de tarea.
- [ ] Sugerencias automáticas ("agente X está bajando, considera revisar
      su prompt").

**Pitfalls conocidos**:

- Las **estadísticas y datasets son por tenant** (sus datos, sus
  criterios): un `tenant_admin` solo ve lo suyo. La **comparativa
  cross-tenant es solo para System Admin** (`task_14_15`) — no esperes
  verla como tenant.
- Con histórico escaso, la **tendencia temporal** y los **outliers**
  pueden quedar planos: necesitas datos reales acumulados para que el
  test sea significativo.
- El **coste en moneda del tenant** queda fuera (depende del sistema FX
  no construido, gap del Plan 11): se expone **USD canónico**. No marques
  fallo por no ver la moneda local.

---

## `human_14_04` — Exportación funciona

**Qué prueba**: exportar un reporte mensual genera un PDF con cabecera,
gráficas y tablas, y un CSV con los datos crudos para análisis externo.

**Precondiciones**:

- Datos suficientes para un reporte mensual.
- Login como `tenant_admin`.
- Ten presente la degradación honesta del PDF (ver nota arriba).

**Pasos**:

1. En `/admin/tenant-stats` (o el explorador de runs `13.8`), pide
   **exportar un reporte mensual como PDF** (`format=pdf`).
2. Comprueba el resultado: se genera un documento con **cabecera,
   gráficas y tablas** (en esta versión, un HTML imprimible "Guardar como
   PDF" del navegador — ver nota).
3. Pide la **exportación CSV** (y/o XLSX): debe descargar los **datos
   crudos** para análisis externo.

**Resultado esperado**: el PDF/HTML imprimible incluye cabecera, gráficas
y tablas; el CSV/XLSX exporta los datos crudos.

**Checklist**:

- [ ] PDF generado con cabecera, gráficas, tablas.
- [ ] CSV exportable con datos crudos para análisis externo.

**Pitfalls conocidos**:

- El **PDF está degradado honestamente** (`task_14_14`): `format=pdf`
  devuelve `text/html` listo para imprimir, no un PDF binario, porque la
  imagen del api-server no incluye renderizador PDF nativo (mismo
  criterio que el docs-viewer). No es un bug — usa "Guardar como PDF" del
  navegador.
- **CSV (stdlib) y XLSX (openpyxl)** se entregan completos: si necesitas
  un binario tabular real, exporta XLSX.
- La **columna de coste en moneda del tenant** no aparece en el export
  (depende del FX no construido); solo USD canónico.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/14-evals-estadisticas.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/14-evals-estadisticas.md`](../../07-changelog/).
3. Verifica que el PR `plan/14-evals-estadisticas` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                           | Causa probable                                              | Fix                                                                            |
| ------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------ |
| El eval no corre en CI al cambiar el prompt       | El cambio no toca un prompt o el workflow no está activo    | El workflow solo dispara al cambiar prompt; revisa `eval-on-prompt-change.yml` |
| El merge se permite pese al eval rojo             | El check de eval no es obligatorio en la protección de rama | Marca el check como requerido en GitHub                                        |
| El LLM-as-judge falla por credenciales            | Proveedor LLM del juez sin credencial/presupuesto           | Configura el proveedor del modelo juez (distinto al evaluado)                  |
| El shadow eval no muestrea nada                   | Poco tráfico para un 5 % aleatorio                          | Lanza más tareas reales; el muestreo es aleatorio                              |
| Las tareas reales se ralentizan con shadow on     | (No debería) el shadow debe correr en background            | Repórtalo; el shadow no bloquea ejecución real                                 |
| El export PDF descarga un HTML, no un PDF binario | Degradación honesta: api-server sin renderizador PDF nativo | Es el comportamiento esperado; usa "Guardar como PDF" o exporta XLSX           |

Errores transversales viven en `docs/03-guides/gotchas/`.
