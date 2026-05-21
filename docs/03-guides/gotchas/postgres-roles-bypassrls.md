---
title: `migrations_user` necesita `BYPASSRLS`; `app_user` debe NO tenerlo
area: postgres
encountered: 2026-05-20
stack: postgres 16
---

## Síntoma

- Alembic falla aplicando una migración con `permission denied for ...`
  o `new row violates row-level security policy ...`.
- O al revés: un endpoint normal (NOBYPASSRLS) que **debería** ver
  cero filas cross-tenant las ve todas.

## Causa raíz

Las policies RLS también gobiernan al rol que las CREA. Si
`migrations_user` no tiene `BYPASSRLS`:

1. El `CREATE POLICY` cubre INSERT/SELECT/etc. inmediatamente.
2. Los seed data que la migración carga después fallan porque el rol
   queda restringido por la policy recién creada.

A la inversa, `app_user` debe respetar RLS estrictamente — un fallo
de tenant_id en una query debe surfacear como "cero filas", no como
"todas las filas".

## Fix

En `docker/postgres/init/02-roles.sh`:

```sql
CREATE ROLE migrations_user WITH LOGIN BYPASSRLS PASSWORD '...';
ALTER ROLE migrations_user WITH LOGIN BYPASSRLS;

CREATE ROLE app_user WITH LOGIN NOBYPASSRLS PASSWORD '...';
ALTER ROLE app_user WITH LOGIN NOBYPASSRLS;
```

Y mantén siempre el flag explícito (incluso en los `ALTER`) para que
nadie lo "olvide" en un futuro init.

## Cómo verificar el fix

```sql
SELECT rolname, rolbypassrls FROM pg_roles
 WHERE rolname IN ('migrations_user','app_user');
```

```
   rolname        | rolbypassrls
-------------------+--------------
 migrations_user  | t
 app_user         | f
```

Si `app_user` aparece con `t`, el test de aislamiento cross-tenant
pasaría falsamente.
