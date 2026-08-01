---
title: "ADR 0151: Retención de las tablas append-only — borrar, archivar o particionar"
status: proposed
date: 2026-08-01
deciders: [dirección, operador]
relates_to: [0028, 0116, 0126, 0149]
plan_referenced: prod-13-rendimiento-y-datos
task: [task_prod13_15, task_prod13_18]
docs_language: es
---

# ADR 0151: Retención de las tablas append-only

> **Nace `proposed` y tiene que seguir así hasta que un humano elija.** No es una
> decisión técnica disfrazada: cuánto tiempo se guarda una auditoría es una
> política de cumplimiento, y borrar `audit_log` a los N meses puede ser
> exactamente lo que un contrato prohíbe. Quien escribe el código no tiene esa
> información. Lo que sí se podía implementar sin la decisión ya está en el árbol
> (§ «Lo que ya no depende de esta decisión»).

## Contexto: seis tablas que solo crecen

El sistema tiene **seis familias append-only**: filas que se insertan y no se
borran nunca, por diseño. Nadie ha decidido nunca hasta cuándo.

| Tabla                  | Qué guarda                                 | Qué la hace pesada                            |
| ---------------------- | ------------------------------------------ | --------------------------------------------- |
| `executions.steps_log` | La traza completa de cada turno del agente | JSONB con prompts y salidas de tool           |
| `audit_log`            | Quién hizo qué en el panel                 | Volumen, no tamaño de fila                    |
| `guardrail_events`     | Cada disparo de guardrail (4 puntos/ciclo) | Un run con muchas tools genera decenas        |
| `notification_logs`    | Cada notificación enviada + su contenido   | Contenido del mensaje desde la migración 0113 |
| `llm_usage_events`     | Consumo LLM de consumidores no-run (0116)  | Una fila por llamada del asistente/córtex     |
| `task_audit_events`    | Transiciones de estado de cada tarea       | Volumen                                       |

### La medición, que es lo que da la escala

Sobre la instancia de desarrollo del 2026-08-01 (180 runs, seis semanas desde el
`2026-06-29`):

```
steps_log total = 1.672 KiB     heap+toast de executions = 2.208 KiB   → 76 %
tamaño medio de steps_log por run = 9.513 B     máximo observado = 64.576 B
fila media completa de executions = 10.843 B
```

**Tres cuartas partes de la tabla `executions` son `steps_log`.** No es una
intuición: es la medida. Y estos son runs de desarrollo — un run de producción
con 50 iteraciones y contexto largo está en el extremo alto del rango, no en la
media.

Extrapolación honesta, con la aritmética a la vista para que se pueda discutir:

| Ritmo                | Runs/año | `steps_log`/año | Comentario                                   |
| -------------------- | -------- | --------------- | -------------------------------------------- |
| 20 runs/día (hoy×10) | 7.300    | ~66 MiB         | Despreciable: no justifica ninguna obra      |
| 200 runs/día         | 73.000   | ~660 MiB        | Empieza a notarse en backup y restauración   |
| 2.000 runs/día       | 730.000  | ~6,6 GiB        | El `pg_dump` nocturno pasa a durar de verdad |

La conclusión que sale de aquí y conviene decir en voz alta: **hoy el problema no
es el disco, es el tiempo de backup/restauración y el coste de las consultas que
tienen que atravesar la tabla**. Quien decida esto no está eligiendo cuánto
espacio ahorrar, sino cuánto tarda una restauración de emergencia.

### Por qué no se puede posponer indefinidamente

Tres consecuencias ya visibles, ninguna teórica:

1. **El listado de runs paga `steps_log` aunque no lo enseñe.** `_fetch_runs`
   cargaba la entidad `Execution` completa; el export llega a 5.000 filas, o sea
   ~50 MiB de JSONB materializados en el proceso para producir un CSV que no
   contiene ni un byte de esa traza (hallazgo perf-6). Eso ya está arreglado
   (§ abajo), pero el arreglo es un parche sobre la causa: la tabla es enorme.
2. **El backup crece con la historia, no con el estado.** Un bundle que tarda una
   hora en restaurarse es un RTO de una hora aunque el 95 % de lo restaurado sean
   trazas de runs de hace ocho meses que nadie va a leer (relacionado: ADR 0149).
3. **La auditoría sin política de retención no es más segura, es más frágil.**
   Guardar para siempre suena conservador hasta que la tabla es tan grande que
   una consulta de auditoría no termina.

## Decisión que hay que tomar

**¿Cuánto se retiene cada familia append-only, y qué se hace con lo que sale de
la ventana?** Tres opciones, con su coste real.

### Opción A — Borrado puro pasado un plazo

Un beat borra por lotes lo anterior a N meses, tabla por tabla, con su propio
plazo configurable.

- **Coste de construcción**: bajo. La infraestructura ya existe (la purga de
  soft-borrados de `task_prod13_14` es el mismo patrón: corte por fecha, lotes,
  dry-run, recuento por tabla). ~1 día.
- **Coste operativo**: ninguno. No añade dependencias.
- **Lo que se pierde**: irreversible. Una investigación a los 14 meses sobre algo
  ocurrido a los 13 no tiene datos.
- **Riesgo regulatorio**: es la opción que puede incumplir un contrato sin que
  nadie se entere hasta que lo pidan.

### Opción B — Archivado a MinIO y después borrado

Antes de borrar, el lote se serializa (NDJSON comprimido, una clave por
tabla/mes) a un bucket de MinIO con su propia política de ciclo de vida.

- **Coste de construcción**: medio. Serializador + clave estable + verificación
  de que el objeto se escribió ANTES de borrar la fila (si no, es la opción A con
  pasos extra). ~2,5 días.
- **Coste operativo**: MinIO pasa a ser parte del camino de cumplimiento, así que
  entra en el backup y en la prueba de restauración con el mismo rango que la BD.
  Y aparece una segunda copia de datos sensibles (prompts, contenido de
  notificaciones) fuera de PostgreSQL: hay que cifrarla y controlar su acceso.
- **Lo que se gana**: la consulta histórica sigue siendo posible, aunque deje de
  ser SQL — hay que reingerir el objeto para responderla.
- **Trampa a nombrar**: un archivado que nadie ha probado a leer es peor que no
  archivar, porque da una falsa sensación de que el dato está. Si se elige B, el
  ensayo de lectura entra en el `restore-drill` (ADR 0126) o no vale.

### Opción C — Retención infinita con particionado nativo por rango

`executions`, `audit_log`, `guardrail_events`, `notification_logs` y
`llm_usage_events` pasan a tablas particionadas por mes (`PARTITION BY RANGE
(created_at)`). No se borra nada: las particiones antiguas se pueden `DETACH` y
mover a otro tablespace, y las consultas recientes solo tocan una o dos.

- **Coste de construcción**: alto. Convertir una tabla existente en particionada
  no es una migración trivial: exige tabla nueva + copia + intercambio, y en
  PostgreSQL **la clave primaria de una tabla particionada debe incluir la clave
  de partición**, así que `executions.id` pasaría a ser `(id, created_at)` y hay
  que revisar todas las FK que apuntan a ella. Se necesita además un job que
  cree la partición del mes siguiente, porque una inserción sin partición falla.
  ~5-8 días, con un downgrade que hay que probar de verdad.
- **Coste operativo**: la RLS y los índices se declaran por partición; el modelo
  mental del operador se complica.
- **Lo que se gana**: cero pérdida de datos y la mejor característica de
  rendimiento a largo plazo. Es la única opción que sigue siendo buena a 2.000
  runs/día.
- **Cuándo NO vale la pena**: a 20 runs/día es sobreingeniería cara.

## Recomendación (argumentada, no vinculante)

**Un híbrido A + C por familia, escalonado en el tiempo. Ahora: A para todo
menos `audit_log`; `audit_log` sin tocar hasta que exista un requisito escrito.**

El razonamiento, en tres pasos:

1. **Las familias NO son equivalentes y tratarlas igual es el error de base.**
   `guardrail_events` a los seis meses es ruido; `audit_log` a los seis meses
   puede ser la única prueba de quién aprobó un despliegue. Un plazo único
   obliga a elegir el más conservador para todo, que es lo que lleva a la
   parálisis actual (retención infinita por omisión).

   Propuesta de plazos, para que el operador tenga algo concreto que corregir:

   | Tabla                  | Plazo propuesto | Por qué                                                                                                                                                                             |
   | ---------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `executions.steps_log` | 90 días         | Es traza de depuración; a los tres meses el run ya se cerró o se rehízo. **Se compacta el campo, NO se borra la fila**: los tokens, el coste y el veredicto se quedan para siempre. |
   | `guardrail_events`     | 180 días        | Sirve para ajustar reglas; el ajuste ocurre en semanas.                                                                                                                             |
   | `notification_logs`    | 180 días        | Contenido del mensaje incluido (0113): cuanto menos tiempo, mejor.                                                                                                                  |
   | `llm_usage_events`     | 400 días        | Alimenta la facturación: hay que poder cerrar un ejercicio completo.                                                                                                                |
   | `task_audit_events`    | 400 días        | Igual: explica la historia de una tarea durante el año fiscal.                                                                                                                      |
   | `audit_log`            | **sin decidir** | Requisito de cumplimiento; no lo puede fijar quien escribe el código.                                                                                                               |

2. **Compactar `steps_log` no es lo mismo que borrar el run**, y es la
   distinción que hace barata la opción A aquí. La fila de `executions` se queda
   entera: `total_tokens`, `total_cost_usd`, `status`, `finish_status`, las
   marcas de tiempo. Lo que se vacía es el JSONB —el 76 % medido— y se sustituye
   por un resumen (`{"compacted_at": …, "iterations": N, "bytes_freed": B}`) para
   que la UI pueda decir «la traza de este run se compactó» en vez de fingir que
   nunca hubo una.

3. **C se difiere hasta que un número lo pida**, no hasta que alguien lo intuya.
   Disparador propuesto y medible: `pg_total_relation_size('executions') > 20
GiB` **o** duración del `pg_dump` nocturno > 30 min. Escribirlo como umbral
   evita las dos formas de equivocarse: particionar de más hoy y descubrirlo
   tarde mañana.

Y la razón de fondo para no recomendar B: introduce una segunda copia de datos
sensibles fuera de PostgreSQL, con su cifrado, su control de acceso y su prueba
de restauración, **para responder una pregunta que nadie ha hecho todavía**. Si
llega el requisito de conservar `audit_log` más allá de lo que la BD aguanta, B
es la respuesta correcta — y entonces se construye, con el requisito escrito
delante.

## Lo que ya no depende de esta decisión

Para que la parálisis de esta decisión no bloquee lo que sí era implementable:

- **La purga de filas soft-borradas** (`task_prod13_14`) está entregada y no
  toca ninguna tabla append-only: `workers/maintenance/purge.py`, con dry-run por
  defecto y una allowlist de dos raíces con las exclusiones justificadas por
  escrito.
- **El listado y el export de runs ya no materializan `steps_log`**
  (`task_prod13_18`, hallazgo perf-6): `routers/tenant_stats.py` selecciona
  columnas escalares explícitas. El export de 5.000 filas dejó de arrastrar el
  JSONB que no publica.
- **El GC de conocimiento** (G-03) ya purga documentos soft-borrados vencidos con
  su blob.

## Consecuencias de NO decidir

Es la opción por defecto y tiene coste, así que conviene nombrarlo: se sigue
acumulando, el bundle de backup sigue creciendo con la historia y el RTO de una
restauración de emergencia empeora mes a mes sin que nadie lo mida. El día que
duela, la migración a C será sobre una tabla mucho más grande que hoy — o sea,
más cara y con más riesgo que ahora.

## Qué hace falta para cerrar este ADR

1. El plazo de `audit_log`, o la confirmación escrita de que no existe requisito.
2. Aceptar (o corregir) los cinco plazos propuestos del punto 1.
3. Confirmar que compactar `steps_log` conservando la fila es aceptable para el
   soporte de nivel 2, que es quien lee esas trazas.
