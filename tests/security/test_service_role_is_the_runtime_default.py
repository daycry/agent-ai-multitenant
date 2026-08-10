"""Los servicios de runtime conectan como `service_user`, no como el dueño del esquema.

prod-14 `task_prod14_05` (hallazgo `tenancy-2`). Hasta que se cambiaron, los
cuatro servicios que escriben en la base de datos —workers, orchestrator,
notification-dispatcher y el engine administrativo del api-server— conectaban
como `migrations_user`: el **propietario del esquema, con `GRANT ALL`**. Un
servicio comprometido podía ejecutar `ALTER TABLE … DISABLE ROW LEVEL SECURITY`
y desactivar el aislamiento multi-tenant de toda la plataforma — el principio
rector nº 1 — sin tocar ninguna fila.

`service_user` es BYPASSRLS **sin DDL**: sigue leyendo cross-tenant (su razón de
ser: un worker atiende a todos los tenants) pero no puede tocar el esquema ni las
policies.

**Por qué esta guarda existe y no basta con `test_db_roles_service_user.py`**:
aquél comprueba los privilegios del rol *dentro de PostgreSQL* y pasa igual de
verde si mañana alguien revierte un `config.py` a `migrations_user`. Lo que se
degrada entonces no es la base de datos, es **quién se conecta a ella**, y eso
solo se ve en el default del settings. Es la misma clase de costura por la que se
escribió `test_service_user_password_is_wired.py`: el contrato roto vive entre dos
ficheros, así que ningún test de uno de los dos lo ve.

El comando declarado en el plano (`auto_prod14_05_a`) apuntaba a
`tests/integration/test_execution_persistence.py`, **un fichero que no existe**:
tal cual, fallaba en la recolección — el peor rojo posible, porque no distingue
«la feature está rota» de «el arnés apuntaba a la nada». Este fichero es su
sustituto y el plan quedó corregido.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

#: `(fichero de settings, nombre del campo)` de cada DSN de RUNTIME.
RUNTIME_DSN_FIELDS: tuple[tuple[Path, str], ...] = (
    (REPO_ROOT / "apps" / "workers" / "src" / "workers" / "config.py", "database_url"),
    (
        REPO_ROOT / "apps" / "orchestrator" / "src" / "orchestrator" / "config.py",
        "database_url",
    ),
    (
        REPO_ROOT
        / "apps"
        / "notification-dispatcher"
        / "src"
        / "notification_dispatcher"
        / "config.py",
        "database_url",
    ),
    (
        REPO_ROOT / "apps" / "api-server" / "src" / "api_server" / "config.py",
        "admin_database_url",
    ),
)

#: Campos que SÍ pueden nombrar `migrations_user`, con el motivo que los salva.
#: No es una lista de perdón: cada entrada describe una operación que **necesita
#: al dueño del esquema**, y el test exige que la descripción del campo lo diga.
DDL_GRADE_FIELDS: dict[str, tuple[str, ...]] = {
    # `pg_dump` de la copia completa: sin rol admin, el volcado sale incompleto.
    "backup_database_url": ("pg_dump", "BYPASSRLS", "admin"),
    # `pg_restore --clean` deja el ownership en el rol que conecta.
    "restore_required_db_role": ("pg_restore", "DDL", "dueño", "owner"),
}


def _field_nodes(path: Path) -> dict[str, ast.AnnAssign | ast.Assign]:
    """Campos declarados en cualquier clase de settings del módulo."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fields: dict[str, ast.AnnAssign | ast.Assign] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields[stmt.target.id] = stmt
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        fields[target.id] = stmt
    return fields


def _default_of(node: ast.AnnAssign | ast.Assign) -> str | None:
    """El `default=` del `Field(...)`, ya concatenado por el parser."""
    value = node.value
    if not isinstance(value, ast.Call):
        return None
    for keyword in value.keywords:
        if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
            constant = keyword.value.value
            return constant if isinstance(constant, str) else None
    return None


def test_the_four_runtime_dsn_fields_still_exist() -> None:
    """No-vacuidad: si un campo se renombra, el resto de esta suite pasaría sola."""
    missing = [
        f"{path.name}:{field}"
        for path, field in RUNTIME_DSN_FIELDS
        if field not in _field_nodes(path)
    ]
    assert not missing, (
        f"campos de DSN de runtime que ya no existen: {missing}. Si se han "
        "renombrado, actualiza RUNTIME_DSN_FIELDS — no borres la guarda."
    )


@pytest.mark.parametrize(
    ("path", "field"),
    RUNTIME_DSN_FIELDS,
    ids=[f"{p.parent.name}.{f}" for p, f in RUNTIME_DSN_FIELDS],
)
def test_runtime_services_default_to_service_user(path: Path, field: str) -> None:
    default = _default_of(_field_nodes(path)[field])
    assert default, f"{path.name}:{field} no declara un `default=` literal"

    assert "://service_user:" in default, (
        f"{path.name}:{field} conecta como un rol distinto de `service_user` "
        f"({default.split('@')[0]}…). Si es `migrations_user`, este servicio "
        "vuelve a ser dueño del esquema: un compromiso suyo puede hacer "
        "`ALTER TABLE … DISABLE ROW LEVEL SECURITY` y tumbar el aislamiento "
        "multi-tenant entero."
    )
    assert "migrations_user" not in default


@pytest.mark.parametrize(
    "path",
    sorted({p for p, _ in RUNTIME_DSN_FIELDS}),
    ids=lambda p: p.parent.name,
)
def test_any_remaining_migrations_user_is_ddl_grade_and_says_why(path: Path) -> None:
    """`migrations_user` no está prohibido: está acotado a lo que necesita DDL.

    Prohibirlo del todo sería mentira —`pg_dump` y `pg_restore` necesitan al
    dueño del esquema— y empujaría a alguien a esconderlo. Lo que se exige es que
    cada superviviente esté en la lista y **explique en su descripción por qué**,
    para que la próxima revisión no tenga que reconstruir el razonamiento.
    """
    source = path.read_text(encoding="utf-8")
    offenders: list[str] = []

    for name, node in _field_nodes(path).items():
        # El VALOR, no la prosa: las descripciones nombran `migrations_user` a
        # propósito para contar de dónde se viene, y eso es documentación útil,
        # no una conexión. Lo que importa es con quién se conecta de verdad.
        default = _default_of(node)
        if not default or "migrations_user" not in default:
            continue
        segment = ast.get_source_segment(source, node) or ""
        if name not in DDL_GRADE_FIELDS:
            offenders.append(name)
            continue
        reasons = DDL_GRADE_FIELDS[name]
        assert any(word.lower() in segment.lower() for word in reasons), (
            f"{path.name}:{name} usa `migrations_user` y su descripción no "
            f"explica por qué necesita al dueño del esquema (se esperaba alguna "
            f"de {reasons})"
        )

    assert not offenders, (
        f"{path.name} declara `migrations_user` en campos no autorizados: "
        f"{offenders}. Los servicios de runtime van con `service_user`; solo "
        f"las operaciones que necesitan DDL ({sorted(DDL_GRADE_FIELDS)}) pueden "
        "conectar como el dueño del esquema."
    )


def test_alembic_is_still_the_one_that_speaks_as_the_schema_owner() -> None:
    """El contrapunto: `migrations_user` no debe desaparecer del repo.

    Si nadie lo nombra ya, o las migraciones dejaron de correr con el dueño del
    esquema (y fallarán al primer `ALTER TABLE`), o alguien "limpió" el rol
    creyendo que era un resto. Las dos lecturas piden mirar antes de seguir.
    """
    env_py = REPO_ROOT / "apps" / "api-server" / "migrations" / "env.py"
    assert env_py.is_file(), "el env.py de Alembic cambió de sitio"
    assert "migrations_user" in env_py.read_text(encoding="utf-8"), (
        "el env.py de Alembic ya no documenta `migrations_user`: las migraciones "
        "deben seguir conectando como el dueño del esquema"
    )
