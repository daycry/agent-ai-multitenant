---
title: "ADR 0145: Vault operable — tokens periódicos renovables y estrategia de desellado"
status: accepted
date: 2026-07-31
deciders: [operador]
relates_to: [0021, 0061]
plan_referenced: prod-10-vault-secretos-operables
task: [task_prod10_07, task_prod10_09]
docs_language: es
---

# ADR 0145: Vault operable — tokens y desellado

> **Estado: `accepted` (firmado el 2026-08-01).** Las dos decisiones quedan
> ratificadas tal como estaban implementadas: **A** (tokens periódicos
> renovables) y **C** (desellado manual con healthcheck honesto y alerta).
> AppRole y el auto-unseal quedan registrados como evolución, con su disparador
> escrito. Detalle en § «Firma del operador» — que además explica por qué el
> desellado manual es la razón de la decisión del ADR 0146.

## Contexto

La auditoría de producción encontró que Vault está bien integrado y **mal
operado**. Dos averías distintas, las dos con la misma forma: el sistema parece
funcionar hasta que de golpe no funciona, y nada apunta a la causa.

### Avería 1 — el token caduca (hallazgo secrets-4)

`routers/llm_providers.py` y `routers/mcp.py` construían cada uno un
`hvac.Client` con el token estático de `API_SERVER_VAULT_TOKEN` y lo cacheaban en
un singleton de módulo. Buscado en todo el repositorio el 2026-07-31: **cero
llamadas a `renew_self` o `lookup_self`**. Un service token tiene TTL — 32 días
en la configuración que documenta el instalador. El día que caduque, TODAS las
credenciales de proveedor LLM y toda resolución de `auth_ref` de MCP dejan de
funcionar a la vez, un mes después del despliegue que lo causó, cuando ya nadie
relaciona una cosa con la otra.

Y una segunda mitad del mismo hallazgo: `vault_bootstrap.py` escribe cuatro
políticas ACL de mínimo privilegio y **nadie mintea un token contra ellas** (no
existe `create_token` en el repo). En la práctica todos los servicios llevaban el
root token.

### Avería 2 — Vault arranca sellado y parece sano (secrets-5, deploy-8)

Vault con backend de fichero arranca **sellado** tras cada reinicio del host:
vivo, contestando HTTP, sin poder descifrar un solo secreto. Tres capas lo daban
por bueno:

1. el healthcheck del compose pide
   `/v1/sys/health?...&sealedcode=200&uninitcode=200` — traduce «sellado» (503) y
   «sin inicializar» (501) a **200**;
2. el compose del instalador arranca las apps con `depends_on: vault:
service_healthy`, o sea detrás de ese 200;
3. el watchdog da por sano cualquier `healthy|running|starting`.

El mapeo (1) **no es un error**: si `sealed` fuese `unhealthy`, Docker
reiniciaría Vault en bucle antes de que nadie pudiera desellarlo. El error es que
no hubiera ninguna otra señal.

---

## Firma del operador (2026-08-01)

Las dos decisiones de abajo quedan **ratificadas tal como están implementadas**:
**A** (tokens periódicos renovables) y **C** (desellado manual con healthcheck
honesto y alerta). El ADR pasa a `accepted`.

Una consecuencia que conviene dejar escrita aquí, porque se decidió el mismo día
y las dos piezas se leen juntas: **el desellado manual es la razón por la que el
[ADR 0146](0146-fernet-en-db-vs-vault.md) NO migra los secretos de SSO a Vault.**
Con desellado manual, un Vault sellado tras cada reinicio del host dejaría el
login federado inaccesible hasta que apareciese un humano. Si algún día se
adopta auto-unseal (opción A o B de la decisión 2), esa objeción desaparece y el
0146 debería reabrirse.

## Decisión 1 — Autenticación de servicios: tokens periódicos, no AppRole

### Opciones

**A. Tokens periódicos renovables (recomendada, implementada).** Un token por
servicio, minteado contra su política, con `-period=72h` y `-orphan`. Nunca
caduca mientras se renueve dentro de su periodo. La renovación la hace el propio
servicio en segundo plano.

- **A favor**: cambio pequeño sobre el `hvac.Client` que ya existía; ningún
  componente nuevo; `-orphan` permite revocar el root token expuesto sin tumbar
  la plataforma; el minteo cabe en un script de 100 líneas.
- **En contra**: el token vive en el `.env` del host. Si alguien lo lee, lo tiene
  hasta que se revoque — no hay rotación automática de la credencial en sí, sólo
  de su caducidad.

**B. AppRole (`role_id` + `secret_id` por servicio).** El servicio se autentica
al arrancar y recibe un token de vida corta.

- **A favor**: la credencial de larga vida (`role_id`) no da acceso por sí sola;
  `secret_id` puede ser de un solo uso y con TTL; es el patrón que HashiCorp
  recomienda para servicios.
- **En contra**: mueve el problema de custodia al `secret_id` (que también hay
  que entregar de algún modo — el «secret zero» clásico), exige un flujo de login
  en cada servicio y un manejo de reintentos que hoy no existe en ninguno, y en
  un despliegue de una sola máquina con Docker Compose el beneficio marginal es
  pequeño: quien pueda leer el `.env` puede leer también el `secret_id`.

### Decisión

**Opción A**, con AppRole registrado como evolución para cuando haya un
requisito de rotación automática de credenciales o más de una máquina.

### Implementado

- `apps/api-server/src/api_server/vault_client.py`: fábrica única
  `build_vault_client()` + `VaultTokenManager`, que hace `lookup_self` al
  arrancar (TTL al log y a la métrica) y `renew_self` en un hilo daemon **antes
  de la mitad del TTL**. Renovar al 90% dejaría una ventana de minutos: si Vault
  está sellado justo entonces, el token caduca y la avería es la misma.
- El fallo de renovación se loguea a nivel **error** y el bucle **no muere**.
  Está escrito así a propósito: un `renew_self` que se rompe en silencio
  reproduce exactamente el problema que este manager evita (riesgo 6 del plan).
  La métrica `agentic_vault_token_ttl_seconds` + la alerta
  `VaultTokenExpiringSoon` (< 24 h) son la red.
- `scripts/vault-mint-service-tokens.sh`: mintea los cuatro tokens periódicos
  huérfanos contra las políticas de `vault_bootstrap.py`. Por defecto salen por
  **stdout**, no a disco.
- Los dos routers pasan por la fábrica; un test recorre el árbol y falla si
  aparece otro `hvac.Client(` fuera de ella — un segundo cliente sería un token
  que nadie renueva.

### Hilo y no `asyncio`

`hvac` va sobre `requests`, que es síncrono, y las dependencias de FastAPI que
construyen el cliente son funciones `def` (FastAPI las ejecuta en el threadpool,
no hay bucle de eventos donde engancharse). Y si lo hubiera, meter una llamada
bloqueante dentro es el hallazgo perf-7 otra vez: un Vault inalcanzable congelaba
el api-server entero.

---

## Decisión 2 — Desellado tras un reinicio

### Opciones

**A. Auto-unseal con transit seal** contra un segundo Vault mínimo.

- **A favor**: cero intervención humana en producción.
- **En contra**: un componente más que operar, y la custodia se muda a la clave
  del Vault de transit — en una sola máquina, ese segundo Vault arranca sellado
  igual, así que el problema se desplaza en vez de resolverse.

**B. Auto-unseal con KMS cloud** (AWS/Azure/GCP).

- **A favor**: el estándar de la industria; custodia delegada a un HSM gestionado.
- **En contra**: exige conectividad saliente permanente y una cuenta cloud. El
  alcance declarado del producto incluye despliegues on-prem aislados, con los
  que esto choca de frente.

**C. Desellado manual + healthcheck honesto + alerta (recomendada, implementada).**

- **A favor**: cero componentes nuevos; el reparto de Shamir sigue siendo real
  (5 custodias separadas); reversible.
- **En contra**: el RTO depende del humano de guardia.

### Decisión

**Opción C** como mínimo viable. A/B quedan evaluadas para cuando exista un
requisito de RTO < 15 min; ese día, la opción A deja de ser un desplazamiento del
problema porque habría más de una máquina.

### Implementado

- `api_server.vault_client.probe_vault_seal()` consulta `/v1/sys/seal-status` —
  no `/v1/sys/health`, que es justamente el que esconde el sellado.
- `/admin/system-health` marca la plataforma **`degraded`** (no `ok`) cuando
  Vault está sellado, con el detalle apuntando al runbook. `down` se reserva para
  postgres: sin Vault la plataforma sirve, degradada; sin postgres, no.
- Gauge `agentic_vault_sealed` + alerta `VaultSealed` (`for: 2m`, `critical`) en
  `docker/monitoring/prometheus/rules/app_alerts.yml`.
- `docs/06-runbooks/restart-services.md` abre con el desellado como **paso 0**.

### El healthcheck del compose NO cambia

Sigue mapeando `sealed → 200`, a propósito y ahora documentado en el runbook. Un
Vault en bucle de reinicio es peor que un Vault sellado: no da tiempo material a
desellarlo. La señal la dan el probe, la métrica y la alerta, que es donde debe
estar.

---

## Consecuencias

- El operador tiene que ejecutar `scripts/vault-mint-service-tokens.sh` una vez y
  dejar de usar el root token en configs. Hasta que lo haga, el manager detecta
  que el token no es renovable (los de root no lo son), lo dice en el log y **no
  entra en bucle** contra Vault.
- La alerta `VaultSealed` aparece en cada reinicio de Vault que tarde más de 2
  minutos en desellarse. En el stack de dev no salta: el compañero
  `vault-unsealer` lo abre solo.
- Si el humano elige A o B para el desellado, la Decisión 2 se reescribe y la
  parte implementada (probe + métrica + alerta) **se conserva**: seguir sabiendo
  si Vault está sellado es útil con auto-unseal o sin él.

## Alternativas descartadas sin desarrollar

- **Tokens de TTL fijo re-minteados por cron**: es la operación manual que
  produjo la bomba de 32 días, sólo que con un cron que también puede fallar en
  silencio.
- **Compartir un token entre servicios**: anula las cuatro políticas de mínimo
  privilegio que el instalador ya escribe, que es trabajo hecho y sin usar.
