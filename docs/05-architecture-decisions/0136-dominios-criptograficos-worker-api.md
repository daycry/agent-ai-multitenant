---
title: "ADR 0136: Dominios criptográficos worker ↔ api-server — secreto HMAC dedicado para los tokens internos"
status: accepted
date: 2026-07-29
deciders: [operador]
relates_to: [0012, 0024, 0054]
plan_referenced: prod-09-sesiones-autorizacion-frontend
task: task_prod09_03
---

# ADR 0136: Dominios criptográficos worker ↔ api-server

> **Estado: `accepted`** (2026-07-31, decidido por el operador).
>
> **Decisión tomada: Ratificada la Opción 1 — secreto HMAC dedicado para los tokens internos.**
>
> No había nada que elegir: la decisión ya estaba implementada y este ADR la
> ratifica. Sus dos condiciones de cierre están **cerradas el 2026-07-31** (ver
> §Condiciones): el instalador genera y propaga `API_SERVER_INTERNAL_TOKEN_SECRET`
> a los dos servicios, y el mapeo del entorno vive en el enum. La asimétrica queda
> como mejora futura, no como trabajo pendiente.

## Contexto verificado (2026-07-29)

### El problema que la decisión resuelve

Hasta `task_prod09_03`, un **solo** secreto firmaba dos cosas sin relación:

- las **sesiones de usuario**, con los claims `sys` (System Admin) y `own`
  (System Owner) — `encode_jwt`, con `settings.jwt_secret`
  ([jwt.py:71-75](../../apps/api-server/src/api_server/auth/jwt.py#L71-L75));
- los **tokens del sandbox** (`AGENTIC_INTERNAL_TOKEN`, con el que el
  agent-runtime llama a `/internal/agent/*`) — `mint_agent_token`, con el
  **mismo** secreto.

Y quien mintea el token del sandbox **no es el api-server: es el worker**, que
importa la función del paquete del api-server
([execution.py:31](../../apps/workers/src/workers/execution.py#L31)) y la llama al
lanzar el contenedor
([execution.py:182-188](../../apps/workers/src/workers/execution.py#L182-L188)).
De ahí la exigencia que el compose llevaba escrita en mayúsculas: el servicio
`workers` **MUST receive `API_SERVER_JWT_SECRET`**
([docker-compose.yml:54-66](../../docker/docker-compose.yml#L54-L66)).

Consecuencia: **comprometer el worker era poder forjar la sesión de cualquier
usuario, incluido un System Admin.** El worker es el servicio que habla con
Docker (vía proxy), monta el data-root completo y ejecuta el código de
orquestación más grande del stack. El peor sitio del despliegue para guardar la
llave que firma las sesiones humanas.

### Lo que ya está implementado

`internal_token_secret` existe
([config.py:93-99](../../apps/api-server/src/api_server/config.py#L93-L99)) y
`mint_agent_token` / `decode_agent_token` firman y verifican **con él**, nunca con
`jwt_secret` ([internal_agent.py:123-124 y 155-156](../../apps/api-server/src/api_server/auth/internal_agent.py#L118-L160)).
Además:

- la guarda del entorno rechaza su default de dev fuera de `dev`
  ([config.py:625-657](../../apps/api-server/src/api_server/config.py#L625-L657));
- hay un **suelo de longitud** para los dos secretos que firman bearers
  ([config.py:659-675](../../apps/api-server/src/api_server/config.py#L659-L675));
- y —el detalle que más me gusta de cómo se implementó— hay una guarda de que
  **los dos secretos DIFIEREN**
  ([config.py:679](../../apps/api-server/src/api_server/config.py#L679)): poner el
  mismo valor en las dos variables reproduciría el agujero sin que nada avisara,
  y ahora no arranca.

La ratificación, entonces, es de la **Opción 1** de más abajo. Nada que decidir
sobre eso.

### La decisión de nombrado que se tomó, y su precio

El diseño elegido mantiene **un solo nombre con el prefijo del api-server**: el
worker debe recibir `API_SERVER_INTERNAL_TOKEN_SECRET`, porque mintea a través de
`api_server.config`
([config.py:87-92, comentario «DEPLOYMENT»](../../apps/api-server/src/api_server/config.py#L87-L92)).
Es la opción con menos código. Su precio es que **el contrato sigue sin ser
comprobable desde el lado del worker**:

`tests/unit/test_compose_env_contract.py` afirma que cada servicio emite claves
**con su propio prefijo** y que cada clave prefijada corresponde a un campo real
de sus `Settings`, y tiene un test de «claves críticas de prod» **solo para el
api-server** (`test_api_server_emits_prod_critical_keys_prefixed`). No hay
equivalente para el worker y no lo puede haber de forma natural mientras el
secreto que el worker necesita se llame `API_SERVER_*`: el propio docstring del
test explica que una clave con el prefijo equivocado «the app silently ignores
it». Aquí no la ignora —el worker corre **dos** clases de `Settings` con prefijos
distintos— pero esa excepción no cabe en el contrato, así que la guarda no cubre
el caso.

La alternativa (`WORKERS_INTERNAL_TOKEN_SECRET` + `mint_agent_token` recibiendo el
secreto por parámetro) haría el contrato comprobable a cambio de ~2 h de
refactor sobre **un solo** sitio de minteo. **No es motivo para rehacer lo
implementado**; queda anotada como la forma de cerrar el hueco de verificación si
el hueco vuelve a morder.

### Prueba de que el hueco de verificación es real: dos bloqueos de arranque ~~vivos~~ YA CERRADOS

> **Los dos se cerraron el 2026-07-31** (ver §Condiciones de cierre). Esta sección
> se conserva **tal cual se escribió**, en presente y con la reproducción delante,
> porque es la evidencia de por qué el hueco de verificación del contrato importaba:
> dos guardas fail-closed correctas dejaron el stack generado sin arrancar, y solo se
> supo al reproducirlo a mano. Leer «no arranca» aquí es leer el diagnóstico, no el
> estado de hoy.

Ninguno de los dos era teórico. Los reproduje.

**(1) El instalador no genera el secreto nuevo.** Cero ocurrencias de
`INTERNAL_TOKEN_SECRET` en `docker/`, en `apps/installer/` y en `scripts/`. El
`.env` generado no lo escribe y el compose no lo referencia, ni para el
api-server ni para el worker. Reproducido:

```
$ python -c "from api_server.config import Settings; Settings(environment='prod')"
ValueError: environment='prod' but these settings still use dev defaults:
  … API_SERVER_INTERNAL_TOKEN_SECRET …
```

Es decir: **con el secreto nuevo en su sitio y el instalador sin tocar, el
api-server no arranca en prod.** El guard funciona exactamente como debe —falla
cerrado— y eso convierte una omisión del instalador en un stack que no levanta.

**(2) El enum cerrado del entorno no habla el idioma del instalador.**
`task_prod09_02` cerró `environment` a `{dev, staging, prod}` con un validador que
**rechaza** cualquier otro valor y que nombra `production` como ejemplo de fallo
([config.py:582-606](../../apps/api-server/src/api_server/config.py#L582-L606)).
El enum del instalador vale `development` / `staging` / `production`
([installer/config.py:115-121](../../apps/installer/backend/src/installer_backend/config.py#L115-L121))
y el compose emite ese valor tal cual
([compose_generator.py:558](../../apps/installer/backend/src/installer_backend/compose_generator.py#L558)).
Reproducido:

| `API_SERVER_ENVIRONMENT` | Resultado                                         |
| ------------------------ | ------------------------------------------------- |
| `dev`                    | arranca                                           |
| `prod`                   | falla por secretos dev (correcto: falta el nuevo) |
| `production`             | **falla: «is not a known environment»**           |
| `development`            | **falla: «is not a known environment»**           |

O sea: **el stack que genera el instalador no arranca hoy en ninguno de sus dos
perfiles**, y no por el secreto sino por el nombre del entorno. Es el mismo
patrón que este ADR describe, pero en espejo: la guarda que existía para evitar
un fallo silencioso ha producido un fallo ruidoso en el único sitio que no se
prueba de punta a punta.

Nada de esto invalida las dos tareas: al contrario, **son fail-closed haciendo su
trabajo**. Lo que hace falta es la pata del instalador, que el plan `prod-09`
remite a **prod-01** (secrets-2). Este ADR lo deja escrito con la reproducción
delante para que no se descubra en el siguiente despliegue.

### Un resto de la etapa anterior, para que nadie lo copie

`docker-compose.manuals.yml:242` pone `WORKERS_JWT_SECRET:
dev-only-jwt-secret-change-me`. `WorkersSettings` **no tiene** campo `jwt_secret`
y su `model_config` lleva `extra="ignore"`
([workers/config.py:948-954](../../apps/workers/src/workers/config.py#L948-L954)):
la clave se descarta sin un aviso. Alguien la escribió creyendo configurar el
secreto de firma del worker. Da la impresión exacta de que el contrato está
cubierto — y no lo estaba entonces ni lo está ahora.

## Opciones

### Opción 1 — Secreto HMAC dedicado (la implementada)

Un secreto propio para el canal worker→api, distinto del que firma sesiones
humanas, con guarda de que difieren y suelo de longitud. Comprometer el worker
deja de poder forjar sesiones de usuario.

**Coste**: el que estimaba el plan (8 h). Cero migración: los tokens internos son
efímeros por contenedor, así que rotar solo pide un reinicio coordinado.

**Riesgo**: despliegue coordinado api-server + workers (riesgo 4 del plan). Un
worker viejo mintea con el secreto viejo y el api nuevo lo rechaza.

### Opción 2 — Firma asimétrica (Ed25519 / RS256)

El worker guarda la clave **privada** y firma; el api-server solo tiene la
**pública**. Comprometer el api-server ya no permite firmar tokens de sandbox.

**Coste**: gestión de claves (generación en el instalador, custodia en Vault,
rotación, publicación de la pública) y acoplamiento con `task_prod09_17`, que
migra la pila JOSE a `joserfc`. Estimación honesta: 3-4× la Opción 1.

**Lo que gana sobre la 1**: protege la dirección api→worker, que **no es la que
preocupa**. El activo a proteger son las sesiones humanas y quien está expuesto es
el worker. La asimetría resuelve un problema que no tenemos.

**Cuándo volvería a la mesa**: si el radio de explosión del worker crece (p. ej.
si empieza a mintear credenciales de más servicios), o si aparece un tercer
consumidor que deba verificar sin poder firmar.

### Opción 3 — Que el worker no firme nada

El token lo mintea el lado api/orchestrator en el dispatch y viaja dentro de la
`ExecutionRequest` por el broker; el worker solo lo reenvía al contenedor. Es la
única que **le quita al worker la capacidad de firmar** en vez de reducir lo que
puede firmar.

**Coste**: medio; hay un solo sitio de minteo. Pero el token pasa a vivir en la
cola de Redis —justo el tipo de sitio del que este plan está sacando credenciales
(el `?token=` de los WebSockets)— y hay que decidir qué pasa en reintentos y
re-lanzamientos, donde hoy se re-mintea con reloj nuevo.

**Por qué no ahora**: cambia el ciclo de vida del token, que hoy es «uno por
contenedor». Queda como el **estado final deseable** si algún día se quiere que el
worker sea un ejecutor sin llaves.

## Decisión propuesta

**Ratificar la Opción 1, ya implementada.** La asimétrica (2) queda como mejora
futura condicionada a que crezca el radio de explosión del worker; la retirada de
la capacidad de firmar (3) como estado final deseable. Ninguna de las dos es
trabajo de este plan.

Y **tres condiciones de cierre**, porque sin ellas la decisión está a medias y el
despliegue no levanta:

1. **El instalador debe generar y propagar `API_SERVER_INTERNAL_TOKEN_SECRET`**
   ✅ **CERRADA el 2026-07-31.** `GeneratedSecrets` lo mintea con un draw
   independiente (la guarda de `config.py` rechaza el arranque si coincide con
   `jwt_secret`), `build_env_vars` lo escribe en el `.env`, y el compose lo emite
   para el api-server **y para el worker**.

   Al implementarlo apareció una segunda avería **más grave que la buscada**: el
   worker del compose generado no recibía **ningún** `API_SERVER_*`. Así que, al
   mintear el token del sandbox, sus `Settings` de api-server (1) se creían en
   `dev` —los guards anti-defaults no disparaban ahí: el fail-open que sobrevivió
   a `task_prod09_02`— y (2) firmaban con el **secreto por defecto**, que el
   api-server, con el real, rechazaba. El sandbox no habría podido llamar a
   `/internal/agent/*`, y en silencio. Se emiten ahora las dos variables.

   Guardas: `test_internal_token_secret_reaches_api_server_and_workers`,
   `test_internal_token_secret_differs_from_the_jwt_secret` y
   `test_workers_get_the_api_server_environment_so_its_guards_fire` (parametrizado
   por los tres perfiles). Prueba de extremo a extremo: `Settings()` construido con
   el `.env` generado arranca con `environment='prod'`; antes levantaba
   `ValueError: … still use dev defaults: API_SERVER_INTERNAL_TOKEN_SECRET`.

2. **El valor de `<PREFIX>ENVIRONMENT` que emite el instalador tiene que estar en
   `{dev, staging, prod}`.** ✅ **CERRADA el 2026-07-30.** Era real, y su causa era
   que el mapeo existía en **uno** de los dos generadores:
   - `config_generators.py` (el `.env`) **sí** traducía, vía `_RUNTIME_ENVIRONMENT`.
   - `compose_generator.py::_app_environment` emitía `cfg.system.environment.value`
     **en crudo**, y el valor del enum del wizard es `"production"`, no `"prod"`.

   Mientras el guard de `environment` era fail-open, un valor desconocido se
   trataba como dev y esto no se notaba: es el mismo agujero que `task_prod09_02`
   cerró. Al volverlo fail-closed, el api-server generado dejó de arrancar con
   `API_SERVER_ENVIRONMENT='production' is not a known environment`.

   Arreglo: el mapeo se mueve al propio enum (`Environment.runtime_value` en
   `installer_backend/config.py`) para que no pueda volver a estar en un generador
   y faltar en el otro — `config_generators` no puede importar de
   `compose_generator` sin ciclo, así que el enum es el único sitio común. Guarda:
   `tests/unit/test_compose_env_contract.py::test_compose_emits_an_environment_value_the_runtime_accepts`,
   parametrizado por los **tres** perfiles (con solo `production` pasaría el día
   que alguien lo "arreglase" con un `if prod`).

   > **Nota de honestidad sobre este ADR.** El 2026-07-30 taché esta condición
   > declarándola falsa, tras verificar `config_generators.py` y **extrapolar** al
   > compose sin abrirlo. Era cierta. El error es el modo de fallo nº1 de
   > [verificar-antes-de-implementar](../03-guides/verificar-antes-de-implementar.md)
   > —comprobar un camino y dar por hecho el otro— y queda escrito aquí porque
   > este ADR se leyó ya una vez con el tachón puesto.

3. **Un test de contrato del lado del worker.** Sea
   `test_workers_emits_prod_critical_keys_prefixed` (con la excepción documentada
   del prefijo cruzado), sea el refactor a `WORKERS_INTERNAL_TOKEN_SECRET`. Sin
   él, la propagación del secreto al worker vuelve a depender de que alguien se
   acuerde — y llevamos dos rondas comprobando que nadie se acuerda.

## Consecuencias

- **`task_prod09_03` está hecha** y se puede marcar cuando su suite esté verde;
  lo que no está hecho es su pata de despliegue, que vive en prod-01.
- **`docker/docker-compose.yml:54-66`** ya no dice la verdad: exige
  `API_SERVER_JWT_SECRET` en el worker y eso pasa a estar de más (y a ser
  contraproducente). Hay que reescribirlo, y `workers/config.py:90-101` con él.
  Es documentación, pero documentación que alguien va a seguir al pie de la letra.
- **`docker-compose.manuals.yml:242`** (`WORKERS_JWT_SECRET`, hoy ignorado) debe
  pasar a `API_SERVER_INTERNAL_TOKEN_SECRET`, que sí se lee. Hoy es decoración.
- **`task_prod09_17`** (migración a `joserfc`) declaraba depender de esta tarea
  porque tocan los mismos módulos. Correcto, y conviene mantener el orden: con el
  secreto ya separado, la migración de librería tiene una superficie más pequeña y
  dos suites que la vigilan por separado.
- **prod-10** debe meter `API_SERVER_INTERNAL_TOKEN_SECRET` en su inventario de
  secretos y en la rotación de Vault. El plan `prod-09` ya lo anticipa en su
  «Próximo Plan», con el nombre viejo (`internal_token_secret`).

## Verificación

1. Un token minteado con `internal_token_secret` **no** valida como sesión de
   usuario, y un JWT de usuario **no** valida como token de agente. Las dos
   direcciones: antes ambas pasaban la firma y solo las separaba el claim `kind`.
2. Con `environment != dev` y `internal_token_secret` en su default (o ausente, o
   más corto que el suelo, o **igual** a `jwt_secret`), **el proceso no arranca**.
   Las cuatro variantes, porque las cuatro reintroducen el agujero.
3. El compose de prod **referencia** `API_SERVER_INTERNAL_TOKEN_SECRET` y el
   `.env` lo escribe, tanto para el api-server como para el worker (extensión del
   test de contrato existente). **Hoy este test estaría rojo.**
4. `API_SERVER_ENVIRONMENT` emitido por el instalador ∈ `{dev, staging, prod}`
   para **todos** los valores del enum del wizard. **Hoy este test estaría rojo**,
   y es el que convierte el bloqueo de arranque en un fallo de CI.
5. `grep -r "API_SERVER_JWT_SECRET" docker/ apps/workers/` no devuelve nada en el
   contexto del worker — con aserción de que la guarda **encontró** los sitios que
   debía revisar, para que no pase vacía el día que alguien renombre los ficheros
   ([verificar-antes-de-implementar §4](../03-guides/verificar-antes-de-implementar.md)).
