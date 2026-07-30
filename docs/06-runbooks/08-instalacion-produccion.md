---
title: "Manual: instalación en PRODUCCIÓN paso a paso (con dominio propio)"
docs_language: es
audience: operador, system admin
updated: 2026-07-18
---

# Manual — Instalación en producción, de cero a servicio publicado

Guía completa y ordenada para dejar la plataforma **en producción** en una
máquina dedicada, publicada bajo un dominio propio (usamos
**`https://example.com`** como ejemplo en todo el manual — sustitúyelo por el
tuyo). Cada paso indica qué hace, cómo verificarlo y qué hacer si falla.

Este manual es el **camino feliz de producción de punta a punta**. Los detalles
finos de cada pieza viven en sus runbooks, enlazados en cada sección:
[instalación desde cero](01-installation-from-scratch.md) (referencia del
instalador), [custom domain](07-custom-domain.md) (dominio/SSO en detalle),
[health-check](health-check.md), [backups](backups.md) y
[rotación de claves](05-key-rotation.md).

> **Alcance**: Docker Compose en **una sola máquina** (CLAUDE.md). Sin
> Kubernetes, sin multi-host, sin HA multi-instancia.
>
> **Camino soportado**: el **CLI desatendido** (`scripts/install.sh`). El wizard
> HTTP existe pero HOY es una simulación de flujo (no aprovisiona); no lo uses
> para producción. Ver [01-installation-from-scratch.md](01-installation-from-scratch.md).

---

## 0. Checklist previa (antes de tocar la máquina)

Reúne TODO esto antes de empezar; el instalador aborta pronto si falta algo,
pero mejor no empezar a medias:

- [ ] **Máquina** dedicada: Linux x86_64, **8 GiB de RAM mínimo** (16 GiB
      recomendado para el perfil `recommended`), **50 GiB de disco libre** para
      datos, Docker Engine **24.0+** y Docker Compose **v2.21+**.
- [ ] **Dominio** que controlas (aquí `example.com`) y acceso al panel DNS.
- [ ] **Puertos 80 y 443 libres** en la máquina y abiertos en el firewall
      hacia internet (la única superficie publicada es el proxy Caddy,
      ADR 0061). Ningún otro puerto debe exponerse públicamente.
- [ ] **Credenciales de al menos un proveedor LLM** del catálogo cerrado
      (ADR 0021): Claude Agent SDK, GitHub Copilot, Azure AI Foundry (APIM) u
      Ollama. Para producción recomendamos DOS (uno gestionado + Ollama local)
      — es lo que trae el perfil `recommended`.
- [ ] **Un gestor de secretos** (o sobres sellados) donde guardar, en cuanto
      aparezcan: las _unseal keys_ de Vault, el _root token_, y la contraseña
      inicial del administrador. **Se muestran UNA sola vez.**
- [ ] Un **email real** para los certificados TLS (avisos de expiración de
      Let's Encrypt), p.ej. `ops@example.com`.

## 1. Prepara el DNS (hazlo primero: propaga mientras instalas)

En tu panel DNS, crea el registro que apunta el dominio a la IP pública de la
máquina:

```text
example.com.    A       203.0.113.10      ; IP pública de tu máquina
; o, si usas un alias:
example.com.    CNAME   mi-host.mi-nube.example.net.
```

Verifica la propagación antes del paso 4 (ACME la necesita):

```bash
dig +short example.com
# → debe devolver la IP pública de la máquina
```

## 2. Prepara el host

```bash
# Docker + Compose v2 (según tu distro; ejemplo Debian/Ubuntu):
curl -fsSL https://get.docker.com | sh
docker --version          # >= 24.0
docker compose version    # >= v2.21

# El repositorio del sistema, en la máquina:
git clone <url-del-repo> /opt/agentic-platform
cd /opt/agentic-platform

# La raíz de datos (el instalador crea el árbol interior con permisos):
sudo mkdir -p /data/agent-platform
```

**Firewall** (ejemplo con ufw): solo SSH + web.

```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

## 3. Escribe tu `install.yaml` de producción

Parte del perfil `recommended` y ajústalo. Copia — **nunca edites el perfil del
repo en sitio ni comitees el resultado** (contiene secretos):

```bash
cp scripts/install-profiles/recommended.yaml /root/install.yaml
chmod 600 /root/install.yaml
```

Contenido de referencia para `https://example.com` (los `CHANGE_ME_…` son
placeholders: **sustitúyelos por secretos propios de alta entropía**, p.ej.
`openssl rand -hex 32`):

```yaml
# /root/install.yaml — producción en example.com
system:
  domain: example.com # el dominio público, SIN esquema ni path
  environment: production # activa los guards de producción (sin secretos dev)
  tls_mode: acme # Caddy emite el certificado público (Let's Encrypt)
  tls_acme_email: ops@example.com

resources:
  worker_replicas: 4 # 4 workers para producción media
  worker_memory_gib: 8
  gpu_enabled: false # true solo con GPU NVIDIA (perfil gpu)

storage:
  data_root: /data/agent-platform
  minio_bucket: agentic-platform
  minio_access_key: CHANGE_ME_minio_access
  minio_secret_key: CHANGE_ME_minio_secret_de_alta_entropia

providers:
  azure_foundry: # proveedor gestionado (ejemplo); ver ADR 0021
    enabled: true
    apim_endpoint: https://apim.example.com/openai
    api_key: CHANGE_ME_azure_apim_key
  ollama: # local, segundo proveedor (redundancia)
    enabled: true
    endpoint: http://ollama:11434

tenant:
  tenant_name: MiEmpresa # el primer tenant (equipo/departamento)
  admin_email: admin@example.com

ports:
  admin_panel: 3000 # internos tras Caddy; no se publican
```

Notas sobre TLS (`system.tls_mode`, ADR 0061):

| Modo       | Cuándo                     | Qué necesita                                                                  |
| ---------- | -------------------------- | ----------------------------------------------------------------------------- |
| `acme`     | **Producción con dominio** | `tls_acme_email` + DNS apuntando (paso 1) + 80/443 alcanzables desde internet |
| `provided` | Certificado corporativo    | `tls_cert_path` + `tls_key_path` en el host                                   |
| `internal` | Pruebas sin dominio        | Nada (CA local de Caddy, cert autofirmado)                                    |

## 4. Ejecuta la instalación

```bash
cd /opt/agentic-platform
./scripts/install.sh --config /root/install.yaml
```

Qué ocurre, en orden (cada fase con su código de salida — tabla completa en
[01-installation-from-scratch.md](01-installation-from-scratch.md#fases-y-códigos-de-salida)):

1. **prereqs** — valida Docker/Compose/RAM/disco/puertos. Si algo falla,
   aborta ANTES de tocar nada, con el mensaje de remediación.
2. **generate_config** — escribe `docker-compose.yml`, `.env` (0600, secretos
   CSPRNG, cero marcadores dev en producción), `config/global.yaml` y
   `caddy/Caddyfile` bajo `/data/agent-platform/`.
3. **pull_images / start_stack** — descarga imágenes y levanta el stack con
   `up -d --wait` (espera healthchecks).
4. **migrations** — aplica todas las migraciones Alembic (servicio one-shot).
5. **bootstrap_vault** — init + unseal + KV v2 + políticas. ⚠️ Las **unseal
   keys y el root token se muestran aquí UNA sola vez.**
6. **seed_tenant** — crea el tenant `MiEmpresa`, el usuario
   `admin@example.com` (contraseña de un solo uso, también mostrada UNA vez) y
   siembra el catálogo builtin (equipos, plantillas, KBs, tools).

> **En el momento en que el CLI imprima el bloque de credenciales, cópialo a tu
> gestor de secretos.** No hay recuperación: sin las unseal keys no se puede
> desellar Vault tras un reinicio; sin la contraseña no entras al panel.
> Detalle: [guardar las credenciales](01-installation-from-scratch.md#guardar-las-credenciales-y-las-unseal-keys).

Si falla a mitad (código 4, PROVISION): corrige la causa y decide — reintento
limpio con `scripts/uninstall.sh --purge-data` + reinstalar, o
`scripts/reinstall.sh --fresh`. Si Vault ya se había inicializado, el reintento
NO vuelve a mostrar las unseal keys (ver la nota del runbook 01).

## 5. Publica bajo tu dominio: `https://example.com`

> Versión resumida aquí; el detalle completo (topologías, nginx, SCIM, SSO)
> está en [07-custom-domain.md](07-custom-domain.md).

Con `system.domain: example.com` y `tls_mode: acme`, Caddy ya sirve el dominio
con certificado público en cuanto el DNS propaga: la SPA en
`https://example.com/` y el API bajo `https://example.com/api/*`
(**single-origin**, ADR 0061 — Caddy quita el prefijo `/api` y enruta al
api-server; todo lo demás va a la SPA).

Queda decirle a la PLATAFORMA cuál es su origen público (de esto penden las
URLs de SSO y los enlaces absolutos):

1. Entra en `https://example.com/` con `admin@example.com` y la contraseña del
   revelado único. Cámbiala en el primer login.
2. Ve a **Plataforma → SSO / Autenticación → «URL base pública de la
   aplicación»** y fija:
   - **URL base pública**: `https://example.com` (sin path)
   - **Prefijo de API**: `/api`
3. Guarda. La **URL de callback** mostrada se recalcula a
   `https://example.com/api/auth/sso/oidc/callback` — ese es el valor exacto a
   registrar en tu IdP si usas SSO.

Equivalente por entorno (bootstrap, en el `.env` generado):

```bash
API_SERVER_SSO_REDIRECT_BASE_URL=https://example.com
API_SERVER_API_PATH_PREFIX=/api
```

**Si vas a usar SSO** (OIDC/SAML): registra en el IdP las URLs que muestra la
pantalla SSO. Con `https://example.com` quedan:

| Valor         | URL efectiva                                     |
| ------------- | ------------------------------------------------ |
| Callback OIDC | `https://example.com/api/auth/sso/oidc/callback` |
| ACS SAML      | `https://example.com/api/auth/sso/saml/acs`      |
| SP EntityID   | `https://example.com/api/auth/sso/saml/metadata` |

Deben coincidir **carácter a carácter** con lo registrado en el IdP. Desde la
migración 0115 puedes configurar **varios proveedores SSO a la vez** (p.ej.
Google y Microsoft): cada config habilitada pinta su propio botón en `/login`
(a partir de la segunda, el `display_name` es obligatorio).

Verificación del dominio:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://example.com/            # 200 (SPA)
curl -s -o /dev/null -w "%{http_code}\n" https://example.com/api/healthz # 200 (API)
```

## 6. Verificación post-instalación (no te la saltes)

Checklist mínima — el detalle en [health-check.md](health-check.md):

- [ ] `docker compose ps` en el data_root: **todos** los servicios
      `Up (healthy)`.
- [ ] `https://example.com/api/healthz` → `200`.
- [ ] `GET /api/admin/system-health` (con sesión de System Admin) →
      `status: ok`, ningún servicio `down`.
- [ ] Login con `admin@example.com` funciona y la contraseña YA está cambiada.
- [ ] Vault desellado (y las unseal keys, guardadas fuera de la máquina).
- [ ] El contenedor del instalador ya no existe.
- [ ] Grafana accesible (overlay de monitorización): dashboard **«Plataforma
      Agéntica»** con métricas llegando (colas, tareas, backup).
- [ ] Crea un proyecto desde una plantilla, un plan de 1 tarea, apruébalo y
      arráncalo: el ciclo completo (dispatch → run → review → validación
      humana) debe fluir sin intervención. Es la prueba de fuego real.

## 7. Endurecimiento y operación (primer día)

- **Backups**: verifica el beat diario y haz una prueba de restore en frío
  ([backups.md](backups.md), [04-disaster-recovery.md](04-disaster-recovery.md)).
  Configura el destino **offsite** si procede.
- **Claves**: agenda la rotación periódica
  ([05-key-rotation.md](05-key-rotation.md)).
- **dblink** (restore selectivo por tenant): créala una vez —
  `CREATE EXTENSION IF NOT EXISTS dblink;` como superuser en la BD
  ([dr-tenant-restore.md](dr-tenant-restore.md), sección «limitaciones»).
- **Alertas**: Alertmanager ya enruta las alertas de app (colas, DLQ, backup)
  a notificaciones del System Admin; configura además el canal externo del
  tenant (Telegram/email) en Notificaciones.
- **Cuentas**: crea los usuarios/miembros reales del tenant y desactiva lo que
  no uses. Si el login va a estar expuesto a internet sin SSO, ten presente que
  el backend soporta TOTP/WebAuthn pero **la UI de enrolamiento MFA aún no
  está cableada** — hasta entonces, contraseñas fuertes + rate-limit (activo
  por defecto) o SSO delante.
- **Actualizaciones**: el procedimiento de upgrade vive en
  [03-system-upgrade.md](03-system-upgrade.md).

## 8. Problemas frecuentes

| Síntoma                                           | Causa típica                       | Acción                                                                                       |
| ------------------------------------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------- |
| ACME no emite el certificado                      | DNS sin propagar o 80/443 cerrados | `dig +short example.com`; abre 80/443; reintenta (Caddy reintenta solo)                      |
| `https://example.com/` da el cert autofirmado     | `tls_mode` quedó en `internal`     | Corrige `install.yaml` y re-genera, o edita el Caddyfile y recarga                           |
| Login SSO cae en la SPA (404) tras volver del IdP | Falta el **Prefijo de API** `/api` | Fíjalo en la tarjeta SSO y re-registra el callback (§5)                                      |
| `redirect_uri_mismatch` en el IdP                 | Callback registrado ≠ efectivo     | Copia el callback EXACTO de la pantalla SSO al IdP                                           |
| Un servicio `Restarting`                          | —                                  | `docker compose logs <servicio> --tail 50` + [02-troubleshooting.md](02-troubleshooting.md)  |
| CLI sale con código ≠ 0                           | Ver tabla de códigos               | [01-installation-from-scratch.md](01-installation-from-scratch.md#fases-y-códigos-de-salida) |
| Vault sellado tras un reinicio de la máquina      | Comportamiento normal              | Desella con las unseal keys ([dr-vault-unseal-rotation.md](dr-vault-unseal-rotation.md))     |

## Relacionado

- [01-installation-from-scratch.md](01-installation-from-scratch.md) — referencia completa del instalador (wizard/CLI, fases, códigos).
- [07-custom-domain.md](07-custom-domain.md) — dominio propio en detalle (topologías, nginx, SCIM, cambios de dominio con SSO activo).
- [ADR 0061](../05-architecture-decisions/0061-reverse-proxy-tls.md) — Caddy single-origin + TLS.
- [ADR 0021](../05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md) — catálogo cerrado de proveedores LLM.
