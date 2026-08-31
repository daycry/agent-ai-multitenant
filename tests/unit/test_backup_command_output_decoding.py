"""`SubprocessRunner` (workers.backup): cómo se decodifica la salida de tar/pg_dump.

Misma familia que el defecto medido el 2026-08-31 en `workers.git_repos._run_git`
(`text=True` sin `encoding=` decodifica con el locale del host), pero aquí la
consecuencia es otra y por eso los tests son otros.

En `backup.py` la salida de los comandos NO se parsea nunca: el motor ramifica
por `returncode` y mete `stdout`/`stderr` en el texto de `BackupError`. O sea que
una decodificación torcida no cambia el control de flujo — degrada el mensaje que
un operador lee **en pleno DR**.

Lo que sí es un fallo de verdad es el otro extremo: con decodificación estricta,
un solo byte que no case con el códec hace saltar `UnicodeDecodeError` DENTRO de
`subprocess.run`, y el backup nocturno muere antes de escribir ningún artefacto.
Un nombre de fichero exótico bajo un bind path no puede tumbar la copia de
seguridad — que es justo el proceso cuyo trabajo es sobrevivir a lo inesperado.
"""

from __future__ import annotations

import sys

import pytest
from workers.backup import SubprocessRunner

pytestmark = pytest.mark.unit


def _emite(expr: str) -> list[str]:
    """argv de un hijo que escribe BYTES crudos por los DOS canales.

    `expr` es una expresión Python que evalúa a `bytes`; el hijo la escribe en
    `.buffer`, que no traduce ni saltos de línea ni códec. Así el test controla
    los bytes EXACTOS que ve el runner, que es de lo que va esto.

    Los dos canales y no sólo `stderr` porque el motor lee
    `result.stderr.strip() or result.stdout.strip()`: si la decodificación se
    arreglara en uno y no en el otro, el fallback silencioso al `stdout` volvería
    a dar mojibake justo cuando tar no escribe en `stderr`.
    """
    return [
        sys.executable,
        "-c",
        f"import sys; _b = {expr}\n"
        "for _c in (sys.stdout, sys.stderr): _c.buffer.write(_b); _c.buffer.flush()",
    ]


def test_un_byte_indecodificable_no_tumba_la_captura() -> None:
    """El fallo real: `tar` nombrando un fichero cuyos bytes no son UTF-8.

    En POSIX un nombre de fichero es una ristra de bytes cualquiera, y `tar` los
    escupe tal cual en sus avisos. Con decodificación estricta eso no da un
    backup degradado: da `UnicodeDecodeError` dentro de `subprocess.run`, con lo
    que el run se va por excepción y esa noche NO hay bundle.
    """
    # 0x81 no está definido en cp1252 Y es una continuación suelta en utf-8: el
    # test vale igual en un dev Windows y en el contenedor con LANG=C.UTF-8.
    # Sin esa propiedad pasaría en CI por la razón equivocada.
    result = SubprocessRunner().run(_emite(r'b"aviso: \x81.txt"'), timeout=30)

    assert result.returncode == 0
    # No se exige QUÉ pone en lugar del byte roto (eso es política del códec),
    # sino que el resto del mensaje sobrevivió y hay un resultado que devolver.
    for canal in (result.stderr, result.stdout):
        assert "aviso:" in canal
        assert ".txt" in canal


def test_los_acentos_de_un_diagnostico_llegan_legibles() -> None:
    """El bundle se captura en un contenedor con LANG=C.UTF-8: la salida es UTF-8.

    Decodificarla con el locale del host convertía `documentación` en
    `documentaciÃ³n` en el texto del `BackupError`. No rompe el backup, pero se
    lee durante una restauración, que es el peor momento para tener que adivinar
    si el fichero del que se queja tar es el que uno cree.
    """
    result = SubprocessRunner().run(_emite('"documentación/guía.md".encode("utf-8")'))

    assert result.stderr.strip() == "documentación/guía.md"
    assert result.stdout.strip() == "documentación/guía.md"
