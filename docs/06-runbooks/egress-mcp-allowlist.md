---
title: Allowlist de hosts MCP remotos en el egress-proxy
docs_language: es
audience: system admin, operador
updated: 2026-09-03
---

# Runbook — Allowlist de hosts MCP remotos (egress-proxy)

Un servidor MCP **remoto** (Atlassian, GitHub, cualquier SaaS con endpoint
`streamable_http` o `sse`) sólo es alcanzable desde una ejecución si su host está
en la allowlist del `egress-proxy`. Este runbook es el procedimiento para
abrirlo, cerrarlo y —lo que más se olvida— **comprobar que el cambio está en
vigor**.

Lo gobierna el
[ADR 0165](../05-architecture-decisions/0165-allowlist-de-hosts-mcp-remotos-en-el-egress.md),
y su reparto de papeles es lo único que hay que tener en la cabeza antes de tocar
nada:

| Quién                                        | Qué es                                                                         |
| -------------------------------------------- | ------------------------------------------------------------------------------ |
| El ajuste `egress.mcp_allowed_hosts`         | La **intención**: lo que un System Admin ha autorizado. No aplica nada         |
| `filter.txt` (dentro de la imagen del proxy) | La **autoridad**: lo que el proxy hace, pero sólo tras reconstruir y recrear   |
| El propio proxy, preguntándole               | El **veredicto**. Es el único que puede responder «¿está permitido?» de verdad |

> **La regla de oro, y la causa de casi todos los incidentes de esta página:**
> guardar **no** es aplicar, y aplicar **no** es «permitido» hasta que el proxy lo
> dice. Entre el guardado y el rebuild hay una ventana en la que el panel y el
> egress dicen cosas distintas — en los **dos** sentidos (§1).

## Comprobación previa

- **Eres System Admin.** El `PUT /admin/platform-settings/{key}` va con
  `require_system_admin` y la capa de datos vuelve a rechazar a cualquier otro
  actor, Tenant Admin incluido. Si el endurecimiento de `/admin/*` te deja fuera,
  el camino de vuelta es [recuperacion-lockout-admin.md](./recuperacion-lockout-admin.md).
- **Estás EN EL HOST**, con el usuario que opera `docker compose` desde el
  directorio del despliegue. El panel **no puede** aplicar el cambio y no es una
  carencia que vaya a corregirse: darle al api-server acceso al daemon Docker
  contradice el
  [ADR 0060](../05-architecture-decisions/0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md).
- **Sabes en qué copia del filtro escribes.** En el repo hay **dos**; en un host
  instalado, **una**. Es el error que más tiempo cuesta descubrir, porque editar
  la copia equivocada no da ningún error: §7.

## 1. Revocar un host (lo urgente, y por eso va primero)

**Quitar el host del ajuste no cierra nada.** El filtro está horneado en la
imagen del proxy (`COPY filter.txt /etc/tinyproxy/filter`) y ningún compose lo
monta como bind, así que hasta que no se reescribe el fichero **y** se reconstruye
la imagen **y** se recrea el contenedor, ese host sigue siendo alcanzable desde
**todos** los sandboxes de **todos** los tenants. El ADR 0165 lo llama revocación
asimétrica y lo lista como riesgo alto: lo que corre prisa tarda lo mismo que lo
que puede esperar.

Lo bueno es cuánto es «lo mismo»: **unos 7 segundos** de reloj, medidos (§8). La
revocación no necesita atajos, necesita que alguien la ejecute.

1. **Quita el host del ajuste** (panel o API, §2). Esto deja constancia de quién
   lo retiró; no cambia el egress.
2. **Reescribe el filtro sin ese host** y **aplica**, que es lo que sí lo cambia
   (§3). En un host instalado:

   ```bash
   cd "$COMPOSE_DIR"
   curl -sS https://<tu-host>/admin/platform-settings \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     | jq '[.[] | select(.key=="egress.mcp_allowed_hosts") | .value][0]' > /tmp/hosts.json
   python3 scripts/egress/render_mcp_allowlist.py \
     --hosts-json /tmp/hosts.json \
     --filter "$COMPOSE_DIR/stack/egress-proxy/filter.txt"
   docker compose build egress-proxy && docker compose up -d --force-recreate egress-proxy
   ```

3. **Compruébalo contra el proxy** (§4). Un `connect=403` con su línea
   `Proxying refused on filtered domain` en el log es la única prueba de que el
   host está cerrado. El ajuste no lo prueba y el fichero tampoco.
4. **Revisa qué ejecuciones lo estaban usando.** Cerrar el egress no aborta un
   run en curso: la conexión ya establecida sigue viva hasta que termina. Si la
   revocación es por un incidente, para también las ejecuciones.

**El martillo, con su coste escrito.** Si el `build` falla (sin red para el
`apk`, registry caído) y hay que cortar igualmente, `docker compose stop
egress-proxy` cierra el host revocado **y todo lo demás**: los agent-runtimes
pierden la salida a los proveedores LLM y toda ejecución en marcha muere. Es una
decisión de incidente, no un paso de este procedimiento.

## 2. Abrir un host: quién lo pide, quién lo aprueba, dónde queda

**Quien lo pide es un tenant; quien lo abre es un System Admin.** No se delega:
el filtro no distingue quién pregunta, así que un host abierto lo está para los
sandboxes de **todos** los tenants (§6).

1. **El tenant declara su servidor MCP** en su proyecto. El guardado devuelve
   **200 con un aviso**, no un error: el servidor declarado es justamente el
   artefacto con el que pide la apertura, y bloquearlo lo dejaría sin poder pedir
   nada. Lo que sí se rechaza con 422 es una `url` mal formada (IP literal,
   `localhost`, sufijo `.internal`, `http://` contra un host externo).
2. **El System Admin juzga la petición.** No es un trámite: cada línea de esta
   lista es un canal de salida completamente escribible hacia un tercero (§6).
3. **La añade al ajuste** `egress.mcp_allowed_hosts`, en la categoría
   **Egress / Red** de **Sistema → Ajustes de plataforma**
   (`/admin/settings/platform-defaults`), o por API:

   ```bash
   curl -sS -X PUT https://<tu-host>/admin/platform-settings/egress.mcp_allowed_hosts \
     -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
     -d '{"value": ["mcp.atlassian.com", "api.githubcopilot.com"]}'
   ```

   El ajuste guarda **hostnames en claro y nada más**. Se rechaza con un 422 que
   dice el motivo: una IP literal, un host sin punto (eso es un servicio del
   compose y se exime por `NO_PROXY`, no por aquí), `169.254.169.254` y demás
   nombres de metadata, cualquier cosa no-ASCII (se pide la forma punycode
   explícita, porque IDNA **admite** el homógrafo en vez de rechazarlo), un
   comodín, y `host:puerto` — el puerto por entrada no existe, ver §3.

4. **Aplica el cambio** (§3) y **verifícalo** (§4).

**Dónde queda la auditoría, que son dos registros porque son dos preguntas:**

- _¿Quién autorizó abrir este host?_ → la fila de `audit_log`
  (`action = "egress.mcp_allowlist.changed"`, con `added` / `removed`). Hace falta
  porque el ajuste sólo guarda el **último** escritor: sobrescribe justo el
  historial que se quiere auditar.
- _¿Qué está corriendo de verdad?_ → el historial de git del `filter.txt`
  aplicado, con su PR. Es el registro que sobrevive a una base de datos
  restaurada de un backup viejo.

## 3. Aplicar: el comando real

El renderizador vive en `scripts/egress/render_mcp_allowlist.py`, es **stdlib
pura** (no importa `api_server`: en un host instalado ese paquete no existe) y
sólo reescribe lo que hay **entre los centinelas**:

```text
# >>> BEGIN generated: egress.mcp_allowed_hosts — NO EDITAR A MANO
^mcp\.atlassian\.com$
# <<< END generated: egress.mcp_allowed_hosts
```

Fuera de esos centinelas están las entradas escritas a mano —los proveedores LLM
del catálogo cerrado, los hosts del córtex, el comodín de APIM
`^[a-z0-9-]+\.azure-api\.net$`— y **ahí no entra el script**. Se editan en el
repo, con rama, PR y revisor: un comodín abre una familia entera de hosts y eso no
se juzga de un vistazo en un formulario.

Sus opciones, tal como las declara el propio script:

| Opción         | Para qué                                                                                                                                       |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `--host`       | Un host suelto, repetible. Cómodo para un cambio de una línea                                                                                  |
| `--hosts-json` | La lista entera desde un fichero JSON, o `-` para leerla de stdin. Acepta tanto un array como el cuerpo `{"value": [...]}` que devuelve la API |
| `--filter`     | Ruta del `filter.txt` a reescribir, repetible. **Obligatoria en un host instalado**; sin ella escribe las dos copias del repo                  |
| `--check`      | No escribe: dice qué cambiaría. Devuelve **1 si algo cambiaría** y 0 si el filtro ya decía eso, así que sirve como guarda en un script         |

Y después, **siempre**, el paso que convierte el fichero en política:

```bash
docker compose build egress-proxy && docker compose up -d --force-recreate egress-proxy
```

> El script lo imprime al terminar por la misma razón por la que está aquí: un
> fichero cambiado y un operador convencido de que ya está es exactamente la
> avería que este procedimiento evita.

**Sobre el puerto, que es donde falla la intuición.** El ajuste guarda sólo
hostname porque tinyproxy **no puede** acotar puertos por host: `ConnectPort` son
**dos directivas globales**, `443` y `8443` (`docker/egress-proxy/tinyproxy.conf`).
Un host que sólo escuche en 9443 no se puede habilitar desde aquí por mucho que su
nombre entre en el filtro — medido, §8 — y cambiarlo exige tocar `tinyproxy.conf`
en el repo, donde vale para todos los hosts a la vez.

## 4. Comprobar que está EN VIGOR: pregúntale al proxy

Ni el ajuste ni el fichero responden a esto. El ajuste dice lo que alguien quiso;
el fichero del repo dice lo que se escribió, que no es necesariamente lo que la
imagen en marcha lleva dentro. Hay tres niveles y sólo el tercero es una prueba.

**4.1 — Qué lleva el contenedor que está corriendo** (mejor que mirar el repo, y
aun así sólo es el fichero):

```bash
docker compose exec egress-proxy cat /etc/tinyproxy/filter
```

**4.2 — El veredicto: un CONNECT a través del proxy**, desde la misma red por la
que sale el sandbox:

```bash
docker run --rm --network agentic-agents curlimages/curl:8.10.1 \
  -sS -o /dev/null -w 'connect=%{http_connect}\n' --max-time 20 \
  -x http://egress-proxy:8888 https://mcp.atlassian.com/
docker compose logs egress-proxy --tail 20
```

La imagen de `curl` se baja por la red del **host**, no por la del sandbox, así
que el `docker run` funciona aunque el destino esté bloqueado. Cómo se lee el
resultado — la tabla sale de la medición de §8, no de la documentación de
tinyproxy:

| Resultado                                                          | Qué significa                                                                                      |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `connect=200`                                                      | **Permitido**: el proxy abrió el túnel. Lo que pase después ya no es asunto suyo                   |
| `connect=403` + log `Proxying refused on filtered domain "<host>"` | El host **no está** en la allowlist aplicada. O no se ha reconstruido la imagen (§3)               |
| `connect=403` + log `Unauthorized connection from "<ip>"`          | El proxy rechazó al **cliente**, no al destino. Es otra avería: §5                                 |
| `connect=403` **sin ninguna línea nueva en el log**                | El puerto del CONNECT no es 443 ni 8443. `ConnectPort` es global y no lo arregla ninguna allowlist |
| `connect=200` y luego un timeout                                   | El proxy dejó pasar; el fallo está más allá — el origen no escucha ahí, o no responde              |

**4.3 — El límite, que hay que saber antes de fiarse:** sondear **no enumera**.
Una allowlist de regex no se puede recorrer desde fuera, así que se puede afirmar
«todo lo que pedí está permitido» y **nunca** «el proxy no permite nada más».
Quien necesite la respuesta exacta lee el fichero (§4.1). Y un healthcheck en
verde tampoco dice nada de esto: el `403 Access denied` que afirma sólo prueba que
el demonio está vivo y aplicando su política.

## 5. Cuando el 403 no es del filtro: el `Allow` por IP cliente

tinyproxy admite clientes de `10.0.0.0/8`, `172.16.0.0/12` y `192.168.0.0/16`
—las redes que Docker asigna a sus bridges—, y **no tiene ninguna entrada IPv6**.
Normalmente eso no molesta a nadie. Molesta en dos casos:

- el operador configuró `default-address-pools` fuera de RFC1918;
- o habilitó IPv6 en los bridges del stack.

**El síntoma es indistinguible del de un host no permitido**: el cliente ve un
403 en el CONNECT en los dos casos. Se separan mirando el log del proxy (§4.2), y
sólo ahí:

```bash
# La IP del cliente que el proxy está viendo, y con qué red la obtuvo.
docker inspect -f '{{range $n,$c := .NetworkSettings.Networks}}{{$n}}={{$c.IPAddress}} v6={{$c.GlobalIPv6Address}}
{{end}}' agentic-api-server
# Las redes que el proxy admite hoy.
docker compose exec egress-proxy grep '^Allow' /etc/tinyproxy/tinyproxy.conf
```

Si la IP del cliente cae fuera de esas tres redes, el arreglo bueno es devolver
los bridges a RFC1918. Ampliar la lista `Allow` es un cambio en el repo, con PR, y
hay que decirlo en voz alta: **relaja la ACL de cliente para todo el mundo**, y la
seguridad de este proxy vive en el filtro de destino, no en quién pregunta.

## 6. Esto NO es control de exfiltración

Hay que repetirlo porque es lo que más se malinterpreta. tinyproxy filtra el
destino del CONNECT y el `Host:`; el cuerpo de una sesión TLS le es opaco. Abrir
`mcp.atlassian.com` significa que **cualquier** sandbox de **cualquier** tenant
puede enviar **cualquier cosa** a Atlassian mientras dure una ejecución. Es un
control de **alcanzabilidad**, no de contenido.

Lo que sí acota el contenido vive en otro sitio, y es en eso en lo que hay que
apoyarse al aprobar una apertura: el `security_level='sandboxed'` de las tools MCP
importadas ([ADR 0052](../05-architecture-decisions/0052-import-mcp-tools-catalogo.md)),
los guardrails `pre_tool` / `post_tool`, las políticas de aprobación por categoría
de acción sensible, y que las credenciales del servidor salgan de Vault.

## 7. Dónde vive el filtro: dos copias en el repo, una en un host instalado

| Dónde                   | Ruta                                                                                | Quién la escribe                                                                                           |
| ----------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Repo — imagen canónica  | `docker/egress-proxy/filter.txt`                                                    | El renderizador (bloque generado) o un PR (el resto)                                                       |
| Repo — copia del wizard | `apps/installer/backend/src/installer_backend/stack_assets/egress-proxy/filter.txt` | Igual, **en el mismo commit**: `test_installer_ships_stack_assets.py` exige que sean idénticas byte a byte |
| Host instalado          | `{compose_dir}/stack/egress-proxy/filter.txt`                                       | El renderizador con `--filter`. Aquí no existe ni `docker/` ni el paquete `api_server`                     |

**Por qué dos en el repo:** la imagen que CI construye y Trivy escanea sale de
`docker/`; la que corre una instalación sale del paquete del instalador, que copia
sus auxiliares **byte a byte** porque en el destino no hay ningún `docker/` del que
copiarlos. Tocar una sola pone la suite en rojo, y con razón.

**Por qué una en el host, y con el bloque vacío:** el instalador escribe el filtro
en `GENERATE_CONFIG`, cuando **todavía no hay base de datos** (las migraciones y la
siembra van después), así que no puede consultar el ajuste ni lo hará. Nace con los
centinelas y nada dentro; la siembra posterior deja el ajuste en `[]`. Los dos dicen
lo mismo sin haberse preguntado nada.

Por eso, en un host instalado, `--filter` no es una comodidad: es la única
invocación correcta. Sin ella el script buscaría las dos rutas del repo y fallaría
—`error: no existe …`— que es el fallo bueno; el malo sería que existieran y
escribir en el sitio que la imagen no lleva.

## 8. Las medidas (D10 del ADR), con fecha y método

El ADR exige que estas dos cosas se **midan**, no se deduzcan. Medidas el
**2026-09-03** contra la imagen real del repo (`docker build docker/egress-proxy/`
→ alpine 3.20 + tinyproxy 1.11), con Docker Desktop 29.7.2 sobre Windows/WSL2, un
cliente `curl` en una red docker de usuario (172.18.0.0/16) y `example.com` como
destino:

| Qué se midió                                   | Resultado                                                                                                                                                               |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Contra qué cadena casa el filtro en un CONNECT | El **host pelado**. `^example\.com$` → `connect=200` en 443 **y** en 8443; `^example\.com:443$` → 403 y el log dice `Proxying refused on filtered domain "example.com"` |
| ⇒ Consecuencia para la línea generada          | **Sin grupo de puerto.** El precedente `^ollama(:[0-9]+)?$` es de HTTP en claro, donde el `Host:` arrastra el `:11434`; en un CONNECT el puerto no viaja                |
| Un CONNECT a un puerto fuera de `ConnectPort`  | 403 al cliente y **ninguna línea en el log** (medido con 9443). Ausencia de log = puerto, no filtro                                                                     |
| Un cliente fuera de las redes `Allow`          | 403 al cliente y `Unauthorized connection from "100.64.7.3".` en el log (cliente puesto a propósito en 100.64.7.0/24)                                                   |
| `build` con sólo `filter.txt` cambiado         | **≈ 3,7 s** (la capa del `apk add` está cacheada; en frío, ≈ 30 s)                                                                                                      |
| Recrear el contenedor                          | **≈ 2,7 s**                                                                                                                                                             |
| **Ventana total de aplicación**                | **≈ 7 s**                                                                                                                                                               |

Ese último número es el que el ADR pedía para decidir si urge montar el filtro
como bind (su deuda aplazada): con 7 segundos, **no urge por coste**. El
disparador que queda vivo es el otro — que las imágenes se publiquen.

Y un ruido conocido, para que nadie lo persiga durante un triaje: tinyproxy 1.11
escupe `WARNING ... deprecated option FilterExtended, use FilterType` en cada
arranque. Es informativo; el filtro funciona.

## Verificación (la lista corta)

- [ ] El ajuste dice lo que se aprobó, y la fila de `audit_log` dice quién.
- [ ] El `filter.txt` **de la copia correcta** (§7) tiene el host dentro de los
      centinelas, y nada fuera de ellos ha cambiado.
- [ ] Se ejecutó `docker compose build egress-proxy && docker compose up -d --force-recreate egress-proxy`.
- [ ] El sondeo de §4.2 devuelve `connect=200` para lo que se abrió y
      `connect=403` para lo que se cerró.
- [ ] `docker compose ps egress-proxy` lo da `healthy`
      ([health-check.md](./health-check.md)).

## Ver también

- [ADR 0165](../05-architecture-decisions/0165-allowlist-de-hosts-mcp-remotos-en-el-egress.md)
  — quién manda sobre la allowlist, y qué NO controla.
- [ADR 0019](../05-architecture-decisions/0019-egress-red-sandbox-agent-runtime.md)
  — por qué existe el egress-proxy y por qué la red de agentes es `internal`.
- [ADR 0021](../05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md)
  — los proveedores LLM del filtro, que viven fuera del bloque generado.
- [Gotcha: el agent-runtime no llega al LLM in-stack](../03-guides/gotchas/agent-runtime-egress-blocks-in-stack-llm.md)
  — el mismo `403 Filtered` visto desde una ejecución.
- [restart-services.md](./restart-services.md) — recrear servicios sin perder datos.
- [02-troubleshooting.md](./02-troubleshooting.md) — averías frecuentes del stack.
