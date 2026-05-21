---
title: `ALTER DEFAULT PRIVILEGES` no se hereda a bases de datos creadas después
area: postgres
encountered: 2026-05-20
stack: postgres 16
---

## Síntoma

`app_user` recibe `permission denied for table X` en una BD que
acabas de crear (por ejemplo, una BD de tests). En la BD dev funciona.

## Causa raíz

`ALTER DEFAULT PRIVILEGES` es **per-database**. Lo configuras una vez
sobre la BD `agentic_platform`, pero al hacer
`CREATE DATABASE agentic_platform_test OWNER migrations_user`, la
nueva BD no hereda esos default privileges.

Además, los ALTER DEFAULT PRIVILEGES **solo aplican a objetos
creados _después_** de su definición. Si la migración corrió antes
de configurar los defaults, las tablas existentes tampoco quedan
cubiertas.

## Fix

Dos pasos en la nueva BD:

1. Repetir `ALTER DEFAULT PRIVILEGES` justo después de crearla:

   ```sql
   ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
   ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
     GRANT USAGE, SELECT ON SEQUENCES TO app_user;
   GRANT USAGE ON SCHEMA public TO app_user;
   ```

2. Tras correr la migración, **retro-grant** a las tablas ya
   creadas:

   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
   ```

`tests/integration/conftest.py` hace exactamente esto al construir
la BD de pruebas.

## Cómo verificar el fix

```sql
\dp public.organizations
```

`app_user` debe aparecer con `arwd` (SELECT/INSERT/UPDATE/DELETE).
