---
title: "ADR 0153: Qué hace el gate con una categoría que la política no lista"
status: accepted
date: 2026-08-01
deciders: [operador]
relates_to: [0016, 0020, 0102, 0104, 0135]
plan_referenced: prod-03-guardrails-validacion-humana
task: [task_prod03_03]
docs_language: es
---

# ADR 0153: Qué hace el gate con una categoría que la política no lista

> **Estado: `accepted` (firmado el 2026-08-02).** Opción (D): esqueletos completos
> sobre las 13 canónicas, más la clave `unlisted_category`. Se tocan TODOS los
> proyectos y solo cambia el comportamiento de los de producción — el detalle
> está en § «Decisión del operador».
>
> Lo que sigue describe el problema tal como se planteó, y las opciones se
> conservan íntegras: era una decisión de producto con consecuencias operativas
> medibles en las dos direcciones —la opción segura para y encola runs, la
> cómoda deja pasar acciones sensibles sin que nadie las vea—, y un ADR sin las
> alternativas descartadas no deja auditar la decisión, solo obedecerla.

## El hecho que la motiva, y por qué no es teórico

`requires_human_approval` (`api_server/db/approval_repo.py:116`) y su espejo del
sandbox `requires_human` (`agent_runtime/approval.py:150`) resuelven así una
categoría que la política del proyecto **no menciona**:

```python
return str(categories.get(category, "auto")) == "human_required"
```

Es decir: **fail-open**. Lo que la política no nombra, corre sin humano.

El [ADR 0104](./0104-default-approval-policy-preset.md) (`accepted`,
2026-07-07) descartó expresamente hacer falta una clave `unlisted_category`, con
este argumento textual:

> «todos los presets construyen sus `decisions` sobre `_all(CATEGORIES, ...)`,
> así que el mapa cubre las 13 categorías canónicas».

**Ese argumento es cierto para `seeds/builtin_approval_policies.py` y falso para
lo que de verdad acaba en `projects.human_approval_policy`.** Los proyectos que
nacen de una plantilla del catálogo no copian un preset: copian
`_POLICY_DEV_SKELETON` (`seeds/builtin_project_templates.py:62-70`), que lista
**cuatro** claves:

```python
_POLICY_DEV_SKELETON = {
    "preset": "development",
    "categories": {
        "code_changes": "auto",
        "git_push": "human_required",
        "external_http": "human_required",   # ← NO es una categoría canónica
        "secrets_access": "human_required",
    },
}
```

Y `human_approval_policy` está en `_TEMPLATE_INHERITED_FIELDS`
(`routers/projects.py:134-142`), así que **la adopción de plantilla lo hereda
tal cual**. Resultado, hoy, en un proyecto creado desde cualquiera de las siete
plantillas que usan el esqueleto:

| Categoría canónica       | Qué decide la política heredada |
| ------------------------ | ------------------------------- |
| `code_changes`           | `auto` (explícito)              |
| `git_push`               | `human_required` (explícito)    |
| `secrets_access`         | `human_required` (explícito)    |
| `git_commit`             | **`auto` por omisión**          |
| `external_http_get`      | **`auto` por omisión**          |
| `external_http_post`     | **`auto` por omisión**          |
| `data_migration`         | **`auto` por omisión**          |
| `production_deploy`      | **`auto` por omisión**          |
| `infra_provision`        | **`auto` por omisión**          |
| `secret_rotation`        | **`auto` por omisión**          |
| `external_communication` | **`auto` por omisión**          |
| `data_export_pii`        | **`auto` por omisión**          |
| `user_management`        | **`auto` por omisión**          |

Diez de trece. Y la clave `external_http` del esqueleto **no gatea nada**: no
existe en `APPROVAL_CATEGORIES`, así que ningún `review()` la consulta jamás. Es
una intención escrita que no llegó a ser una regla — el mismo modo de fallo del
hallazgo g6, en pequeño y todavía vivo.

Las dos plantillas que se declaran `production` (`legacy-migration`,
`devops-bootstrap`) construyen sus `categories` **encima** de ese esqueleto, así
que arrastran los mismos diez huecos y añaden dos o tres decisiones. Un proyecto
que la UI presenta como «Producción» deja `production_deploy` gateado y
`external_communication`, `data_export_pii` y `user_management` en auto.

El [ADR 0135](./0135-que-autoriza-una-aprobacion-humana.md) (`accepted`) dejó
esta decisión fuera de su alcance y la difirió «al ADR de política de fallo del
motor». Ese ADR es el [0102 D5](./0102-cableado-motor-guardrails-runtime.md),
que trata el fallo de un **check del motor de guardrails** y no dice nada del
vocabulario de la política de aprobación humana. O sea: la decisión quedó
apuntada en un sitio que no la contenía. Este ADR existe para que no siga ahí.

## Lo que NO está en discusión

- El motor de guardrails ya tiene su política de fallo decidida e implementada
  (ADR 0102 D5, `on_error` por check, `block` por defecto para los `locked`).
  Esto es otra cosa: la **validación humana** del principio rector nº11.
- El default de plataforma para un proyecto **sin** política ya está resuelto
  por el ADR 0104: hereda el preset `development`. El agujero de aquí es el
  proyecto que **sí** tiene política, pero incompleta.

## Opciones

### (A) Dejarlo como está: lo no listado corre en `auto`

- **Coste de implementación**: cero.
- **Coste real**: los diez huecos de arriba siguen abiertos en todo proyecto
  nacido de plantilla, y el operador no tiene forma de enterarse salvo leyendo
  el JSONB. Un proyecto marcado «Producción» permite exportar PII y crear
  usuarios sin revisión.
- **Cuándo es defendible**: si se acepta que la política del proyecto es
  responsabilidad de quien la escribe y la plataforma no opina.

### (B) Arreglar solo los esqueletos de las plantillas

Reescribir `_POLICY_DEV_SKELETON` (y los dos derivados) sobre las 13 canónicas,
con `external_http` sustituido por `external_http_get` / `external_http_post`.

- **Coste**: ~1 h de código + una migración de datos para los proyectos que ya
  copiaron el esqueleto (si no, los existentes se quedan como están).
- **Qué NO arregla**: la política escrita a mano por un tenant admin vía API
  sigue pudiendo omitir categorías, y el default sigue siendo `auto`. Cierra el
  caso conocido, no la clase.
- **Riesgo operativo**: los proyectos de plantilla empiezan a gatear diez
  categorías más. `external_http_post` y `external_communication` son las que
  más van a doler en runs autónomos.

### (C) Clave `unlisted_category: auto | human_required` en la política

Lo que proponía el plan prod-03 (decisión clave 3): `requires_human_approval` y
`requires_human` leen esa clave en vez del `"auto"` fijo. Se siembra
`human_required` en `production` y `customer-external`, y `auto` en `sandbox` y
`development`.

- **Coste**: ~0,5 día. Dos funciones espejo (api-server + sandbox, que no se
  importan entre sí), los 4 seeds, un test de contrato y la UI de la política
  para que la clave sea visible y editable.
- **Qué arregla**: la clase entera. Una política incompleta bajo un preset
  estricto pasa a fallar **cerrado**, venga de donde venga.
- **Riesgo operativo**: es el que hay que medir. Un proyecto
  `customer-external` con política incompleta pasa de no parar nada a parar
  **todo lo que no esté listado**. Con el bucle aprobar→re-aparcar ya cerrado
  (ADR 0135) eso no es un livelock, pero sí una cola de aprobaciones que alguien
  tiene que atender.

### (D) (C) + (B): la clave y además esqueletos completos

- **Coste**: ~1 día.
- Es lo más coherente: la clave cubre la clase, y arreglar los esqueletos evita
  que la clave tenga que actuar en el caso más común (donde actuaría de golpe
  sobre diez categorías a la vez, que es justo el escenario que asusta).

## Recomendación

**(D)**, y en este orden: primero (B) —que es una corrección de datos con efecto
acotado y auditable, categoría a categoría— y solo después (C), que es la red
para lo que se escriba a mano en el futuro. Hacer (C) sin (B) convierte un
esqueleto incompleto en un muro; hacer (B) sin (C) deja la clase abierta.

Y una petición concreta al firmar: **decidir también qué pasa con los proyectos
que ya existen**. Migrar sus políticas cambia el comportamiento de runs en
marcha; no migrarlas deja los huecos donde están. Ninguna de las dos es
gratis, y elegir «no decidir» es elegir la segunda.

## Decisión del operador (2026-08-02)

**Opción (D)**: se completan los esqueletos de plantilla sobre las 13 canónicas
**y** se añade la clave `unlisted_category`. Implementado y desplegado en el
árbol; este documento llegó tarde a decirlo, que es el pecado que esta casa
persigue — se corrige el 2026-08-10.

### Sobre los proyectos que ya existen, que es lo que el ADR pedía decidir

La primera propuesta separaba mal dos cosas y **lo corrigió el operador**:
«completar» una política y «endurecerla» no son lo mismo. Dejar los proyectos de
desarrollo sin tocar los habría dejado con cuatro categorías escritas, nueve
implícitas y sin `unlisted_category` — o sea, su comportamiento decidido por un
default del código en vez de por su política, que es exactamente el estado
indefinido que este ADR venía a cerrar.

Lo firmado:

- **Se tocan TODOS los proyectos.** Al terminar, ninguna política tiene
  categorías implícitas.
- **Solo cambia el comportamiento de `production` y `customer-external`.** En
  `development` y `sandbox` las categorías ausentes se escriben con `auto`, que
  es lo que ya hacían de facto: la política pasa de implícita a explícita sin que
  nada se comporte distinto.
- La migración **0133** lo aplica con dry-run previo
  (`api_server.cli audit-approval-policies`), y dos tests lo fijan: que no queda
  ninguna política incompleta, y que los proyectos de desarrollo deciden
  EXACTAMENTE lo mismo antes y después.

### Y la decisión que se tomó de más, porque hacía falta

Al revisar qué gatea de verdad cada categoría apareció que **solo 5 de las 13
tienen alguna herramienta detrás**; las otras ocho no las puede disparar nada
hoy. Con eso a la vista, el operador decidió además que
**`external_http_post` pase a `auto` en el preset `development`**: esa categoría
cubre TODAS las tools MCP del proyecto, que se dan de alta como `sandboxed`, así
que gatearla haría que cada integración pidiese aprobación desde el primer día.

El contrapeso queda escrito: `external_communication` y `data_migration` —las dos
que SALEN del proyecto— sí se gatean en desarrollo. Y la tensión con el hallazgo
g6 está anotada en
`tests/unit/test_mcp_tool_approval_category.py`, no enterrada.

## Consecuencias si se acepta

- `task_prod03_03` del plan prod-03 queda desbloqueada y se puede cerrar.
- El ADR 0104 necesita una nota: su §«Sin brecha de categoría no listada» es
  correcta para los presets del catálogo y **no** para los esqueletos de las
  plantillas de proyecto, que es lo que acaba en la BD.
- Un test de contrato nuevo tendría que pinear que **toda** política sembrada
  —preset o esqueleto de plantilla— cubre las 13 canónicas, para que esto no
  pueda volver a divergir en silencio.
