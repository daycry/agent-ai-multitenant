---
title: "ADR 0137: La tabla `users` se queda global — el aislamiento del directorio se ancla por membership, no por RLS"
status: accepted
date: 2026-07-30
deciders: [claude-code]
relates_to: [0047, 0076, 0117]
plan_referenced: prod-14-tenancy-defensa-profundidad
task: task_prod14_09
---

# ADR 0137: La tabla `users` se queda global

## Contexto

`users` es la única tabla del esquema con datos de persona que **no** tiene
`tenant_id` ni RLS. La auditoría de producción (hallazgo `tenancy-5`, severidad
low) lo marcó como divergencia del Principio Rector nº 1 y el plan `prod-14`
pedía un ADR con tres opciones para que un humano eligiera:

1. dejarlo como está y añadir un test de no-fuga;
2. una vista `tenant_users` (users ⨝ user_org_memberships) como único camino de
   lectura en contexto tenant;
3. una política RLS sobre `users` con `EXISTS` contra `user_org_memberships`,
   más una excepción para el propio `app.user_id`.

El meta-test de cobertura RLS que este mismo plan introduce
(`tests/integration/test_rls_invariant.py`) obliga a que cada tabla sin
`tenant_id` lleve su justificación escrita. Esta es la de `users`.

## Qué dice el código hoy

Inventario de los `select(User…)` del api-server (2026-07-30):

| Sitio                                              | Anclaje                                                             |
| -------------------------------------------------- | ------------------------------------------------------------------- |
| `assistant/tools.py:279`                           | `JOIN user_org_memberships` + `tenant_id == ctx.tenant_id` + activa |
| `routers/human_agents.py:195` (`assignable-users`) | `JOIN user_org_memberships` + `tenant_id == tenant_id`              |
| `routers/admin.py:248,287`                         | `require_system_admin` (cross-tenant por diseño)                    |
| `auth/deps.py:215,258`                             | `User.id == user_id` — el propio usuario, solo lee flags de rol     |
| `routers/auth.py:76,194,504`                       | flujo **pre-tenant** (login, bootstrap, `/me`)                      |
| `routers/scim.py`, `routers/sso.py`                | aprovisionamiento **pre-tenant** por email                          |
| `routers/mfa.py:592`                               | `User.id == user_id` — el propio usuario                            |
| `seeds/init_tenant.py`                             | bootstrap de instalación                                            |

**No hay ninguna desviación**: los dos únicos lugares que resuelven personas en
contexto tenant lo hacen atravesando `user_org_memberships`, que **sí** tiene RLS
(`membership_tenant_isolation`, migración 0001). Es decir: el aislamiento del
directorio ya está delegado a una tabla protegida. Un olvido del filtro
`membership.tenant_id == ...` en la aplicación seguiría cerrado, porque la RLS de
la tabla de membresías lo cierra por debajo.

## Decisión

**Opción 1, endurecida: `users` se queda global y sin RLS.** Se descartan la
vista `tenant_users` (opción 2) y la política RLS (opción 3), y el refuerzo que
falta NO es de esquema sino una **guarda estática** sobre las queries.

## Por qué se rechazan las opciones 2 y 3

Porque las dos chocan con la misma imposibilidad, y no es de implementación sino
conceptual:

> **La tabla de identidades tiene que ser legible ANTES de que haya identidad.**
> Una RLS que dependa de quién eres no puede gobernar la consulta que averigua
> quién eres.

El login empieza con `SELECT … FROM users WHERE email = $1` (`routers/auth.py:76`).
En ese momento no hay `app.tenant_id` —el tenant se resuelve DESPUÉS, con las
membresías— y tampoco hay `app.user_id`, que es precisamente lo que la consulta
va a averiguar. La «excepción para el propio `app.user_id`» de la opción 3 no
salva el caso: en el paso donde hace falta, ese GUC aún no existe. Igual ocurre
con el aprovisionamiento SCIM y con el primer login SSO, que buscan por email sin
tenant.

Y la evidencia de que este no es un argumento teórico ya está en el repositorio:
`_load_active_memberships` **corre hoy sobre el engine BYPASSRLS**, y su propio
docstring explica que es porque «la sesión del llamante todavía no tiene tenant
activo». Poner RLS en `users` empujaría al engine BYPASSRLS todavía más superficie
del router de autenticación —el más expuesto de la aplicación— o exigiría una
política tan permisiva que no protegería nada. **La opción 3 empeora la postura de
seguridad real mientras mejora la métrica «tablas con RLS».** Ese es exactamente
el modo de fallo que este plan intenta evitar.

La opción 2 (vista) no comparte el problema de arranque, pero tampoco aporta el
invariante que se busca: una vista es **opt-in**. Mientras `app_user` conserve
`SELECT` sobre `users` —y lo necesita para el login—, cualquier query futura puede
seguir yendo a la tabla directamente. La vista sería azúcar sintáctico sobre el
JOIN que los dos llamantes ya escriben a mano, y su única forma de convertirse en
invariante sería `REVOKE SELECT ON users FROM app_user`… que rompe el login. La
única vía estructural real sería partir el rol de la api-server en dos (uno que
puede leer `users` para autenticar, otro para todo lo demás), y eso significa dos
engines y dos sessionmakers en el api-server: coste alto, y un ADR propio.

## Consecuencias

Aceptadas:

- `users` figura en `GLOBAL_TABLES_ALLOWLIST` del meta-test de invariante RLS con
  esta justificación y un puntero a este ADR. La excepción queda **visible**, que
  era la mitad del problema: hasta ahora era invisible.
- El aislamiento del directorio depende de dos cosas que sí son verificables: la
  RLS de `user_org_memberships` y que las lecturas en contexto tenant pasen por
  ella.

Pendiente (follow-up, no bloquea este ADR):

- **Guarda estática de queries sobre `User`**: un test que recorra el AST del
  api-server, encuentre los `select(User…)` y exija que cada uno esté (a) unido a
  `UserOrganizationMembership`, (b) filtrado por `User.id`, o (c) en una allowlist
  de módulos pre-tenant/admin (`auth`, `scim`, `sso`, `admin`, `seeds`, `mfa`).
  Con la aserción «vio al menos N queries» para que no envejezca en vacío. Es lo
  único que convierte «hoy no hay desviación» en «mañana tampoco». Sin esto, la
  decisión de este ADR se sostiene sobre un inventario con fecha.
- Test de no-fuga a nivel de endpoint (`test_users_directory_isolation.py`, tarea
  `task_prod14_08` del plan): ningún endpoint de contexto tenant devuelve usuarios
  sin membership activa en el tenant del llamante.

Si algún día se parte el rol de la api-server en dos (login vs. resto), la opción
2 vuelve a la mesa: con `REVOKE SELECT ON users` para el rol general, la vista
`tenant_users` sí sería un invariante. Ese es el disparador para reabrir esto.
