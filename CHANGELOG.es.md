[English](CHANGELOG.md) · **Español**

# Registro de cambios

Este fichero documenta todos los cambios relevantes del proyecto.

El formato sigue [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), y
el proyecto pretende seguir [Versionado Semántico 2.0.0](https://semver.org/lang/es/).

> **Todavía no se ha publicado nada.** No hay etiquetas de git, ni releases de
> GitHub, ni imágenes en el registro de contenedores, ni paquetes de SDK
> publicados; todos los `pyproject.toml` de un servicio despegable siguen
> declarando `version = "0.0.0"`. Así que abajo hay una sola sección y es
> `[Unreleased]`. En [Versionado y releases](#versionado-y-releases) está lo que
> tiene que pasar antes de que eso cambie.

## [Unreleased]

Integrado en `master` como `e8e945da` el 2026-08-21 (pull request #67): **91
commits** escritos entre el 2026-07-30 y el 2026-08-21 — 39 arreglos, 28 features,
10 de documentación, 8 de mantenimiento, 2 refactores, 2 de tests, 1 de
rendimiento y 1 merge.

El trabajo posterior a ese merge, en
`work/validacion-cortex-seguridad-2026-07-30`, también se lista aquí y llegará a
`master` en el siguiente pull request.

### Por qué importa esta entrada

Cuatro cosas de esta tanda hay que leerlas antes de las listas, porque cambian
cuánto valen el resto de los números de este repositorio.

- **La integración continua vuelve a dar veredicto.** La CI llevaba en rojo en
  `master` desde el 2026-06-26, y una tubería permanentemente roja deja de ser una
  señal: nadie distingue el fallo nuevo del viejo, así que el pull request #66 se
  mergeó en rojo y nadie pudo decir si estaba bien. Se arreglaron seis causas
  distintas leyendo cada una el log de SU job — entre ellas, que la CI nunca creaba
  `docker/.env` (con lo que el proyecto compose entero abortaba antes de producir un
  solo log), que el gate de tipos no estaba corriendo en absoluto y se veía rojo por
  su envoltorio, y que `pip-audit --strict` moría en la primera omisión de un
  editable y tapaba 8 paquetes vulnerables. Otros dos jobs no eran ni verdes ni
  rojos: se les agotaba el reloj y GitHub los marcaba `cancelled`. Ni uno se arregló
  relajando nada — sin `continue-on-error`, sin ignores nuevos, sin umbrales bajados
  y sin pines quitados.

- **La deriva de esquema pasa de 162 items a cero.** `alembic check` no podía dar
  veredicto siquiera: `migrations/env.py` importaba un solo módulo de la capa de
  datos, así que `Base.metadata` se quedaba en 34 de 84 tablas, y la tabla
  referenciada que faltaba hacía morir el comando con un traceback de SQLAlchemy que
  se lee como un problema local de quien lo ejecuta. Con la metadata completa llegó
  el veredicto — y era que una migración autogenerada habría propuesto borrar
  `ix_chunks_embedding_hnsw`, el índice vectorial del RAG. Quien añadiese una
  columna se llevaba de regalo la degradación silenciosa del RAG a búsqueda
  secuencial.

- **Tres defectos reales de producto aparecieron al destapar tests que nadie
  corría.** Los tres vivían detrás de jobs que no reportaban: un diálogo de borrado
  que se rearmaba habilitado tras pulsar Cancelar (así que un borrado con
  confirmación por nombre sobrevivía a una cancelación y el siguiente era un clic,
  en cuatro pantallas), un 401 de un servidor MCP de terceros que cerraba la sesión
  del operador en el panel, y un formulario de canal de notificación que borraba lo
  que el operador acababa de escribir cuando llegaba la respuesta de los
  transportes. Ninguno era un test flaky.

- **Una invalidación de caché que no se había ejecutado nunca, en ninguna ruta, ya
  corre.** `set_platform_setting` documenta dos invalidaciones y la segunda la
  drenaba sólo la factoría de sesión de tenant, por la que no pasa ninguna ruta de
  System Admin. En la dirección ON→OFF el córtex podía conservar sus herramientas
  web hasta 30 segundos después de que el owner cortase el gate: un kill-switch de
  egress con retardo. Arreglarlo en la sesión y no en los diez llamantes destapó una
  décima ruta que nadie había mirado — aprobar o rechazar un plan desde el enlace de
  revisión no publicaba `plan_status_changed`, así que el Kanban gerencial se
  quedaba quieto justo en esa transición.

### Añadido

- **Gobernanza.** La cadena de precedencia que decide qué documento manda cuando
  dos se contradicen (`.docx` > `CLAUDE.md` > decisión escrita del operador > ADR
  aceptado posterior > plan > código > intuición), y un campo mecanizable
  `rejects:` en el frontmatter de los ADR para que «¿rechaza algún ADR esta casilla
  del roadmap?» deje de ser una pregunta que sólo contesta la prosa.
- **Historia del prompt y una puerta de evaluación al editarlo.**
  `PUT /agents/{id}` sobrescribía el `system_prompt` sin dejar rastro; ahora hay una
  tabla append-only con RLS FORCE (migración `0143`) y
  `GET /agents/{id}/prompt-versions` con el diff calculado al servir. Editar un
  prompt corre el golden set del agente, y en los presets `production` y
  `customer-external` un resultado peor que el umbral rechaza la escritura con 409,
  nombrando qué escenarios empeoraron.
- **El sujeto de la evaluación por fin ve el prompt.** `LLMSubjectModel.produce`
  mandaba un único mensaje `user` sin `system`, así que dos corridas del mismo
  dataset con prompts distintos salían estadísticamente iguales y «¿esta edición
  empeora la calidad?» era incontestable por construcción — con las tablas llenas y
  el dashboard pintando números.
- **Marketplace v2, fases 0 y 1**: el despliegue como entidad de primera clase
  (migración `0128`), validación del `config_schema`, avisos de actualización en el
  catálogo, y un test de cadena que va de publicar hasta que el agente TIENE la tool
  y el proyecto TIENE el servidor MCP.
- **Cinco tablas particionadas**, rotación de anillos de claves y un Vault operable.
- **Endurecimiento de la sesión**: sesión en cookie httpOnly + Secure + SameSite con
  doble-submit CSRF, validación de `Origin` del WebSocket en la misma entrega, y
  registro por invitación.
- **Fases F2–F5 del córtex**: estado afectivo, una identidad que el córtex puede
  proponerse a sí mismo, y el endpoint de onboarding con por dónde usarse.
- **Internacionalización del panel**: el hub de proyectos, la pantalla de login y
  once módulos más; los dos registries sirven ES y EN.
- **Un arnés guionizado para los 12 specs e2e que CI no corre** porque hablan con un
  api-server de verdad, con las cinco trampas de ese montaje escritas.
- **Configuración de Dependabot**, y el pineado por digest extendido a una tercera
  superficie que ningún documento nombraba: el catálogo de servicios, que tenía
  dentro el único `:latest` del sistema, en imágenes que corren al lado del código
  del agente.
- **Guardas nuevas**, todas verificadas en rojo antes de darlas por buenas: el
  reparto en shards se reproduce contra el árbol real, para que una partición que se
  deje ficheros fuera no pueda pasar verde; una casilla marcada del roadmap ya no
  puede declarar un fichero de test que no existe; la puerta cross-tenant no puede
  colgar de un shard; y la comparación de autogenerate afirma sobre su propio
  aparato, no sobre su resultado.

### Cambiado

- **El job de integración se parte en cuatro shards más un job de puerta aparte.**
  La suite pide unos 72 minutos y el job tenía un reloj de 45, así que no terminaba
  nunca. La protección de rama tiene que exigir ahora **cinco** checks:
  `Integration tests (shard N/4)` para N de 0 a 3, y la puerta.
- **`migrations/env.py` recorre el paquete de la capa de datos** en vez de listar
  imports: 34 → 84 tablas en `Base.metadata`, 53 módulos, con `onerror` explícito
  porque `walk_packages` se come los `ImportError` en silencio.
- **Los modelos declaran ya los índices que crearon las migraciones**, en 23 tablas.
  Los índices HNSW se verificaron de la única forma que vale —tirándolos en una BD
  desechable y reconstruyéndolos desde el DDL que compila el modelo— porque la
  comparación de índices de Alembic no mira `using`, `ops` ni `with`.
- **Cuatro ficheros de más de mil líneas pasan a paquetes**, sin cambiar una firma.
- **Los adaptadores de destino del backup los encola la api-server** en vez de
  ejecutarlos dentro.
- **LangGraph 0.6.11 → 1.2.11** y la familia LangChain a 1.x. `websockets` retrocede
  a conciencia de 17.0.1 a 15.0.1 porque `langgraph-sdk` 0.4.2 exige `<16`; el
  retroceso queda anotado junto al pin para que nadie lo suba sin saberlo.
- **Un formateador, no dos.** Con `black` 26.3.1 (obligado por dos avisos de
  seguridad) y `ruff` 0.16.3 los dos estilos dejaron de coincidir: black
  reformateaba 17 ficheros, ruff los devolvía, y ningún `git commit` podía terminar.
  Se retira el hook de `black`.
- **El hook de prettier fija una versión de verdad.** Apuntaba a un mirror archivado
  en `v4.0.0-alpha.8`, un paquete que no trae formateador y deja que el CLI se
  instale el 3.x del día — medido en 3.8.3 en local frente a 3.9.6 en el runner, con
  el mismo `rev` en los dos lados.
- **markdownlint corre también en local**, con el mismo pin exacto que CI, así que
  un estilo de viñeta ya no cuesta un run completo de CI para enterarse.
- **La puerta de análisis de composición de software bloquea en vez de informar.** Su
  backlog está vacío, y con el backlog vacío el modo informe no protegía de nada:
  sólo garantizaba que la próxima vulnerabilidad entrase en `master` detrás de un
  check verde-tachado que nadie lee.

### Arreglado

- **Las tres imágenes de infraestructura se construían con un nombre y corrían con
  otro, así que no las escaneaba nadie.** `docker/egress-proxy`,
  `docker/registry-proxy` y `docker/whatsapp-neonize` tenían tres constructores que
  producían tres nombres distintos: el compose canónico declaraba `build:` sin
  `image:`, lo que hace que compose bautice la imagen con el nombre del _proyecto_
  (`agentic-platform-egress-proxy`); `ci.yml` la etiquetaba `agentic-egress-proxy:v1`;
  y el compose que genera el instalador repetía la forma del canónico con el proyecto
  que eligiera cada instalación. En la máquina del operador convivían dos. El daño no
  era el desorden: CI construía su copia y la tiraba —esas imágenes no están ni en
  `release-images.yml` (no se publican) ni en la matriz de templates (no son
  templates)—, así que el `egress-proxy`, la ÚNICA salida a internet del contenedor
  donde corre código no confiable (ADR 0019, Principio Rector 2), no había pasado
  nunca por Trivy. Y el comentario que reparte la cobertura de Trivy entre los tres
  workflows afirmaba igualmente, cinco líneas por debajo del bucle que las construye,
  que no quedaba ninguna imagen del repo sin escanear. Ahora los tres actores
  construyen `agentic-platform/<nombre>:v1`, `ci.yml` escanea cada una, y la
  afirmación la sostiene una guarda que deriva la lista del árbol, no esa frase.

- **Las catorce imágenes de sandbox nunca se habían publicado, y el fallo era
  invisible.** `build-runtime-templates.yml` empujaba a `ghcr.io/agentic-platform`
  autenticándose con el `GITHUB_TOKEN` de Actions, que sólo puede publicar en el
  namespace del dueño del repositorio: el destino era inalcanzable por
  construcción. La publicación ocurre **sólo en `master`** y en rama se construye y
  escanea sin empujar, así que el gate de siempre salió verde tres semanas; la
  primera vez que corrió en `master` murieron los catorce builds con `denied:
permission_denied: The requested installation does not exist`. Lo caro no fue el
  rojo: `runtime_images.json` seguía con `digests: {}` —su fallback documentado—, o
  sea que **cada host seguía construyendo su propia variante** de las catorce
  imágenes donde vive el aislamiento por contenedor del Principio Rector 2, que es
  exactamente el estado que el ADR 0148 se firmó para terminar. El namespace se
  deriva ahora de `github.repository_owner`, y una guarda rechaza cualquier
  namespace de GHCR escrito a mano en un workflow que se autentique con el
  `GITHUB_TOKEN`; conservar el viejo habría exigido guardar un PAT clásico de larga
  vida como secreto del repo, peor cadena de suministro que el problema que
  arregla. El riesgo estaba anotado en `prod-01` y su mitigación —«decidir el
  registro en la primera semana»— nunca se ejecutó.

- **La segunda invalidación de caché de `set_platform_setting` no corrió nunca, en
  ninguna ruta.** Arreglado en la sesión y no en los llamantes, lo que además hizo
  que el veredicto del enlace de revisión publique `plan_status_changed` por primera
  vez.
- **Los diálogos de borrado se rearmaban habilitados tras Cancelar** en las cuatro
  pantallas que usan confirmación por nombre (proyectos, equipos, agentes y bases de
  conocimiento).
- **Un 401 de un servidor MCP cerraba la sesión del operador.** `apiFetch` trata
  todo 401 como sesión caducada; los dos endpoints de MCP contestan 401 cuando falla
  la credencial del _tercero_, que no dice nada de quien llama.
- **El diálogo de canal de notificación borraba lo escrito** cuando llegaba la
  respuesta de los transportes, y dejaba «Crear» deshabilitado para siempre sin
  error.
- **El interruptor de emergencia del sync de precios sólo se accionaba con SQL a
  mano.** `price_sync_enabled` no estaba en el registro de settings de plataforma,
  así que su endpoint contestaba 404 mientras cuatro docstrings prometían que un
  System Admin lo cambia desde el panel.
- **Forkear un agente con el nombre ocupado daba 500**; ahora da 409 con un nombre
  sugerido.
- **Los dos healthchecks de tinyproxy nunca fueron válidos** y un `|| true` lo
  tapaba.
- **El preflight del restore paraba servicios que el compose desplegado no
  declara**, y elevaba en el paso 3 — antes de restaurar nada.
- **El veredicto de `alembic check` era inalcanzable** para todos los planes que lo
  declaran como criterio de cierre.
- **`uq_task_dependencies_pair` no existió nunca en ninguna base de datos.**
  PostgreSQL descarta en silencio un UNIQUE cuyas columnas son exactamente las de la
  primary key dentro del mismo `CREATE TABLE` —sin error y sin NOTICE—, así que
  autogenerate lo proponía para siempre y el síntoma se leía al revés, invitando a
  escribir justo la migración equivocada.
- **Tres migraciones crearon `created_at`/`updated_at` sin `nullable=False`**, el
  mismo descuido copiado tres veces frente a 163 columnas equivalentes NOT NULL;
  cerrado por la migración `0144`, con relleno previo y reversible.
- **76 órdenes de test del roadmap nombraban ficheros que no existen.** Una casilla
  marcada cuyo comando nombra un fichero que falta afirma una verificación que no
  pudo ocurrir. El inventario que las congeló está ahora vacío.
- **21 casillas del roadmap afirmaban cosas falsas**, y tres casillas del córtex
  decían estar bloqueadas por un humano cuando la mitad de cada una era ejecutable.
- **Un caché de buildx servía una capa de `apt-get upgrade` rancia.** `rust-cargo`
  llevaba semanas con el paso de parches y seguía entregando los paquetes viejos: el
  texto de la instrucción no cambia nunca y el digest de la base está pineado, así
  que la capa venía del caché. Una imagen que declara aplicar los parches del sistema
  y entrega el juego de paquetes del día en que se llenó el caché es peor que una sin
  ese paso.
- **La puerta de evaluaciones moría en el import**, y eso se lee como «la puerta está
  roja».
- **Un test que «sólo pasaba solo»** tenía razón: había encontrado un singleton atado
  al event loop.

### Seguridad

- **Una fuga cross-tenant, cerrada.** La tabla de respaldo que crea la migración
  `0133` no tenía `tenant_id` ni RLS, y `02-roles.sh` deja un `ALTER DEFAULT
PRIVILEGES` que concede a `app_user` DML completo sobre toda tabla que Alembic cree
  después — verificado midiendo `has_table_privilege`, no suponiéndolo. Cualquier
  sesión de tenant podía leer qué categorías gatean los proyectos de los demás
  clientes. La migración `0138` REVOCA en vez de poner RLS: la aplicación no tiene
  ningún motivo para leer esa tabla, y «no hay acceso» es más fuerte que «hay una
  política».
- **Cinco de las seis tablas del córtex no tenían defensa estructural** — entre ellas
  la que guarda el texto literal de las conversaciones del System Owner. La
  inferencia que falla: «el córtex no es un recurso de tenant ⇒ no lleva RLS». La
  migración `0140` pone ENABLE + FORCE con policy sobre `owner_user_id`.
- **La puerta de aislamiento cross-tenant deja de colgar del shard 0.** Cuando a ese
  shard se le agotaba el reloj, `tests/migrations` quedaba `skipped` y la puerta de
  aislamiento no informaba — un job que muere a medias no cuenta lo que no llegó a
  hacer: lo omite.
- **`pip-audit`: 45 vulnerabilidades en 12 paquetes → ninguna**, sin una sola entrada
  en `.pip-audit-ignore`. Seis eran de la familia LangGraph. `python-jose` era el
  único caso que habría justificado una excepción (su aviso de `ecdsa` no tiene
  versión que lo corrija) y resultó ser residuo del venv: el código ya había migrado
  a `joserfc`, así que se desinstala en vez de excusarse.
- **CVE-2026-53615** en nueve paquetes de la familia util-linux, en la imagen de la
  api-server y en cuatro templates de runtime.
- **Trivy y las dos superficies de `npm audit --audit-level=high` están a cero** sin
  supresiones: `.trivyignore` y `.pip-audit-ignore` no tienen entradas vigentes.

### Retirado

- `tareas.txt`, y los scripts de demo de fase de la raíz del repositorio.
- El hook de `black` en pre-commit (ver **Cambiado**).
- `getToken` / `setToken` del módulo de autenticación del panel — su ausencia es la
  feature, y un test lo afirma para que nadie los reponga «sólo para el endpoint de
  subida».

## Versionado y releases

Este proyecto no ha cortado nunca una versión. En concreto, medido el 2026-08-21:

| Artefacto                             | Estado                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------- |
| etiquetas de git                      | ninguna                                                                   |
| releases de GitHub                    | ninguna                                                                   |
| registro de contenedores (ghcr)       | vacío — `release-images.yml` no ha corrido nunca                          |
| `agentic-platform-sdk` (PyPI)         | sin publicar                                                              |
| `@agentic-platform/sdk` (npm)         | sin publicar (`private: true`)                                            |
| `version` de cada servicio despegable | `0.0.0` — los dos paquetes de SDK dicen `0.1.0`, y ninguno está publicado |

Hasta que se etiquete una primera versión, todo aterriza bajo `[Unreleased]`.
Cortarla exige, como mínimo: decidir qué números de versión llevan los componentes
(no hay ADR para eso todavía), correr `release-images.yml` por primera vez, y
decidir si los SDK se publican siquiera.

## Cómo añadir una entrada

En este repositorio viven dos changelogs y contestan preguntas distintas.

- **Este fichero** es la historia legible del repositorio: qué cambió, en secciones
  Keep a Changelog, lo más nuevo primero. Se añade a `[Unreleased]` en el mismo
  commit que el cambio, y la misma entrada se añade a
  [`CHANGELOG.md`](CHANGELOG.md) — la guarda exige que las dos mitades mantengan la
  misma estructura de secciones.
- **`docs/07-changelog/{plan_id}.md`** es una entrada por plan del roadmap, exigida
  por `CLAUDE.md` antes de que un plan pueda llegar a `completed`, y comprobada por
  `tests/unit/test_roadmap_frontmatter.py`. Ese formato no cambia; este fichero no lo
  sustituye.

La política de idiomas que gobierna las dos mitades de este fichero es
[`docs/03-guides/bilingual-docs.es.md`](docs/03-guides/bilingual-docs.es.md).

[unreleased]: https://github.com/daycry/agent-ai-multitenant/commits/master
