"""Referencia inmutable de las imágenes de runtime (ADR 0148).

Este módulo responde a una sola pregunta, y es la que hace auditable el
Principio Rector 2: **¿qué imagen exacta ejecutó el código de este tenant?**

Hasta el 2026-08-01 no tenía respuesta. El catálogo componía
``agent-runtime-<slug>:v1`` con una constante y el workflow de la matriz
construía con ``push: false``: **cada host construía su propia variante** de las
14 imágenes donde corre el código NO confiable. Dos instalaciones del mismo
commit ejecutaban cosas distintas y nadie podía decir cuáles.

El ADR 0148 (opción a, firmada) sustituye eso por una referencia resuelta por
digest. La pieza que lo hace posible es el **manifiesto de release**
(``runtime_images.json``, junto a este módulo):

```json
{
  "_generated_by": ".github/workflows/build-runtime-templates.yml …",
  "registry": "ghcr.io/agentic-platform",
  "version": "v1",
  "digests": {"python-pytest": "sha256:…", …}
}
```

Tres propiedades deliberadas:

* **Lo escribe el pipeline, no una mano.** Es la condición 1 del ADR: un digest
  editado a mano no tiene vía de refresco, y un pin sin refresco es una CVE
  congelada para siempre (riesgo 3 de `prod-11`). Quien lo reescribe es
  :mod:`shared_test_runtimes.release`, invocado por el job de publicación.
* **El tag viaja junto al digest** (``…:v1@sha256:…``), igual que en los ``FROM``
  de los Dockerfiles: sin él nadie sabe qué versión corre y Dependabot no puede
  proponer la siguiente.
* **Vacío significa vacío.** Mientras no haya release publicada, ``digests`` es
  ``{}`` y la referencia vuelve al nombre local que construye
  ``scripts/dev/build-runtime-templates.sh``. El estado de hoy queda escrito en
  un fichero que se lee, en vez de deducirse de una constante enterrada.

La palanca ``RUNTIME_IMAGE_REGISTRY`` reapunta el repositorio a un mirror sin
tocar el digest — es el camino del host air-gapped que documenta
``docs/04-reference/installation.md``. Reapuntar el registry no debilita nada:
si el mirror sirve otra cosa, el pull por digest falla, que es justo lo que se
quiere.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# Prefijo del repositorio de cada imagen de runtime. El slug del catálogo lo
# completa: ``agent-runtime-python-pytest``.
IMAGE_PREFIX = "agent-runtime-"

# Fichero versionado con el resultado de la última release publicada.
MANIFEST_PATH = Path(__file__).with_name("runtime_images.json")

# Reapunta el repositorio a un mirror (host sin salida a internet), conservando
# el digest. Ver ADR 0148 § «Decisión del operador».
REGISTRY_ENV_VAR = "RUNTIME_IMAGE_REGISTRY"

# Un digest de imagen es sha256 en hex minúscula. Aceptar cualquier otra cosa
# convertiría el manifiesto en un sitio donde `imagetools inspect` puede colar
# su mensaje de error como si fuera un digest.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class RuntimeImageManifestError(RuntimeError):
    """El manifiesto de release no se puede usar tal y como está.

    Se prefiere fallar al arrancar antes que componer una referencia que el
    worker resolverá contra quién sabe qué.
    """


def is_valid_digest(text: str) -> bool:
    """¿Es ``text`` un digest de imagen con la forma que Docker resuelve?"""
    return bool(_DIGEST_RE.match(text))


def split_reference(reference: str) -> tuple[str, str | None, str | None]:
    """Partir ``repo[:tag][@sha256:…]`` en sus tres piezas.

    El ``:`` del puerto de un registry (``localhost:5000/foo``) NO es un tag:
    partir por el último ``:`` sin comprobar que no haya ``/`` detrás es el bug
    clásico de este parseo, y aquí produciría un pull contra un repositorio
    inexistente.
    """
    repo_and_tag, _, digest = reference.partition("@")
    tag: str | None = None
    head, sep, maybe_tag = repo_and_tag.rpartition(":")
    if sep and "/" not in maybe_tag:
        repo_and_tag, tag = head, maybe_tag
    return repo_and_tag, tag, (digest or None)


def pinned_pull_reference(reference: str) -> str | None:
    """Referencia canónica ``repo@sha256:…`` con la que descargar, o ``None``.

    ``None`` significa «esta referencia no declara procedencia»: la imagen de
    runtime propia de un proyecto (ADR 0129) se construye en el host y no vive
    en ningún registry. Quien llama decide qué hacer con ese caso; el worker lo
    ejecuta tal cual, como venía haciendo.
    """
    repo, _, digest = split_reference(reference)
    if digest is None:
        return None
    return f"{repo}@{digest}"


@dataclass(frozen=True)
class ReleaseManifest:
    """Lo que la última release publicada dejó dicho sobre las 14 imágenes."""

    version: str
    registry: str = ""
    digests: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_pinned(self) -> bool:
        """¿Hay release publicada de la que fiarse?"""
        return bool(self.digests)

    def digest_for(self, slug: str) -> str | None:
        return self.digests.get(slug)

    def repository(self, slug: str) -> str:
        name = f"{IMAGE_PREFIX}{slug}"
        return f"{self.registry}/{name}" if self.registry else name

    def reference(self, slug: str) -> str:
        """Referencia completa de la plantilla ``slug`` para el catálogo."""
        reference = f"{self.repository(slug)}:{self.version}"
        digest = self.digest_for(slug)
        return f"{reference}@{digest}" if digest else reference


def load_manifest(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> ReleaseManifest:
    """Leer y VALIDAR el manifiesto de release.

    Las validaciones no son ceremonia: cada una corresponde a una forma
    concreta de acabar ejecutando código no confiable en una imagen que nadie
    puede identificar.
    """
    source = path or MANIFEST_PATH
    environ = os.environ if env is None else env
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - el fichero se versiona
        raise RuntimeImageManifestError(f"falta el manifiesto de release en {source}") from exc
    except json.JSONDecodeError as exc:
        detail = f"manifiesto de release ilegible ({source}): {exc}"
        raise RuntimeImageManifestError(detail) from exc
    if not isinstance(raw, dict):
        raise RuntimeImageManifestError(f"el manifiesto {source} debe ser un objeto JSON")

    version = str(raw.get("version") or "").strip()
    if not version:
        raise RuntimeImageManifestError(f"{source}: `version` es obligatoria y no puede ir vacía")

    digests_raw = raw.get("digests") or {}
    if not isinstance(digests_raw, dict):
        raise RuntimeImageManifestError(f"{source}: `digests` debe ser un objeto slug → digest")
    digests: dict[str, str] = {}
    for slug, digest in sorted(digests_raw.items()):
        text = str(digest)
        if not _DIGEST_RE.match(text):
            raise RuntimeImageManifestError(
                f"{source}: digest inválido para {slug!r}: {text!r} "
                "(se espera `sha256:` + 64 hex en minúscula)"
            )
        digests[str(slug)] = text

    registry = str(environ.get(REGISTRY_ENV_VAR) or raw.get("registry") or "").strip().rstrip("/")
    if digests and not registry:
        raise RuntimeImageManifestError(
            f"{source}: hay digests publicados pero ningún `registry` del que "
            f"descargarlos (ni {REGISTRY_ENV_VAR} en el entorno)"
        )

    return ReleaseManifest(version=version, registry=registry, digests=digests)


__all__ = [
    "IMAGE_PREFIX",
    "MANIFEST_PATH",
    "REGISTRY_ENV_VAR",
    "ReleaseManifest",
    "RuntimeImageManifestError",
    "is_valid_digest",
    "load_manifest",
    "pinned_pull_reference",
    "split_reference",
]
