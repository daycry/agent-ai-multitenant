---
title: "ADR 0152: Recall vectorial multi-tenant — índices HNSW parciales, particionado o mitigación"
status: proposed
date: 2026-08-01
deciders: [operador]
relates_to: [0151, 0028]
plan_referenced: prod-13-rendimiento-y-datos
task: [task_prod13_12]
docs_language: es
---

# ADR 0152: Recall vectorial multi-tenant

> **Estado: `proposed`.** La mitigación (opción A) **ya está entregada y en
> producción** desde task_prod13_12; lo que este ADR pide decidir es si se
> compra además la solución estructural, y cuál. No es urgente: es una decisión
> que conviene tomar **antes** de que el corpus sea grande, porque las dos
> opciones estructurales son mucho más caras sobre una tabla llena.

## Contexto: un índice global sobre una tabla multi-tenant

`chunks` tiene un único índice HNSW (`ix_chunks_embedding_hnsw`) sobre
`embedding vector(768)`, compartido por todos los tenants. PostgreSQL resuelve
una búsqueda vectorial **primero por el índice y después por los filtros**: HNSW
devuelve sus `ef_search` vecinos más próximos mirando TODO el índice, y solo
entonces se aplican la RLS por `tenant_id` y el filtro de KBs visibles.

Con un corpus desbalanceado eso produce una pérdida de recall silenciosa. Si un
tenant tiene el 95 % de los chunks, casi todos los candidatos que devuelve el
índice son suyos, el filtro los descarta, y el tenant pequeño recibe **cero
resultados** para una consulta que sí tiene respuesta en su corpus.

**No es una fuga de datos**: la RLS hace su trabajo y nadie ve lo ajeno. Es lo
contrario y por eso es peor de detectar — el RAG contesta «no encuentro nada» y
el usuario no puede distinguir eso de que realmente no haya nada. Un fallo de
seguridad se ve; éste no.

### La medición, que es lo que da la escala

Sobre pgvector 0.8.2, con 2.000 chunks del tenant grande contra 30 del pequeño
(768 dimensiones, todos los del grande más cerca del vector de consulta), pidiendo
10 resultados del tenant pequeño:

| Configuración                           | Resultados devueltos |
| --------------------------------------- | -------------------: |
| `ef_search=40` (default), sin iterative |                **0** |
| `ef_search=100`, sin iterative          |                **0** |
| `ef_search=100` + `iterative_scan`      |               **10** |

La fila del medio es la que importa: **subir `ef_search` no arregla el problema**.
Se puede subir hasta que duela y el recall sigue siendo cero, porque el
desequilibrio no es de cantidad de candidatos sino de dónde están.

Reproducible en `tests/integration/test_vector_recall_desbalanceado.py`, que lleva
el arco sin mitigación dentro como control.

## Lo que ya está hecho (y por qué no cierra el asunto)

`api_server/rag/hnsw.py` fija en la transacción de búsqueda
`hnsw.iterative_scan = relaxed_order` y un `hnsw.ef_search` configurable: cuando
el filtro descarta demasiados candidatos, el índice **sigue recorriendo** en vez
de rendirse con lo que ya tenía. Eso convierte el 0 de la tabla en un 10.

Lo que NO arregla, y es la razón de este ADR: `iterative_scan` paga el
desequilibrio en **latencia**. Cuanto mayor sea la proporción del corpus que hay
que recorrer y descartar, más tarda la búsqueda del tenant pequeño. La curva
crece con el tamaño del tenant MÁS GRANDE de la plataforma, que es una variable
sobre la que el tenant pequeño no tiene ningún control ni visibilidad. Hoy, con
un corpus de desarrollo, no se nota; el día que se note, la tabla ya será grande
y las dos opciones de abajo serán mucho más caras.

## Opciones

### A. Quedarse en la mitigación (statu quo)

Ya entregada. Coste adicional **0**.

Se paga en latencia del tenant pequeño, creciente con el corpus del grande, y en
un tope implícito: `iterative_scan` tiene su propio límite (`hnsw.max_scan_tuples`),
así que con un desequilibrio suficientemente bestia el recall vuelve a caer — solo
que más tarde.

Cuándo es la respuesta correcta: mientras ningún tenant domine el corpus. Es el
caso de hoy.

### B. Índices HNSW parciales por tenant

`CREATE INDEX … ON chunks USING hnsw (embedding vector_cosine_ops) WHERE tenant_id = '<uuid>'`,
uno por tenant.

Se compra el recall perfecto y la latencia independiente del vecino: cada
búsqueda entra por un índice que solo contiene lo suyo, sin post-filtrado que
descarte nada.

Se paga en tres sitios, y el tercero es el que suele matar esta opción:

1. **DDL en el alta de tenant.** Crear un tenant deja de ser un `INSERT` y pasa a
   ser un `CREATE INDEX`, con su bloqueo y su modo de fallo. Rompe la propiedad
   «esquema compartido + RLS» que sostiene todo el modelo multi-tenant (ADR 0028).
2. **El planificador tiene que elegirlo.** Un índice parcial solo se usa si el
   predicado de la consulta **implica** el del índice, y el `tenant_id` de la
   búsqueda llega hoy por `current_setting()` de la RLS, no como constante en el
   SQL. Habría que pasarlo explícito en el `WHERE`, y verificar con `EXPLAIN` que
   se elige — no es negociable, porque si no lo elige el sistema se comporta
   exactamente como hoy pero con N índices que mantener.
3. **N índices que reconstruir.** Un cambio de dimensión, de operador o de
   parámetros HNSW deja de ser una migración y pasa a ser N.

Estimación: 3-5 días, y **crece con el número de tenants para siempre**.

### C. Particionar `chunks` por `tenant_id`, con un HNSW por partición

`PARTITION BY HASH (tenant_id)` (o `LIST` con partición por defecto), y el índice
HNSW declarado sobre la tabla particionada, que PostgreSQL materializa por
partición. El _partition pruning_ deja la búsqueda dentro de la partición del
tenant.

Se compra lo mismo que B —recall perfecto, latencia acotada— **sin DDL por
tenant**: el número de particiones es fijo y se elige una vez.

Se paga:

1. **La PK pasa a ser compuesta**, igual que en el
   [ADR 0151](./0151-retencion-de-tablas-append-only.md): PostgreSQL exige que la
   clave primaria de una tabla particionada incluya la clave de partición. Hay
   que revisar todo lo que referencia `chunks.id`.
2. **La migración mueve datos.** Es un `CREATE TABLE … PARTITION BY` + copia +
   `RENAME`, con ventana de mantenimiento o doble escritura.
3. **HASH obliga a elegir el módulo hoy.** Con 16 particiones y un tenant que
   acapare el 95 %, ese tenant sigue teniendo su partición enorme — pero ya no
   contamina a los demás, que es el problema de este ADR.

Estimación: 5-8 días. Es la misma familia de cambio que el plan
[`part-01-particionado-append-only`](../roadmap/part-01-particionado-append-only.md)
ya está ejecutando sobre otras cinco tablas, así que el equipo tendría el
procedimiento fresco y las trampas ya documentadas.

## Recomendación

**A ahora, C cuando se cumpla un disparador medible.** Y el disparador escrito,
porque «cuando duela» sin número es «nunca hasta que sea carísimo»:

> Pasar a C cuando **el tenant más grande supere el 60 % de los chunks con
> embedding de la plataforma** y el corpus total pase de ~200.000 chunks.

Los dos a la vez, y no cualquiera de los dos: el desequilibrio sin volumen lo
absorbe `iterative_scan` sin que se note, y el volumen sin desequilibrio no
produce el fallo de db-6 (todos los tenants son grandes, los candidatos del
índice ya son suyos).

No se recomienda **B** aunque sea más barata de implementar: mete DDL en el
camino de alta de un tenant, que es el sitio con peor relación entre lo que se
gana y lo que se puede romper, y su coste operativo no deja de crecer.

Lo que hay que instrumentar **para que la decisión se pueda tomar**, y que hoy
no existe: una métrica del reparto del corpus por tenant. Sin ella el disparador
de arriba no es comprobable y este ADR se queda en literatura. Es barato —una
consulta agregada en el job de mantenimiento— y es el único trabajo que esta
recomendación pide hacer ya.

## Consecuencias de NO decidir

Es la opción por defecto y tiene coste, así que conviene nombrarlo: se sigue
acumulando corpus sobre un índice global, la latencia de los tenants pequeños se
degrada sin que nadie la mire, y el día que haya que particionar `chunks` será
sobre una tabla mucho más grande — o sea, con ventana de mantenimiento más larga
y más riesgo que hoy.
