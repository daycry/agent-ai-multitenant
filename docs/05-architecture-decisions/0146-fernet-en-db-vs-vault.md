---
title: "ADR 0146: Cifrado Fernet en Postgres vs Vault para SSO, notificaciones y webhooks"
status: proposed
date: 2026-07-31
deciders: [operador]
relates_to: [0021, 0028, 0145]
plan_referenced: prod-10-vault-secretos-operables
task: [task_prod10_10]
docs_language: es
---

# ADR 0146: Fernet-en-DB vs Vault para SSO, notificaciones y webhooks

> **Estado `proposed`. NADA se ha migrado.** Esta decisión contradice —o
> confirma— un principio rector del `CLAUDE.md`, y su radio de explosión es todo
> el material cifrado en Postgres de tres familias de secretos. La escribe un
> agente; la firma un humano. `task_prod10_11` implementa lo que se firme.

## El conflicto, en una frase

El `CLAUDE.md` dice «**Vault es la única vía de credenciales**» y
`llm_providers/vault.py` lo cumple al pie de la letra: la credencial de un
proveedor LLM va a Vault, la BD guarda **sólo el puntero**
(`secret_vault_path`), y sin Vault la escritura devuelve 503. Pero otras tres
familias de secretos **no pasan por Vault en absoluto**: se cifran con Fernet en
columnas de Postgres, con una clave derivada de una variable de entorno.

| Familia                               | Dónde vive el secreto                                            |
| ------------------------------------- | ---------------------------------------------------------------- |
| Client secrets OIDC (SSO)             | Columna cifrada, `API_SERVER_SSO_ENCRYPTION_KEY(S)`              |
| Secretos de canales de notificación   | Columna cifrada, `API_SERVER_NOTIFICATION_ENCRYPTION_KEY(S)`     |
| Signing secrets de webhooks entrantes | Columna cifrada, `API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY(S)` |

O es una excepción legítima y hay que escribirla, o es una deuda y hay que
pagarla. Lo que no puede seguir es que el principio diga una cosa y el código
haga otra en la mitad de los casos: eso deja a quien lee el `CLAUDE.md` sin saber
dónde buscar un secreto, y a quien audita sin saber qué esperar.

## Lo que ha cambiado desde que se escribió el plan

El plan prod-10 se redactó el 2026-06 y **recomendaba la opción A** (migrar a
Vault). Al verificar la premisa hoy, 2026-07-31, el terreno ha cambiado: el plan
**prod-05** acaba de entregar, para estas tres familias exactamente:

- **anillos de claves MultiFernet** (`*_ENCRYPTION_KEYS`, cabeza + cola), de modo
  que una clave nueva se añade sin invalidar el material existente;
- `api_server.cli.reencrypt_secrets` — re-cifrado masivo sobre la clave cabeza,
  con `--dry-run` que dice si el paso de retirada es seguro;
- guardas de arranque que rechazan un anillo con defaults de dev.

Es decir: la capacidad que Vault aportaría sobre Fernet —**rotación real**— ya
existe en el camino Fernet. Eso no cierra la decisión, pero cambia su balance, y
la recomendación de junio ya no se puede aplicar sin releerla.

## Opciones

### A — Migrar a Vault y degradar el camino Fernet a 503

Cuando Vault esté cableado, estas tres familias se escriben en
`secret/tenants/{tenant}/…`; la BD guarda el puntero; sin Vault, las escrituras
devuelven 503 igual que en el flujo LLM. Script de migración para el material
existente.

- **A favor**: un solo modelo mental para «dónde vive un secreto»; el principio
  del `CLAUDE.md` deja de tener excepciones; los secretos salen del backup de
  Postgres (hoy un dump lleva el ciphertext, y quien tenga el dump **y** la env
  var tiene los secretos); audit log de acceso a secretos gratis, que es de
  Vault y no de una columna.
- **En contra**: convierte a Vault en dependencia **dura** de funciones que hoy
  sobreviven sin él (SSO, notificaciones, webhooks entrantes). Con Vault sellado
  —el escenario del ADR 0145, que ocurre en cada reinicio del host— el login SSO
  dejaría de funcionar. Hoy no. Y hay que escribir y probar una migración de
  datos con material que, si se pierde, obliga a re-configurar cada IdP y cada
  canal a mano.

### B — Bendecir la excepción, documentarla y ponerle salvaguardas

Se declara que estas tres familias viven cifradas en la BD **por diseño**, se
escribe en el `CLAUDE.md` con su razón, y se añaden las salvaguardas que hoy
faltan: excluir esas columnas del export de backups o cifrarlas con una clave
distinta de la de las columnas, para que un dump robado no sea suficiente.

- **A favor**: cero riesgo de migración; SSO sigue funcionando con Vault sellado,
  que es lo que un operador espera de un login; se apoya en la rotación que
  prod-05 acaba de entregar en vez de duplicarla.
- **En contra**: el principio del `CLAUDE.md` pasa a tener una excepción escrita,
  y las excepciones escritas se citan para abrir la siguiente. La clave sigue
  viviendo en una variable de entorno del host, o sea al alcance de cualquiera
  que lea el `.env`.

### C — Híbrida: Vault cuando está, Fernet como respaldo

Escribir en los dos sitios y leer de Vault primero.

**Descartada sin desarrollar.** Dos fuentes de verdad para el mismo secreto es
la peor de las tres: duplica la superficie de exposición (el secreto está en los
dos sitios) sin eliminar ninguna, y añade la pregunta «¿cuál gana?» a cada
lectura y a cada rotación.

## Recomendación

**Sin recomendación firme, y a propósito.** El plan recomendaba A; la entrega de
prod-05 ha movido el balance lo bastante como para que repetir esa recomendación
sin que un humano relea el trade-off sería exactamente el modo de fallo nº1 de
`verificar-antes-de-implementar.md` — dar por buena una premisa envejecida.

Lo que sí está claro y no depende de la decisión:

1. **La asimetría de disponibilidad es el eje real.** Migrar a Vault hace que un
   Vault sellado apague el login SSO. Quien firme debe decidir eso a sabiendas,
   no descubrirlo en el primer reboot.
2. **La salvaguarda de backups de la opción B hace falta igualmente** mientras
   quede un solo secreto cifrado en columnas — o sea, hasta que A esté migrada y
   verificada. No es trabajo exclusivo de B.

## Qué NO se ha hecho

- No se ha migrado ni un secreto.
- No se ha tocado ningún read-path.
- No se ha modificado el `CLAUDE.md`.

`task_prod10_11` queda bloqueada hasta que este ADR pase a `accepted` con una
opción elegida.
