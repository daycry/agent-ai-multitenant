"""Afirmar sobre imágenes de runtime sin fijar la FORMA de la referencia.

El catálogo devuelve dos formas según haya release publicada o no (ADR 0148)::

    sin release:  agent-runtime-<slug>:<version>
    con release:  ghcr.io/<owner>/agent-runtime-<slug>@sha256:…

Seis tests de integración fijaban el literal `agent-runtime-python-pytest:v1` y
empezaron a fallar el 2026-08-21, el día que las catorce imágenes se publicaron
por primera vez y `runtime_images.json` dejó de tener `digests: {}`. No estaban
mal escritos por descuido: describían con exactitud el único estado que este repo
había tenido nunca. Lo que pasa es que ese estado era el que el ADR 0148 se firmó
para terminar, así que fijarlo era fijar lo transitorio.

Y lo que esos tests prueban de verdad —qué plantilla gana entre el default del
proyecto y el de la herramienta, y que `runtime_template` se sustituye por una
imagen concreta— no depende de la forma de la referencia.

La forma sí tiene sus propios tests, y ahí es donde debe estar:
``tests/unit/test_runtime_image_pinning.py`` cubre las dos ramas con manifiestos
controlados, incluida la de digest, sin depender de si hoy hay release o no.
"""

from __future__ import annotations


def apunta_a(imagen: str, plantilla: str) -> bool:
    """¿La referencia apunta a esa plantilla, sea cual sea su forma?

    Se comprueba por el segmento `agent-runtime-<slug>`, que está en las dos
    formas. Los tests que la usan afirman además la NEGATIVA sobre la plantilla
    rival cuando lo que se prueba es una precedencia: sin eso, un resolutor que
    devolviera siempre la misma imagen pasaría.
    """
    return f"agent-runtime-{plantilla}" in imagen
