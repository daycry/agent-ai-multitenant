"""Un `${{ }}` dentro de otro `${{ }}` no es una expresión: es texto literal.

El 2026-08-21 los catorce builds de `Build runtime templates` murieron con
`invalid reference format` porque el tag llevaba, tal cual, la cadena
`ghcr.io/${{ github.repository_owner }}`. La causa: al cambiar el namespace se
sustituyó dentro de una cadena entrecomillada que ya vivía dentro de un `${{ }}`
abierto::

    IMAGE_REF: ${{ … && format('{0}/agent-runtime-{1}', 'ghcr.io/${{ … }}', …) }}

GitHub Actions no reevalúa ahí: la cadena viaja entera. Y **actionlint pasa en
verde** —lo hizo— porque la sintaxis es correcta; lo que está mal es el
significado. Así que esta clase de error no la cubre el linter de workflows y hay
que vigilarla aparte.

Duele el doble en un workflow como ése, cuyo camino de publicación **sólo corre
en `master`**: el error no puede aparecer en ninguna rama ni en ningún PR, así
que el merge es la primera ejecución que lo ve. Una guarda local es lo único que
lo adelanta.
"""

from __future__ import annotations

from pathlib import Path

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

_ABRE = "${{"
_CIERRA = "}}"


def aperturas_anidadas(texto: str) -> list[int]:
    """Offsets donde se abre un `${{` estando otro sin cerrar.

    Se recorre a mano y no con una expresión regular porque entre las dos
    aperturas hay llaves de los placeholders de `format` (`{0}`, `{1}`): una regex
    que exija «apertura, nada de llaves, otra apertura» falla justo por eso, y fue
    el primer intento de encontrar este fallo — devolvió cero coincidencias con el
    error delante.
    """
    fuera: list[int] = []
    i = profundidad = 0
    while i < len(texto):
        if texto.startswith(_ABRE, i):
            if profundidad:
                fuera.append(i)
            profundidad += 1
            i += len(_ABRE)
        elif texto.startswith(_CIERRA, i) and profundidad:
            profundidad -= 1
            i += len(_CIERRA)
        else:
            i += 1
    return fuera


def _workflows() -> list[Path]:
    return sorted(_WORKFLOWS.glob("*.yml"))


def test_ningun_workflow_anida_expresiones() -> None:
    culpables: list[str] = []
    for wf in _workflows():
        texto = wf.read_text(encoding="utf-8")
        for offset in aperturas_anidadas(texto):
            linea = texto.count("\n", 0, offset) + 1
            culpables.append(f"{wf.name}:{linea}")
    assert not culpables, (
        "estos sitios abren un ${{ }} dentro de otro, así que el interior es texto "
        f"literal y no se evalúa: {culpables}. Pasa el valor como ARGUMENTO de "
        "`format(...)` en vez de interpolarlo dentro de la cadena."
    )


def test_el_detector_reconoce_el_caso_real() -> None:
    """Non-vacuidad de verdad: se le da el fallo exacto que ocurrió."""
    malo = (
        "      IMAGE_REF: ${{ needs.prep.outputs.publish == 'true' && "
        "format('{0}/agent-runtime-{1}', 'ghcr.io/${{ github.repository_owner }}', "
        "matrix.template) }}"
    )
    assert aperturas_anidadas(malo), "el detector no ve el caso que tumbó los 14 builds"
    bueno = (
        "      tags: ${{ needs.prep.outputs.publish == 'true' && "
        "format('{0},{1}/x:{2}', env.IMAGE_REF, env.REGISTRY, github.sha) "
        "|| env.IMAGE_REF }}"
    )
    assert not aperturas_anidadas(bueno), "el detector marca una expresión correcta"
    dos_seguidas = "      image: ${{ env.REGISTRY }}/api-server:${{ github.sha }}"
    assert not aperturas_anidadas(dos_seguidas), (
        "dos expresiones consecutivas en la misma línea son legítimas y frecuentes"
    )


def test_hay_workflows_y_expresiones_que_revisar() -> None:
    """Si el descubrimiento se rompe, la guarda de arriba pasaría vacía.

    Los umbrales son MEDIDOS, no estimados: el 2026-08-21 había 5 workflows y 95
    expresiones. Se dejan justo por debajo para que una caída real —un glob que
    deja de casar, un fichero renombrado— salga en rojo en vez de en verde vacío.
    """
    ficheros = _workflows()
    assert len(ficheros) >= 5, f"sólo se encontraron {len(ficheros)} workflows"
    total = sum(w.read_text(encoding="utf-8").count(_ABRE) for w in ficheros)
    assert total >= 90, f"sólo se vieron {total} expresiones en total"
