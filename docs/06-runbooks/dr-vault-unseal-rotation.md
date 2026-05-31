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
