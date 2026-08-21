---
title: "El `.venv` local va por detrás de `constraints.txt`, y eso esconde rojos que CI sí verá"
status: published
created: 2026-08-01
docs_language: es
---

# La suite pasa en local y fallará en CI: el venv no es lo que CI instala

## Síntoma

`tests/unit/` sale **verde** con `.venv/Scripts/python.exe` y, sin haber tocado
nada, dos guardas fallan cuando alguien las corre en otro sitio:

```
FAILED tests/unit/test_security_headers_middleware.py::test_the_public_api_v1_contract_stays_published_in_prod
E   AssertionError: the public v1 OpenAPI document disappeared; /api/v1 paths: {'', '/me', '/me/memberships', '/metrics'}
FAILED tests/unit/test_metrics_endpoint_wired.py::test_metrics_does_not_shadow_the_authenticated_inbox_metrics
```

El mensaje induce a error a propósito: dice que **desapareció** el contrato
público de la API. Invita a buscar quién borró un `include_router`, y no hay tal.

## Causa raíz

Desde `prod-11` (ADR 0147) **CI no instala desde los rangos de los
`pyproject.toml`: instala desde el lock** —
`pip install -e "apps/api-server[dev]" -c constraints.txt`. El `.venv` del repo,
en cambio, se creó antes y sigue resuelto por rangos. Son dos entornos
distintos, y el lock va **por delante**:

| Paquete   | `.venv` del repo | `constraints.txt` (lo que instala CI) |
| --------- | ---------------- | ------------------------------------- |
| fastapi   | 0.136.1          | **0.141.1**                           |
| starlette | 1.0.0            | **1.3.1**                             |

FastAPI 0.141 cambió cómo se exponen las rutas: las que entran por
`include_router()` ya **no se aplanan** en `APIRoute` dentro de `app.routes`,
sino que aparecen envueltas en objetos `_IncludedRouter` sin atributo `.path`.
Cualquier test que haga

```python
paths = {str(getattr(r, "path", "")) for r in app.routes}
```

pasa de ver ~300 rutas a ver cuatro. **La aplicación está intacta** —las rutas se
sirven igual—; lo que cambió es la introspección. Por eso el fallo se disfraza de
regresión de producto y no lo es.

Medido el 2026-08-01: de ~170 paquetes comparables entre el `.venv` y el lock,
**74 divergen y 72 son el venv retrasado**. `fastapi` era uno de esos 72, y una
inspección previa lo había dado por «riesgo bajo» sin ejecutarlo. No lo era.

## Cómo reconocerlo

Antes de buscar al culpable en el código, compara las dos resoluciones:

```bash
# Qué tienes tú
ls -d .venv/Lib/site-packages/fastapi-*.dist-info
# Qué instala CI
grep -E '^(fastapi|starlette)==' constraints.txt
```

Si no coinciden, el rojo (o el verde) puede ser del entorno, no del cambio.

## Fix

**El de raíz** es no tener dos resoluciones. Para reproducir lo que CI ejecuta,
crea un venv aparte instalado desde el lock — no toques el `.venv` del repo, que
lo usa todo lo demás:

```bash
uv venv --seed --python 3.12 /tmp/lockvenv
# los MISMOS 12 editable installs que el job `test-unit` de ci.yml, todos con -c
/tmp/lockvenv/Scripts/python.exe -m pip install -e "apps/api-server[dev]" -c constraints.txt
# … y los once restantes
/tmp/lockvenv/Scripts/python.exe -m pytest tests/unit/ -q -p no:randomly
```

**El del síntoma concreto**: un test que necesite el inventario de rutas no debe
leer `app.routes` plano. Usa `app.openapi()["paths"]`, o recorre recursivamente
entrando en `route.routes` cuando el objeto lo tenga.

## Por qué importa más de lo que parece

Con CI caído por facturación, **nadie estaba corriendo la resolución del lock**.
El día que CI vuelva, estos dos tests salen rojos en `master` y parecerán rotos
por el último commit que pase por allí, que no tendrá nada que ver. Un lock que
nadie ejecuta no da reproducibilidad: da una segunda realidad sin vigilar.

## Relacionado

- [pytest necesita el venv del repo](./pytest-needs-the-repo-venv.md) — la otra
  mitad: usar el Python global mata la recolección entera.
- [ADR 0147 — lockfile Python: uv workspace](../../05-architecture-decisions/0147-lockfile-python-uv-vs-pip-tools.md).
- [Referencia: cadena de suministro](../../04-reference/cadena-suministro.md) §2.
- [Plan prod-11](../../roadmap/prod-11-cadena-suministro.md), `task_ci_lock_10`
  (`auto_prod11_10_b`).
