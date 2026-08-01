"""La referencia de sesiones y el runbook de lockout dicen lo que dice el código.

`task_prod09_18` pide dos documentos, y su gate declarado es revisión humana.
Una revisión humana caduca: el día que alguien renombre una cookie o cambie el
mensaje de un 403, la documentación pasa a mentir sin que nada avise. Estas
guardas son la mitad automatizable — y son de **descubrimiento**, no de
subcadena fija: los nombres de cookie, los knobs de endurecimiento y los
mensajes de rechazo se leen del código, así que añadir un control nuevo sin
documentarlo las pone en rojo.

Cada guarda afirma además que **encontró algo**: un descubrimiento que deje de
encontrar nada pasaría vacío y envejecería en silencio (trampa nº4 de
`docs/03-guides/verificar-antes-de-implementar.md`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REFERENCE = _ROOT / "docs" / "04-reference" / "sesiones.md"
_RUNBOOK = _ROOT / "docs" / "06-runbooks" / "recuperacion-lockout-admin.md"
_HARDENING_SRC = (
    _ROOT / "apps" / "api-server" / "src" / "api_server" / "auth" / "admin_hardening.py"
)


@pytest.fixture(scope="module")
def reference() -> str:
    return _REFERENCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runbook() -> str:
    return _RUNBOOK.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Los dos documentos existen y están indexados
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("doc", "index"),
    [
        (_REFERENCE, _ROOT / "docs" / "04-reference" / "README.md"),
        (_RUNBOOK, _ROOT / "docs" / "06-runbooks" / "README.md"),
    ],
)
def test_the_document_exists_and_its_folder_index_links_it(doc: Path, index: Path) -> None:
    """Un documento que no está en el índice de su carpeta no lo encuentra nadie."""
    assert doc.is_file(), f"falta {doc.relative_to(_ROOT)}"
    assert f"./{doc.name}" in index.read_text(
        encoding="utf-8"
    ), f"{doc.name} no está enlazado desde {index.relative_to(_ROOT)}"


# ---------------------------------------------------------------------------
# Los nombres de cookie/cabecera salen del código, no de la memoria del autor
# ---------------------------------------------------------------------------
def test_the_reference_names_the_cookies_the_code_actually_sets(reference: str) -> None:
    from api_server.auth import cookies

    names = [
        cookies.SESSION_COOKIE_NAME,
        cookies.CSRF_COOKIE_NAME,
        cookies.CSRF_HEADER_NAME,
    ]
    assert len(names) == 3 and all(names), "el módulo de cookies dejó de exponer sus nombres"

    missing = [n for n in names if n not in reference]
    assert not missing, f"la referencia no nombra: {missing} (¿se renombró una cookie?)"


def test_the_reference_documents_every_admin_hardening_knob(reference: str) -> None:
    """Descubrimiento: los knobs se leen del MÓDULO del gate, no de una lista.

    La fuente es `admin_hardening.py`: todo `settings.<campo>` que el gate
    consulta es, por definición, un knob que cambia quién entra en `/admin/*`.
    Un cuarto control añadido y no documentado pone esto en rojo, que es justo
    lo que queremos que pase. (Derivarlo de `Settings.model_fields` por prefijo
    no vale: `admin_database_url` empieza igual y no es un knob de acceso.)
    """
    from api_server.config import Settings

    src = _HARDENING_SRC.read_text(encoding="utf-8")
    knobs = sorted(
        {
            name
            for name in re.findall(r"settings\.([a-z_]+)", src)
            if name in Settings.model_fields and name.startswith("admin_")
        }
    )
    assert len(knobs) >= 3, f"la guarda dejó de encontrar los knobs de admin (vio {knobs})"

    missing = [k for k in knobs if f"API_SERVER_{k.upper()}" not in reference]
    assert not missing, f"knobs de /admin/* sin documentar en la referencia: {missing}"


def test_the_reference_documents_the_register_and_skew_knobs(reference: str) -> None:
    """Los ajustes que introdujeron `task_prod09_05` y `task_prod09_16`."""
    from api_server.config import Settings

    knobs = sorted(
        f
        for f in Settings.model_fields
        if f.startswith("register_rate_limit") or f == "incoming_webhook_max_skew_seconds"
    )
    assert len(knobs) >= 3, f"la guarda dejó de encontrar los knobs nuevos (vio {knobs})"

    missing = [k for k in knobs if f"API_SERVER_{k.upper()}" not in reference]
    assert not missing, f"ajustes nuevos sin documentar: {missing}"


def test_the_reference_documents_the_two_separate_secrets(reference: str) -> None:
    """El punto entero de secrets-9: que sean DOS y que se vean como dos."""
    for var in ("API_SERVER_JWT_SECRET", "API_SERVER_INTERNAL_TOKEN_SECRET"):
        assert var in reference, f"{var} no aparece en la referencia de sesiones"


def test_the_reference_documents_the_derived_dedup_prefix(reference: str) -> None:
    """Si cambia el prefijo de la clave derivada, un operador que busque
    `body-sha256:` en la tabla no encontrará nada y la doc estará mintiendo."""
    from api_server.webhooks import signatures

    prefix = signatures._DERIVED_DELIVERY_PREFIX
    assert prefix, "el módulo de firmas dejó de exponer el prefijo derivado"
    assert prefix in reference, f"la referencia no menciona el prefijo de dedup {prefix!r}"


# ---------------------------------------------------------------------------
# El runbook reconoce los rechazos REALES del gate
# ---------------------------------------------------------------------------
def test_the_runbook_quotes_every_rejection_message_the_gate_raises(runbook: str) -> None:
    """Descubrimiento sobre el fuente del gate: cada `detail=` que levanta
    `require_hardened_system_admin` tiene que estar en la tabla de síntomas.

    Es lo primero que mira alguien que está fuera: pega el mensaje que ve. Un
    control nuevo con un mensaje nuevo, o un mensaje reescrito, deja el runbook
    inservible en silencio — salvo por esta guarda.
    """
    src = _HARDENING_SRC.read_text(encoding="utf-8")
    body = src[src.index("async def require_hardened_system_admin") :]
    details = re.findall(r'detail="([^"]+)"', body)

    assert len(details) >= 3, f"la guarda dejó de encontrar los rechazos del gate: {details}"

    missing = [d for d in details if d not in runbook]
    assert not missing, f"el runbook no reconoce estos rechazos: {missing}"


def test_the_runbook_warns_against_the_tempting_shortcut(runbook: str) -> None:
    """Poner `environment=dev` desarma los tres controles de golpe... y también
    el guard fail-closed de secretos. El runbook tiene que decirlo: es la
    primera idea que se le ocurre a cualquiera con prisa."""
    assert "API_SERVER_ENVIRONMENT=dev" in runbook
    assert "fail-closed" in runbook


def test_the_two_documents_point_at_each_other(reference: str, runbook: str) -> None:
    """Ninguno sirve solo: el que configura la allowlist lee la referencia, y
    el que se queda fuera llega por el runbook."""
    assert "recuperacion-lockout-admin.md" in reference
    assert "sesiones.md" in runbook
