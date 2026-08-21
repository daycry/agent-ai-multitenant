"""Guardia anti-drift de la matriz RBAC documentada.

Plan prod-15 `task_gov_rbac_matrix_08`. Hallazgo docsroadmap-5: `rbac.md` se
declara "**el contrato** entre el código de los endpoints y los tests
integration cross-rol", y aun así se le habían escapado routers enteros de la
superficie System Admin (`/admin/platform-settings`, `/admin/ollama` y — no lo
sabía ni la auditoría — `/admin/embeddings`).

**Alcance deliberado: la superficie `/admin/*`.** Es donde un endpoint sin
documentar duele más (gate de plataforma, engine BYPASSRLS) y donde el drift es
detectable de forma estática y barata. El test es **estático (AST)** a propósito:
importar `create_app()` tarda minutos y arrastra dependencias de BD, lo que
convertiría esta guardia en algo que nadie corre.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ROUTERS = _ROOT / "apps" / "api-server" / "src" / "api_server" / "routers"
_RBAC_MD = _ROOT / "docs" / "04-reference" / "rbac.md"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _admin_prefixes(tree: ast.Module) -> dict[str, str]:
    """`{nombre_de_variable: prefijo}` de los `X = APIRouter(prefix="/admin/…")`."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "APIRouter":
            continue
        for kw in node.value.keywords:
            if kw.arg != "prefix" or not isinstance(kw.value, ast.Constant):
                continue
            value = kw.value.value
            if not (isinstance(value, str) and value.startswith("/admin/")):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = value
    return prefixes


def _decorated_routes(tree: ast.Module, prefixes: dict[str, str]) -> list[tuple[str, str]]:
    """`[(prefijo, "MÉTODO /ruta/completa")]` de los `@X.<método>("…")`."""
    routes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                continue
            if deco.func.attr not in _HTTP_METHODS:
                continue
            owner = deco.func.value
            if not isinstance(owner, ast.Name) or owner.id not in prefixes:
                continue
            suffix = ""
            if deco.args and isinstance(deco.args[0], ast.Constant):
                suffix = str(deco.args[0].value)
            prefix = prefixes[owner.id]
            routes.append((prefix, f"{deco.func.attr.upper()} {prefix}{suffix}"))
    return routes


def _admin_routers() -> dict[str, list[str]]:
    """`{prefijo /admin/...: [métodos+rutas]}` descubierto por AST."""
    found: dict[str, list[str]] = {}
    for path in sorted(_ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        prefixes = _admin_prefixes(tree)
        if not prefixes:
            continue
        for prefix, entry in _decorated_routes(tree, prefixes):
            found.setdefault(prefix, []).append(entry)
    return found


def test_discovery_finds_the_admin_routers() -> None:
    """Sin esto, todo lo de abajo pasaría vacío (guarda §4)."""
    routers = _admin_routers()
    assert len(routers) >= 6, (
        f"el descubrimiento AST de routers /admin/* falló (vio {sorted(routers)}): "
        "cambió la forma de declarar los routers y esta guardia dejó de guardar"
    )
    total = sum(len(v) for v in routers.values())
    assert total >= 20, f"se descubrieron routers pero casi ninguna ruta ({total})"


def test_every_admin_prefix_is_in_the_rbac_matrix() -> None:
    """Todo prefijo `/admin/*` del código aparece en `rbac.md`.

    Es la guardia que evita el siguiente docsroadmap-5: si alguien añade un
    router de plataforma y no lo documenta, este test lo dice antes del merge.
    """
    matrix = _RBAC_MD.read_text(encoding="utf-8")
    routers = _admin_routers()
    assert routers, "descubrimiento vacío"

    undocumented = sorted(prefix for prefix in routers if prefix not in matrix)
    assert not undocumented, (
        "prefijos /admin/* que existen en el código y NO están en "
        f"docs/04-reference/rbac.md: {undocumented}. "
        "Añade su sección a la matriz (es el contrato, no decoración)."
    )


def _normalize(path: str) -> str:
    """`/admin/x/{provider_id}` → `/admin/x/{}`.

    La matriz escribe `{id}` donde el código escribe `{provider_id}`: eso es
    cosmética, no drift. Un test que falle por el nombre del parámetro sería
    ruido y acabaría desactivado.
    """
    return re.sub(r"\{[^}]*\}", "{}", path)


def _matrix_rows() -> str:
    """SOLO las filas de tabla de `rbac.md`, normalizadas.

    Crítico: la prosa del documento dice cosas como "los endpoints `/admin/*`
    corren sobre BYPASSRLS". Si se buscara el comodín en TODO el texto, ese
    `/admin/*` cubriría cualquier ruta y este test pasaría **vacío** — la
    trampa §4 de `verificar-antes-de-implementar.md`. Un endpoint solo está
    documentado si tiene **fila** en la matriz.
    """
    lines = _RBAC_MD.read_text(encoding="utf-8").splitlines()
    rows = [line for line in lines if line.lstrip().startswith("|")]
    assert len(rows) >= 150, f"el filtro de filas de tabla falló (vio {len(rows)})"
    return _normalize("\n".join(rows))


def _covered_by_wildcard(path: str, rows: str) -> bool:
    """¿Cubre la matriz `path` con una FILA comodín de algún ancestro?

    La matriz agrupa familias enteras (`/admin/backup/restore/*` cubre
    `/admin/backup/restore/jobs/{}`). El `\\*` es el asterisco escapado que usa
    Markdown en las celdas.
    """
    parts = path.strip("/").split("/")
    for cut in range(1, len(parts)):
        ancestor = "/" + "/".join(parts[:cut])
        if f"{ancestor}/*" in rows or f"{ancestor}/\\*" in rows:
            return True
    return False


def test_every_admin_route_is_in_the_rbac_matrix() -> None:
    """Cada ruta concreta `/admin/*` aparece en la matriz.

    Más fino que el prefijo: documentar `/admin/ollama` y olvidar
    `/admin/ollama/models/pull` deja el endpoint mutador sin contrato — que es
    exactamente lo que pasaba con `/admin/model-prices/sync/{apply,diff,audit}`,
    documentado solo como `/admin/model-prices/sync`.

    Compara **rutas, no métodos**: el método vive en una columna de la tabla y
    acoplarse a su formato haría la guardia frágil sin ganar cobertura real.
    """
    rows = _matrix_rows()
    routers = _admin_routers()

    missing: set[str] = set()
    checked = 0
    for routes in routers.values():
        for entry in routes:
            checked += 1
            path = _normalize(entry.split(" ", 1)[1])
            if path in rows or _covered_by_wildcard(path, rows):
                continue
            missing.add(path)
    assert checked >= 20, f"casi ninguna ruta quedó cubierta por el test ({checked})"
    assert not missing, f"rutas /admin/* sin fila en docs/04-reference/rbac.md: {sorted(missing)}"
