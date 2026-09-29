---
plan_id: prod-05-rotacion-claves
title: Rotación de claves ejecutable — MultiFernet, re-cifrado, dual JWT y job real
started_at: 2026-07-31
completed_at: null
status: pending_human_validation
tasks_done: 10
tasks_total: 10
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 10 casillas están
> marcadas desde el 2026-08-10 y el plan seguía etiquetado `pending_approval`
> («no empezado»). Esta entrada se escribe el 2026-09-29, en la consolidación
> del roadmap, tras volver a ejecutar sus tests declarados. Los tests humanos
> del plan siguen sin validar.

# Changelog — Plan prod-05 · Rotación de claves ejecutable

La auditoría de producción de 2026-06 encontró que la plataforma **no tenía ningún
camino ejecutable para rotar sus claves**. El job de rotación usaba siempre un
cliente fake y auditaba `SUCCEEDED` sin rotar nada; las tres claves Fernet eran
de clave única, así que rotarlas volvía ilegibles todos los secretos guardados; el
JWT validaba contra un solo secreto; y la clave de backups no llevaba key-id, con
lo que rotarla dejaba ilegibles los bundles históricos. El runbook omitía seis de
las ocho claves.

Este plan cierra los siete hallazgos `gap2-1` a `gap2-7`.

## Resultado por fases

- **Fase A — Fernet rotable (tasks 01-03).** Los cuatro builders pasan a
  `MultiFernet` con anillos `*_ENCRYPTION_KEYS`: la primera clave cifra, todas
  descifran, y la variable singular antigua sigue valiendo como anillo de uno.
  `python -m api_server.cli reencrypt-secrets` re-cifra por tabla, con dry-run e
  idempotente. El TOTP tiene clave propia (ADR 0143, opción A) con fallback al
  anillo SSO, así que la decisión se despliega sin ninguna acción del operador.
- **Fase B — JWT dual (task 04).** `API_SERVER_JWT_SECRETS`: firma con la primera,
  verifica contra todas. Una rotación ya no corta sesiones ni tokens de agente en
  vuelo.
- **Fase C — Job de rotación real (tasks 05-07).** Cliente `hvac` real. Sin Vault
  vivo el ciclo termina `SKIPPED` con alerta, nunca `SUCCEEDED`. La propagación
  es regenerar el `.env` y reiniciar coordinadamente (ADR 0144, opción B): Vault
  no entra en el camino crítico de arranque. La rotación de MinIO acuña la
  credencial nueva antes de escribir en KV y revoca la antigua tras propagar.
  Probarla contra MinIO real destapó que `revoke()` no era idempotente: un
  reintento tras una propagación a medias dejaba `pending_apply=true` para
  siempre. Arreglado.
- **Fase D — Backups con anillo (task 08).** Formato de bundle v2 con key-id en la
  cabecera. El restore selecciona por key-id en v2 y prueba el anillo completo en
  v1: los bundles antiguos siguen siendo legibles mientras su clave esté en el
  anillo.
- **Fase E — Runbook y drill (tasks 09-10).** `docs/06-runbooks/05-key-rotation.md`
  reescrito clave por clave, con verificaciones que el código cumple. Drill en
  `tests/integration/test_key_rotation_drill.py`.

## Lo que NO entra

- **Leer secretos de Vault en runtime con recarga.** Descartado por el ADR 0144
  para el alcance de una sola máquina. Si el despliegue pasa a multi-máquina, el
  ADR se supersede.
- **Operabilidad de Vault** (tokens, unseal, custodia de claves): es prod-10.

## Verificación (2026-09-29)

Los ficheros de test que declara el plan, ejecutados contra el Postgres de
desarrollo:

| Árbol                         | Ficheros | Resultado             |
| ----------------------------- | -------: | --------------------- |
| `tests/` (unit + integration) |       10 | 121 passed, 5 skipped |

Los cinco saltos son del test de rotación de MinIO, que exige el servicio vivo y
sus credenciales de root. Ejecutado aparte contra el MinIO del compose de
desarrollo, con las credenciales de `docker/.env`:

```text
tests/integration/test_minio_rotation_applies_to_service.py
7 passed in 81.85s
```

El test acuña y revoca una service account de prueba. No toca la credencial de
root.
