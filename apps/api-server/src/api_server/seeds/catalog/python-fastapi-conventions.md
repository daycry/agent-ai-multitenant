# Convenciones de stack: Python + FastAPI

Guía práctica para servicios HTTP en Python con FastAPI, SQLAlchemy 2.x async
y pytest. Pensada como referencia para agentes que generan o revisan código de
backend.

## Layout del repositorio

Estructura un servicio por capas, no por tipo de fichero:

```
src/<package>/
  api/          # routers FastAPI, dependencias, schemas de request/response
  domain/       # modelos de dominio + lógica de negocio pura (sin I/O)
  db/           # modelos SQLAlchemy, repositorios, sesión
  services/     # casos de uso que orquestan dominio + db + externos
  config.py     # settings (pydantic-settings), una sola fuente de verdad
  main.py       # creación de la app, montaje de routers, lifespan
tests/
  unit/         # dominio puro, sin red ni DB
  integration/  # DB real / app real con dependencias falsas donde aplique
```

Regla: el dominio no importa FastAPI ni SQLAlchemy. Los routers no contienen
lógica de negocio; delegan en services.

## Configuración

Usa `pydantic-settings` con una clase `Settings` única, instanciada una vez:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env")

    database_url: str
    redis_url: str
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

No leas variables de entorno dispersas por el código. No hardcodees valores
mágicos: declara constantes con nombre o campos de settings.

## async / await

- Define endpoints y dependencias I/O-bound como `async def`.
- Nunca bloquees el event loop: para CPU-bound o librerías síncronas usa
  `await asyncio.to_thread(fn, ...)` o un executor.
- No mezcles drivers síncronos (psycopg2) en rutas async; usa `asyncpg` vía
  el engine async de SQLAlchemy.
- Una operación que no hace I/O no necesita ser `async`.

## Routers y dependencias

Agrupa endpoints relacionados en un `APIRouter` con prefijo y tags:

```python
router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID,
    svc: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    project = await svc.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project
```

Inyecta sesiones de DB, settings y servicios con `Depends`. Esto hace los
tests triviales: sobreescribe la dependencia en lugar de parchear globals.

## Schemas Pydantic

- Separa schemas de entrada (`ProjectCreate`) de salida (`ProjectOut`). Nunca
  expongas el modelo ORM directamente.
- Activa `model_config = ConfigDict(from_attributes=True)` para mapear desde
  filas ORM.
- Valida en el borde: el schema es la frontera de confianza. El dominio asume
  datos ya validados.

## SQLAlchemy 2.x async

Usa el estilo declarativo 2.0 con `Mapped[...]` y `mapped_column`:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase): ...


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str]
```

- Crea el engine con `create_async_engine` y entrega sesiones con
  `async_sessionmaker(expire_on_commit=False)`.
- Una sesión por request, abierta y cerrada por una dependencia FastAPI.
- Usa `select(...)` + `await session.execute(stmt)`; evita la API legacy
  `session.query(...)`.
- Carga relaciones explícitamente con `selectinload`/`joinedload` para no caer
  en N+1.

### Multi-tenancy

Cada query filtra por `tenant_id`. Nunca emitas un `select`/`update`/`delete`
sin acotar el tenant; si la app es multi-tenant, inyecta el tenant en una
dependencia y pásalo al repositorio. No confíes sólo en RLS: defensa en
profundidad.

## Manejo de errores

- Lanza `HTTPException` sólo en la capa de API. El dominio lanza excepciones
  de dominio propias (`ProjectNotFound`), que un handler traduce a HTTP.
- Registra un `exception_handler` por tipo de error de dominio para mapear a
  códigos coherentes (404, 409, 422...).
- No devuelvas trazas ni mensajes internos al cliente; loguéalos y responde un
  mensaje neutro con un id de correlación.

```python
@app.exception_handler(ProjectNotFound)
async def handle_not_found(request: Request, exc: ProjectNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})
```

## OpenAPI

- Anota `response_model`, `status_code` y `responses` para que el esquema
  generado sea fiel.
- Documenta los códigos de error en `responses={404: {...}}`.
- Versiona la API por prefijo de ruta (`/v1`) cuando rompas compatibilidad.

## Testing con pytest + httpx

- Usa `httpx.AsyncClient` con `ASGITransport` para probar la app sin levantar
  un servidor real:

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_get_project(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/projects/123")
    assert resp.status_code == 404
```

- Sobreescribe dependencias con `app.dependency_overrides` para inyectar fakes
  (DB en memoria, servicios stub) en lugar de parchear.
- Tests unitarios del dominio: sin red, sin DB, deterministas.
- Tests de integración: marca y sepáralos; usa una DB real efímera.
- Apunta a >70% de cobertura en el dominio crítico.

## Tooling

- `ruff` para lint + format, `mypy --strict` para tipos. Type hints
  obligatorios en firmas públicas.
- Pre-commit con ruff + mypy. CI falla si no pasan.
- Pin de dependencias con un lockfile; no instales en runtime.
