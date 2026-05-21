---
adr: "0007"
title: Seeds idempotentes via UUIDv5 + ON CONFLICT
status: accepted
date: 2026-05-21
deciders: System Architect
phase: 01-dominio-minimo
---

# ADR 0007 — Estrategia de seeds idempotentes

## Contexto

El Plan 01 introduce **6 catálogos built-in** que necesitan estar
poblados al arrancar una instalación nueva: 11 agentes, 33 skills,
18 tools, 5 teams, 8 plantillas de proyecto, 4 políticas humanas.

Tres requisitos chocan:

1. **Re-correr el seeder no debe duplicar filas.** Una instalación
   madura puede aplicarlo decenas de veces (bootstrap, CI, después
   de un wipe parcial).
2. **Los IDs deben ser estables entre instalaciones.** El panel
   admin enlaza el built-in "Backend Dev" entre tenants y entre
   máquinas — si su UUID cambia cada `seed`, las referencias
   externas (tests, runbooks, capturas) envejecen.
3. **Cero migraciones para añadir un built-in nuevo.** Añadir un
   skill o un agente no debería requerir una migración Alembic; es
   datos, no esquema.

Lo más simple sería `INSERT … ON CONFLICT DO NOTHING` con UUID
aleatorio. Cumple (1) y (3) pero rompe (2): cada instalación tiene
IDs distintos para los mismos built-ins.

## Decisión

Cada fila built-in deriva su PK de un `uuid5(NAMESPACE, slug)`:

```python
from uuid import UUID, uuid5

AGENT_SEED_NAMESPACE = UUID("00000000-0000-0000-0000-000000000011")

def _agent_id(slug: str) -> UUID:
    return uuid5(AGENT_SEED_NAMESPACE, f"agent:{slug}")
```

El seeder hace **UPSERT idempotente** vía `ON CONFLICT (id) DO
UPDATE`:

```sql
INSERT INTO approval_policy_templates (id, tenant_id, name, ..., is_builtin)
VALUES (:id, :tenant_id, :name, ..., true)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    categories = EXCLUDED.categories,
    updated_at = now()
```

Reglas:

- `tenant_id` = `00000000-0000-0000-0000-000000000001` (tenant
  plataforma).
- `is_builtin = true` activa la policy RLS `<tabla>_builtin_read`
  que lo expone como lectura a todos los tenants.
- El `slug` por categoría se concatena con un prefijo
  (`agent:`, `skill:`, `tool:`, etc.) en el namespace para que dos
  catálogos con el mismo slug no colisionen.

El seeder se invoca con `python -m api_server.seeds`. Para CI lo
ejecuta `scripts/dev/run-e2e.ps1` después de aplicar migraciones.

## Alternativas descartadas

1. **Fixtures Alembic.** Cada seed sería una migración. Rechazado:
   añadir un agente nuevo requiere una migración (rompe req 3) y
   las "data migrations" reversibles son frágiles.
2. **CSV/JSON files en disco + loader genérico.** Más declarativo
   pero el seed necesita lógica (multi-idioma, FK a otros
   built-ins). Mantener Python a mano es más explícito.
3. **`INSERT … ON CONFLICT DO NOTHING`.** Rompe el caso "edité el
   prompt de un built-in y quiero re-seedeear para propagarlo":
   sólo `DO UPDATE` reaplica cambios.
4. **UUIDs aleatorios + tabla `seed_marker`.** Cumple (1) pero
   rompe (2) y añade una tabla extra que mantener.

## Consecuencias

Positivas:

- Tests y documentación pueden citar UUIDs de built-ins por
  literal: el `Backend Dev` siempre tiene el mismo `id` en todas
  las instalaciones.
- `re-seed` propaga ediciones del catálogo plataforma a las
  instalaciones desplegadas sin requerir migraciones.
- La idempotencia hace el seeder seguro en CI y en bootstrap
  scripts.

Negativas / cuidados:

- **Renombrar un slug = nuevo UUID.** Si el slug `backend-dev`
  pasa a `backend-developer`, el UPSERT inserta una fila nueva
  _además_ de dejar la vieja huérfana. Una migración manual debe
  retirar la antigua. Documentado en `gotchas/`.
- **`updated_at` se mueve cada seed.** `created_at` permanece
  estable, lo que permite ordenar built-ins por orden de inserción
  (ver router `/approval-policies`). El UPSERT no toca `created_at`
  porque sólo aparece en la lista de columnas del INSERT.
- **Tenant plataforma reservado.** Nadie puede borrar el tenant
  plataforma porque dispararía un cascade-delete de todos los
  built-ins. Garantía protegida por RLS + ausencia de UI para
  manipular ese tenant.

## Referencias

- Documento maestro, sección 13 (catálogos plataforma).
- Implementación: `apps/api-server/src/api_server/seeds/__init__.py`
  - un fichero `builtin_*.py` por catálogo.
- Tests: `tests/integration/test_seed_*.py` (uno por catálogo).
- Migraciones que activan `_builtin_read`: 0005, 0006, 0008.
