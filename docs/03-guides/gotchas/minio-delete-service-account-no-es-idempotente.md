---
title: MinIO `delete_service_account` NO es idempotente (404 XMinioInvalidIAMCredentials)
area: docker
encountered: 2026-08-01
stack: minio-py 7.2.20, MinIO RELEASE.2025-x, prod-05 task_prod05_07
---

## Síntoma

Revocar dos veces la misma credencial de MinIO revienta:

```
workers.credential_rotation.CredentialRotationError: MinIO admin API refused to
delete service account 'EWDL1QNFS3Y8CHBWQM8E': MinioAdminException
```

El detalle que la envoltura se traga:

```
admin request failed; Status: 404, Body: {"Code":"XMinioInvalidIAMCredentials",
"Message":"The specified service account is not found", ...}
```

Y no aparece **nunca** en la suite: todos los dobles de
`tests/unit/test_vault_rotation_client_hvac.py` aceptaban el segundo borrado tan
felices. Sólo se ve corriendo contra un MinIO de verdad.

## Causa raíz

El `Protocol` `MinioCredentialRotator.revoke` promete «Idempotent», y la
implementación no lo era: se limitaba a delegar en
`MinioAdmin.delete_service_account`, y MinIO **no** trata borrar algo que no
existe como un no-op — devuelve 404 con el código
`XMinioInvalidIAMCredentials`. (S3 sí lo trata así para objetos; la API de
administración de IAM, no. La analogía es la trampa.)

Por qué importa más de lo que parece: el único llamante es
`revoke_previous_minio_credential`, el **paso 4** del add-then-remove de la
rotación de claves. Ese paso se ejecuta a mano después de la propagación, y se
reintenta cuando la propagación se quedó a medias. Con el 404 subiendo, el
reintento aborta y la entrada KV se queda con `pending_apply=true` **para
siempre**, describiendo una credencial anterior que ya no existe.

## Fix

`apps/workers/src/workers/credential_rotation_hvac.py`: `revoke()` distingue el
404 del resto y devuelve en silencio (con log `…already_revoked`).

```python
if _is_minio_not_found(exc):
    _log.info("credential_rotation.minio.service_account_already_revoked", ...)
    return
raise CredentialRotationError(...) from exc
```

`_is_minio_not_found` es **deliberadamente estrecho** — casa
`XMinioInvalidIAMCredentials`, o un 404 con «not found» — porque tragarse un 403
(credencial de root equivocada) o un error de red convertiría «no pude hablar con
MinIO» en «ya estaba revocada», que es exactamente cómo una credencial sobrevive
a su propia rotación.

Se matchea por **cadena**: `MinioAdminException` no expone un código tipado, y
el módulo importa `minio` de forma diferida a propósito para que un worker que
nunca rota no pague la dependencia.

## Cómo verificar el fix

Sin MinIO (corre en CI):

```
.venv/Scripts/python.exe -m pytest tests/unit/test_vault_rotation_client_hvac.py -q -p no:randomly
```

Los tres que fijan la trampa: `test_revoking_an_already_revoked_credential_is_a_no_op`,
`test_a_permission_error_on_revoke_still_fails_loudly`,
`test_a_transport_failure_on_revoke_still_fails_loudly`.

Contra MinIO real (compose levantado):

```
ROTATION_TEST_MINIO_ROOT_USER=… ROTATION_TEST_MINIO_ROOT_PASSWORD=… \
  .venv/Scripts/python.exe -m pytest tests/integration/test_minio_rotation_applies_to_service.py -q
```

7 tests. Sin credenciales se **saltan** los que tocan el servicio, con el motivo
impreso; los dos del fallo ruidoso corren siempre.

## La lección, que es más grande que el bug

El plan prod-05 tenía la rotación de MinIO «implementada y probada» — con
dobles. Los dobles acreditaban el **orden** de las llamadas, que era la mitad
correcta; lo que el hallazgo gap2-2 pedía demostrar era el **efecto** en el
servicio, y eso un doble no puede refutarlo: dice «me llamaste», no «MinIO
cambió». La primera ejecución contra el MinIO real encontró el defecto en menos
de un minuto.
