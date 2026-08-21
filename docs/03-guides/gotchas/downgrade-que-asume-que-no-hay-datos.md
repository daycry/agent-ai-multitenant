---
title: Un `downgrade` que asume «no puede haber filas de esto» caduca al día siguiente
area: postgres
encountered: 2026-08-18
stack: Alembic 1.x, PostgreSQL 16, pytest integración (BD de ámbito sesión)
---

## Síntoma

`test_migration_0011_is_reversible` (en `tests/integration/test_max_review_retries_scope.py`)
**pasa en solitario y falla en la suite completa**:

```
sqlalchemy.exc.IntegrityError: NotNullViolationError:
  column "client_id" of relation "sso_configurations" contains null values
[SQL: ALTER TABLE sso_configurations ALTER COLUMN client_id SET NOT NULL]
```

El mensaje no menciona SAML, ni migraciones, ni qué borrar. Y como sólo aparece
en lote, la lectura fácil es «flaky de orden». No lo era.

## Causa raíz

Dos capas, y las dos importan:

1. **El defecto real (producción).** La migración `0033_sso_saml_columns`
   introdujo SAML relajando `issuer` y `client_id` a NULL, y su `downgrade` los
   volvía a poner `NOT NULL` con este razonamiento escrito en el docstring: «no
   `saml` rows can exist at downgrade time because the table only held OIDC rows
   before this revision». Era cierto **el día que se escribió** —esa revisión ES
   la que estrena SAML— y falso desde el siguiente. Con una sola configuración
   SAML viva, la cadena de migraciones **no es reversible**, que es justo lo que
   la regla dura de `CLAUDE.md` prohíbe llevar a un despliegue.

2. **Por qué sólo se ve en lote.** La BD de integración es de **ámbito sesión**
   y la comparten los 500+ ficheros. `test_key_rotation_drill.py` siembra una
   fila `saml` y no la retira. Cualquier test que baje la cadena por debajo de
   la 0033 después de él se come el error, tres capas más allá del sitio donde
   se sembró.

## Fix

- **La migración**: el `downgrade` borra lo que el esquema de destino no sabe
  representar (`DELETE FROM sso_configurations WHERE client_id IS NULL OR issuer
IS NULL`) y lo dice en el docstring. Es el patrón que ya usa el resto de la
  cadena — `0113`, `0115` (¡sobre esta misma tabla, y se ejecuta ANTES!) y
  `0137` borran filas en su bajada. La 0033 era la excepción.
- **El test**: siembra ÉL la fila SAML antes del ida y vuelta. Así prueba la
  propiedad de verdad —reversible **con datos**— y deja de depender de que otro
  fichero contamine la sesión.

## La regla que se saca de aquí

Un `downgrade` no se valida contra una tabla vacía: se valida contra los datos
que el `upgrade` hace posibles. Si una migración RELAJA una restricción, su
bajada tiene que decidir explícitamente qué hace con las filas que sólo existen
gracias a esa relajación — borrarlas o abortar con un mensaje que nombre la
tabla, el motivo y el remedio. «No puede haber ninguna» es una suposición con
fecha de caducidad.
