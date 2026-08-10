---
title: Rotación de unseal keys de Vault
docs_language: es
audience: system admin, responsable de seguridad
updated: 2026-05-30
---

# Runbook — Rotación de unseal keys (Vault)

Rotar las claves de desellado (unseal keys) de HashiCorp Vault con
`vault operator rekey`, sin pérdida de datos ni de los secretos
custodiados. La rotación NO cambia la clave maestra que cifra el almacén
(eso es `rekey` del barrier, opcional al final); reparte de nuevo la clave
maestra en un **nuevo conjunto de shares de Shamir**, invalidando las
unseal keys antiguas.

Caso de uso: una unseal key se ha comprometido o sospechas que sí, rota un
custodio, o toca la rotación periódica de la política de seguridad.

Contexto del despliegue: Vault se inicializa con
[`scripts/init-vault.sh`](../../scripts/init-vault.sh) (Shamir 5-of-5,
threshold 3). Vault NO corre en modo dev en este escenario.

## Propósito

- Sustituir las unseal keys actuales por un conjunto nuevo, invalidando
  las viejas.
- Mantener Vault desellado y todos los secretos intactos durante y
  después de la rotación.

## Precondiciones

- Vault **inicializado, desellado y sano**:
  `GET /admin/system-health` reporta `vault: ok`, o bien:

  ```bash
  docker compose -f docker/docker-compose.yml \
    exec -T vault vault status -format=json
  ```

  con `"sealed": false`.

- Dispones del **número-umbral de unseal keys actuales** (threshold = 3 de
  5 por defecto): la rotación las exige para autorizar el rekey.
- Custodios disponibles para recibir las **nuevas** shares (idealmente uno
  por share, en ubicaciones separadas).
- Un **backup reciente** del volumen `vault_data` por si acaso (ver
  [dr-manual-backup.md](./dr-manual-backup.md)).
- Acceso al contenedor `vault` vía `docker compose exec` (no requiere CLI
  de Vault en el host).

> La rotación es atómica: hasta que NO se aportan las suficientes claves
> nuevas-nonce, el rekey está en curso y puede **cancelarse** sin efecto.
> Las unseal keys antiguas siguen valiendo hasta que el rekey **completa**.

## Pasos

### 1. (Recomendado) backup de Vault antes de tocar nada

```bash
docker run --rm \
  -v agentic_vault_data:/data:ro \
  -v "$(pwd)":/backup \
  alpine tar czf /backup/vault_data-$(date +%Y%m%d-%H%M).tar.gz -C /data .
```

(El prefijo real del volumen depende del proyecto Compose; confírmalo con
`docker volume ls`.)

### 2. Inicia la operación de rekey

Pide un nuevo reparto de 5 shares con threshold 3 (ajusta a tu política).
Vault devuelve un `Nonce` que identifica esta operación de rekey:

```bash
docker compose -f docker/docker-compose.yml \
  exec -T vault \
  vault operator rekey -init -key-shares=5 -key-threshold=3
```

Anota el **Nonce**. Mientras el rekey está iniciado, Vault sigue
operativo con las claves antiguas.

### 3. Aporta el umbral de claves ACTUALES

Cada custodio introduce su unseal key **actual** referenciando el Nonce.
Repite hasta alcanzar el threshold (3 veces por defecto):

```bash
docker compose -f docker/docker-compose.yml \
  exec vault \
  vault operator rekey -nonce=<NONCE>
```

(Sin `-T` para que Vault pida la clave de forma interactiva y NO quede en
el historial del shell; introduce una clave por ejecución.)

Cuando se alcanza el threshold, Vault **emite las nuevas shares** en la
salida del último comando. Cópialas de inmediato — no se vuelven a
mostrar.

### 4. Distribuye y custodia las nuevas shares

- Entrega cada nueva share a su custodio en una ubicación separada
  (gestor de contraseñas, sobre sellado, smartcard…).
- Recuerda el umbral de pérdida: si pierdes
  `(shares - threshold + 1)` = 3 de las 5, NO podrás desellar y los datos
  serán irrecuperables.
- **Destruye** de forma segura las unseal keys **antiguas** en todos sus
  soportes (`shred -u` en archivos locales; invalida entradas en gestores
  de contraseñas). Tras un rekey completado, las antiguas ya no desellan,
  pero deben eliminarse igualmente para no inducir a error.

### 5. (Opcional) rota también la clave maestra del barrier

Para rotar la clave de cifrado del almacén (no solo su reparto), encadena:

```bash
docker compose -f docker/docker-compose.yml \
  exec -T vault vault operator rotate
```

Esto añade una nueva versión de la clave de cifrado para los datos nuevos;
no requiere re-desellar.

### Desellar tras un restore

Si llegas aquí desde un DR ([dr-full-restore.md](./dr-full-restore.md))
con el volumen `vault_data` restaurado, Vault arranca **sellado**.
Deséllalo con el umbral de unseal keys vigentes en el momento del backup:

```bash
docker compose -f docker/docker-compose.yml \
  exec vault vault operator unseal   # repite threshold veces
```

## Verificación

- `vault operator rekey -status` (con el Nonce) reporta el rekey como
  **completado** (`Started: false`).
- Vault sigue **desellado** y sano:
  `vault status` → `"sealed": false`; `GET /admin/system-health` →
  `vault: ok`.
- Los secretos siguen accesibles: lee uno de prueba con un token de
  servicio (no el root):

  ```bash
  docker compose -f docker/docker-compose.yml \
    exec -T -e VAULT_TOKEN=<token-servicio> vault \
    vault kv get secret/<ruta-de-prueba>
  ```

- **Sella y desella** de prueba con las **nuevas** claves en una ventana
  de mantenimiento para confirmar que el nuevo conjunto funciona ANTES de
  destruir cualquier copia de respaldo de emergencia.

## Rollback / aborto

- **Rekey iniciado pero aún sin completar**: cancélalo; las claves
  antiguas siguen siendo válidas y no hay efecto:

  ```bash
  docker compose -f docker/docker-compose.yml \
    exec -T vault vault operator rekey -cancel
  ```

- **Perdiste las nuevas shares antes de distribuirlas**: cancela el rekey
  (si aún no completó) y reinícialo; si YA completó y perdiste suficientes
  shares nuevas, restaura el volumen `vault_data` desde el backup del paso
  1 (Vault arranca sellado, desellable con las claves **antiguas**) y
  vuelve a rotar.
- **Vault queda sellado y no consigues desellar**: NO reinicialices
  (`vault operator init` destruye el almacén). Restaura `vault_data` desde
  backup y desella con el conjunto de claves correspondiente a ese backup.

## Incidente abierto — el material de init sigue en claro en el working tree

> **Estado el 2026-08-10: PENDIENTE.** Esta sección NO describe una operación
> hecha: describe la que hay que hacer. Se comprueba en un segundo:
>
> ```bash
> .venv/Scripts/python.exe scripts/check_no_secret_artifacts.py
> ```
>
> Hoy sale en rojo con 5 artefactos —`vault-init-output/init-response.json`,
> `root-token.txt` y `unseal-keys.txt`, dos de ellos con material `hvs.`—,
> escritos el **2026-05-20** y en disco desde entonces. Cuando el procedimiento
> de abajo esté ejecutado, ese comando sale en verde, y **sólo entonces** se
> puede marcar `task_prod10_01` del plan
> [prod-10](../roadmap/prod-10-vault-secretos-operables.md).

**Por qué no lo puede cerrar nadie que no tenga las llaves.** Los pasos 1 y 4
exigen custodias físicas/organizativas y el 3 exige el umbral de Shamir. Ningún
automatismo puede repartir sobres ni decidir quién custodia qué. Lo que sí está
hecho es todo lo demás: `scripts/init-vault.sh` ya no vuelve a escribir `.txt` en
claro (cifra a `age`/`gpg` o imprime una sola vez), el gate de CI y el hook
pre-commit fallan si el directorio reaparece con contenido, y
`scripts/vault-mint-service-tokens.sh` permite que los servicios dejen de usar el
root token **antes** de revocarlo.

### Orden exacto (no lo cambies: el paso 2 protege al 3)

**Precondición**: Vault desellado (`vault status` con `"sealed": false`) y un
backup reciente del volumen `vault_data` ([dr-manual-backup.md](./dr-manual-backup.md)).

1. **Reparte y custodia las 5 unseal keys.** Están en
   `vault-init-output/unseal-keys.txt` (y dentro de `init-response.json`). Una
   por custodio, en ubicaciones separadas: gestor de contraseñas corporativo o
   sobre sellado. **Perder ≥3 de 5 es perder los datos de Vault para siempre** —
   no hay recuperación, ni backup que valga sin las claves. Anota QUIÉN custodia
   QUÉ share en el registro de seguridad, **nunca en este repositorio**.

2. **Quita a los servicios de encima del root token, ANTES de revocarlo.** Con el
   root token actual todavía vivo:

   ```bash
   VAULT_TOKEN="$(cat vault-init-output/root-token.txt)" \
     ./scripts/vault-mint-service-tokens.sh >> docker/.env
   docker compose -f docker/docker-compose.yml up -d   # + los overlays que uses
   ```

   Acuña un token **periódico y huérfano** por política
   (`api-server`, `workers`, `orchestrator`, `notification-dispatcher`). Huérfano
   es la palabra clave: así el paso 3 no se los lleva por delante. Y periódico
   porque `VaultTokenManager` los renueva solo en segundo plano
   (`api_server.vault_client`, `workers.vault_client`).

   **Verifica antes de seguir**: `/admin/system-health` en `ok`, y los logs del
   api-server con `vault.token.lookup` mostrando TTL y políticas del token nuevo.
   Si esto no está verde, PARA: revocar ahora deja la plataforma sin secretos.

3. **Revoca el root token expuesto y emite otro.**

   ```bash
   # Revocar el que lleva desde el 2026-05-20 en disco
   docker compose -f docker/docker-compose.yml exec vault \
     vault token revoke "$(cat vault-init-output/root-token.txt)"

   # Emitir uno nuevo: requiere el UMBRAL de unseal keys (3 de 5)
   docker compose -f docker/docker-compose.yml exec vault vault operator generate-root -init
   #   -> apunta el nonce y el one-time password (OTP)
   docker compose -f docker/docker-compose.yml exec vault vault operator generate-root
   #   -> repetir 3 veces, una unseal key por invocación, con el mismo nonce
   docker compose -f docker/docker-compose.yml exec vault \
     vault operator generate-root -decode=<encoded-token> -otp=<otp>
   ```

   El token que sale del `-decode` es el nuevo root. **Va al gestor de
   contraseñas personal del responsable de seguridad y a ningún fichero de
   configuración**: los servicios ya no lo necesitan (paso 2).

   Comprobación de que la revocación surtió efecto: usar el token viejo contra
   Vault devuelve **403**.

   ```bash
   docker compose -f docker/docker-compose.yml exec vault \
     env VAULT_TOKEN="<token-viejo>" vault token lookup   # -> permission denied
   ```

4. **Borrado seguro de las copias locales.**

   ```bash
   shred -u vault-init-output/*                 # Linux / macOS
   ```

   En **Windows** `shred` no existe y `del` sólo suelta el puntero. Sobrescribe
   antes de borrar:

   ```powershell
   sdelete -p 3 -nobanner .\vault-init-output\*   # Sysinternals
   Remove-Item -Recurse -Force .\vault-init-output
   ```

   Si el disco es SSD con TRIM, sobrescribir no garantiza nada: asume que el
   material estuvo expuesto y da por buena la revocación del paso 3 como la
   defensa real. Por eso el paso 3 va antes que el 4 y no al revés.

5. **Confirma el cierre.**

   ```bash
   .venv/Scripts/python.exe scripts/check_no_secret_artifacts.py   # -> exit 0
   git status --porcelain | grep vault-init-output                 # -> vacío
   ```

   Y prueba el gate: crea `vault-init-output/dummy.txt` con cualquier contenido y
   lanza el hook pre-commit — tiene que fallar con un mensaje que explique el
   arreglo. Bórralo después.

6. **Anota aquí la fecha de ejecución y quién la hizo** (sin decir dónde están
   las custodias), y marca `task_prod10_01` en el plan prod-10.

### Lo que este procedimiento NO arregla

Que el material estuvo legible en el working tree ~3 meses, y que cualquier
proceso con acceso al repositorio —incluidos los agentes IA que trabajan sobre
él— pudo leerlo. La revocación del paso 3 corta el uso del token; las **unseal
keys no se pueden revocar**, sólo rotar. Si hay sospecha real de exfiltración,
encadena con el `rekey` de este mismo runbook (secciones 2-4 de arriba) para
invalidar las cinco shares antiguas.

## A quién avisar

- **Responsable de seguridad**: lidera la rotación y coordina a los
  custodios de las shares.
- **System Admin**: programa la ventana de mantenimiento y verifica la
  salud del stack tras la rotación.
- **Custodios de las shares**: cada uno recibe y guarda una nueva share, y
  destruye la antigua.

## Notas

- Vault NO expande variables de entorno en su config; el root token solo
  se usa para emitir tokens de servicio por política, nunca en configs de
  servicio (ver [`scripts/init-vault.sh`](../../scripts/init-vault.sh)).
- Si Vault queda atascado en `Restarting`, revisa
  [`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md)
  y [`vault-entrypoint-config-flag.md`](../03-guides/gotchas/vault-entrypoint-config-flag.md).
