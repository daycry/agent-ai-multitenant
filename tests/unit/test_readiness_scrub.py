"""El saneado del detalle de `/readyz` (`task_audit14_08`).

Este fichero existe por una lección aprendida a la mala. La aserción end-to-end
de `tests/integration/test_health_readiness.py::test_readyz_503_body_carries_no_credentials`
—«la contraseña del DSN no aparece en el cuerpo del 503»— **pasa igual con el
saneado desactivado**: se comprobó borrando la sustitución y el test siguió verde,
porque hoy ni asyncpg ni redis-py meten la credencial en el `str(exc)`. Es
exactamente el modo de fallo del apartado 4 de
`docs/03-guides/verificar-antes-de-implementar.md`: una guarda que no puede
fallar no es una guarda.

Aquella aserción se conserva —es la red de seguridad para el día que un driver
nuevo sí arrastre el DSN—, pero el saneado en sí se prueba AQUÍ, contra mensajes
que sí llevan credenciales. Si alguien quita el `sub`, este fichero se pone rojo.
"""

from __future__ import annotations

import pytest
from api_server.routers.health import _MAX_DETAIL, _scrub

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        (
            "OperationalError: connect failed for "
            "postgresql+asyncpg://api_user:sup3r-s3cr3t@db:5432/agentic",
            "sup3r-s3cr3t",
        ),
        ("ConnectionError: Error connecting to redis://:h0rr1bl3@redis:6379/0", "h0rr1bl3"),
        ("AuthenticationError: bad password for amqp://celery:rabbitpw@broker:5672//", "rabbitpw"),
    ],
)
def test_scrub_removes_credentials_from_urls(message: str, secret: str) -> None:
    cleaned = _scrub(message)
    assert secret not in cleaned, f"credencial superviviente: {cleaned}"
    assert "***@" in cleaned, f"debe quedar la forma de la URL, sin el usuario: {cleaned}"


def test_scrub_keeps_the_useful_part() -> None:
    """Sanear no puede dejar el detalle inútil: el operador necesita el host, el
    puerto y el tipo de error para saber qué arreglar."""
    cleaned = _scrub("OperationalError: connect to postgresql://u:p@db-primary:5432/agentic failed")

    assert "OperationalError" in cleaned
    assert "db-primary:5432" in cleaned


def test_scrub_truncates_a_driver_traceback() -> None:
    """Un mensaje de driver de 4 KB engordaría cada línea de log del healthcheck."""
    cleaned = _scrub("x" * (_MAX_DETAIL * 4))

    assert len(cleaned) <= _MAX_DETAIL + 1  # +1 por el «…» que marca el recorte
    assert cleaned.endswith("…")


def test_scrub_leaves_a_credential_less_message_alone() -> None:
    """El caso mayoritario: los drivers de hoy NO filtran, y el mensaje debe
    llegar intacto (si lo mutilara, el saneado costaría diagnóstico)."""
    message = "ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 5432)"

    assert _scrub(message) == message
