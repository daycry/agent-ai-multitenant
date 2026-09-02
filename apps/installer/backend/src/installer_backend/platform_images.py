"""Referencia por digest de las SEIS imágenes de plataforma (ADR 0148 → 0161).

Este módulo responde, para las imágenes que el compose generado descarga, la
misma pregunta que :mod:`shared_test_runtimes.images` responde para las catorce
de runtime: **¿qué imagen exacta se instaló en este host?**

Hasta hoy no tenía respuesta. `compose_generator` componía
``${PLATFORM_REGISTRY:-…}/<app>:${PLATFORM_IMAGE_TAG:-v1.0.0}`` con dos
constantes, o sea un **tag mutable**: el `v1.0.0` de hoy no es el de mañana, y
dos instalaciones del mismo número de versión podían estar corriendo binarios
distintos sin que nada lo dijera. Es literalmente la objeción con la que el ADR
0148 (``docs/05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md``)
condenó el statu quo de las catorce, sin resolver en las seis; el ADR 0161
(``docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md``)
lo mide y pone el orden duro: **no se publica el instalador antes de cerrar
esto**, porque un instalador verificado que descarga seis imágenes sin verificar
no arregla la cadena, sólo mueve el eslabón débil un paso.

La pieza que lo hace posible es el manifiesto de release (``platform_images.json``,
junto a este módulo), con las mismas tres propiedades deliberadas que el de los
runtimes:

* **Lo escribe el pipeline, no una mano.** Condición 1 del ADR 0148: un digest
  tecleado no tiene vía de refresco, y un pin sin refresco es una CVE congelada
  para siempre. Quien lo reescribe es :mod:`installer_backend.platform_release`,
  invocado por el job `installer` de ``.github/workflows/release-images.yml``
  después de que las seis hayan pasado su build y su Trivy.
* **El tag viaja junto al digest** (``…:v1.0.0@sha256:…``). Sin él nadie sabe qué
  versión corre y Dependabot no puede proponer la siguiente.
* **Vacío significa vacío.** Mientras no haya release publicada, ``digests`` es
  ``{}`` y la referencia vuelve **byte a byte** a la de siempre, con sus dos
  variables de entorno. Esto no es una concesión: un mecanismo de pin que
  abortase sin manifiesto publicado dejaría el producto sin instalar hasta que
  existiera una release, que es justo al revés de cómo se llega a tener una.

Dos diferencias con el módulo hermano, y las dos son a propósito:

**1. El registry es obligatorio siempre.** En los runtimes puede faltar porque
sin release se construyen en el host con ``scripts/dev/build-runtime-templates.sh``
y el nombre local es legítimo. Aquí no hay caso local: el compose generado vive
en la máquina del operador y lo único que puede hacer es *bajar* la imagen. Una
referencia sin registry no es «local», es Docker Hub sin decirlo.

**2. El pin es todo o nada.** Cinco de seis pineadas es peor que ninguna, porque
*parece* pineado: quien audite el compose ve digests, da la procedencia por
cerrada y no vuelve a mirar el servicio que sigue bajando un tag. Si el pipeline
sólo resuelve cinco, lo que hay es un fallo del pipeline, no una release.

**Los mandos que quedan, y por qué sólo uno.** ``PLATFORM_REGISTRY`` sigue
reapuntando el repositorio a un mirror —el camino del host sin salida a internet,
igual que ``RUNTIME_IMAGE_REGISTRY``— y no debilita nada: si el mirror sirve otra
cosa, el pull por digest falla, que es justo lo que se quiere.
``PLATFORM_IMAGE_TAG``, en cambio, **desaparece de la referencia en cuanto hay
digest**, y no por purismo: Docker resuelve por digest e ignora el tag, así que
dejar la variable pintada al lado sería un mando que miente — alguien lo edita en
el ``.env``, ve cambiar el compose y sigue bajando exactamente la misma imagen.
Para instalar otra versión se regenera el árbol con el instalador de esa versión,
que es el flujo del ADR 0161 (el instalador genera, el operador ejecuta).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: Las apps cuya imagen publica este repo y que el compose generado referencia.
#:
#: Va escrita aquí y no derivada del árbol porque dentro del contenedor del
#: instalador NO hay repositorio: el manifiesto tiene que viajar con el paquete.
#: Quien deriva del árbol es la guarda
#: (`tests/unit/test_platform_images_wiring.py`), que compara esta tupla con las
#: imágenes que `generate_compose` referencia de verdad — así una app nueva no
#: puede entrar en el compose y quedarse fuera del pin sin que nada avise, que es
#: exactamente lo que le pasó al `watchdog` durante diez días en 2026-08.
PLATFORM_APPS: tuple[str, ...] = (
    "admin-panel",
    # `task_cv_44` (auditoría 2026-09-01, B-09): las dos imágenes que el worker
    # LANZA por tarea. El ADR 0148 pineó las plantillas y este manifiesto las
    # apps; `agent-runtime:v1` y `browser-runtime:v1` no se publicaban ni
    # pineaban. Se construyen en `release-images.yml` (job `runtimes`) y el
    # compose se las pasa al worker por `WORKERS_*_RUNTIME_IMAGE`.
    "agent-runtime",
    "api-server",
    "browser-runtime",
    "notification-dispatcher",
    "orchestrator",
    "watchdog",
    "workers",
)

#: Fichero versionado con el resultado de la última release publicada.
MANIFEST_PATH = Path(__file__).with_name("platform_images.json")

#: Reapunta el repositorio a un mirror conservando el digest. Se resuelve en el
#: HOST, al hacer `docker compose up`, no aquí: por eso viaja al compose como
#: expresión de sustitución y no como valor.
REGISTRY_ENV_VAR = "PLATFORM_REGISTRY"

#: Mando heredado del tag. Sólo sobrevive mientras NO haya digests publicados;
#: ver §«Los mandos que quedan» del docstring.
TAG_ENV_VAR = "PLATFORM_IMAGE_TAG"

# Un digest de imagen es sha256 en hex minúscula. Aceptar cualquier otra cosa
# convertiría el manifiesto en un sitio donde `imagetools inspect` puede colar su
# mensaje de error como si fuera un digest.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# La línea `image:` del artefacto de arranque descargable
# (`docker/bootstrap/docker-compose.generate.yml`, ADR 0161 §envoltorio de la B).
# Acepta las dos formas que puede tener: el hueco `${INSTALLER_IMAGE_DIGEST:-}` de
# antes de la primera release, y un digest ya sellado por una release anterior —
# porque sellar tiene que poder REEMPLAZAR, no sólo rellenar. Si sólo supiera
# rellenar, la segunda release no encontraría hueco y dejaría el artefacto
# apuntando a la imagen de la primera: un fichero que dice ir por digest y va por
# el equivocado, que es peor que ir por tag.
_INSTALLER_IMAGE_LINE = re.compile(
    r"^(?P<indent>[ \t]*)image:[ \t]*"
    r"(?P<reference>\S*/installer:[^\s@$]+)"
    r"(?:\$\{[A-Z_]+(?::-[^}]*)?\}|@sha256:[0-9a-f]{64})?[ \t]*$",
    re.M,
)


class PlatformImageManifestError(RuntimeError):
    """El manifiesto de release no se puede usar tal y como está.

    Se prefiere abortar la generación antes que escribir un compose con una
    referencia que el host resolverá contra quién sabe qué — y que, a diferencia
    de un error aquí, nadie va a mirar dos veces.
    """


@dataclass(frozen=True)
class PlatformImageManifest:
    """Lo que la última release publicada dejó dicho sobre las seis imágenes."""

    version: str
    registry: str
    digests: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_pinned(self) -> bool:
        """¿Hay release publicada de la que fiarse?"""
        return bool(self.digests)

    def digest_for(self, app: str) -> str | None:
        return self.digests.get(app)

    def registry_expression(self) -> str:
        """Prefijo de repositorio tal y como viaja al compose generado."""
        return "${" + REGISTRY_ENV_VAR + ":-" + self.registry + "}"

    def tag_expression(self) -> str:
        """Tag tal y como viaja al compose cuando NO hay digest que mande."""
        return "${" + TAG_ENV_VAR + ":-" + self.version + "}"

    def reference(self, app: str) -> str:
        """Referencia completa de la imagen de ``app`` para el compose generado."""
        if app not in PLATFORM_APPS:
            raise PlatformImageManifestError(
                f"{app!r} no es una imagen de plataforma; las que hay son {list(PLATFORM_APPS)}"
            )
        repository = f"{self.registry_expression()}/{app}"
        digest = self.digest_for(app)
        if digest is None:
            return f"{repository}:{self.tag_expression()}"
        return f"{repository}:{self.version}@{digest}"


def load_platform_manifest(path: Path | None = None) -> PlatformImageManifest:
    """Leer y VALIDAR el manifiesto de release.

    Las validaciones no son ceremonia: cada una corresponde a una forma concreta
    de acabar instalando una plataforma que nadie puede identificar después.
    """
    source = path or MANIFEST_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - el fichero se versiona
        raise PlatformImageManifestError(f"falta el manifiesto de release en {source}") from exc
    except json.JSONDecodeError as exc:
        raise PlatformImageManifestError(
            f"manifiesto de release ilegible ({source.name}): {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PlatformImageManifestError(f"el manifiesto {source.name} debe ser un objeto JSON")

    version = str(raw.get("version") or "").strip()
    if not version:
        raise PlatformImageManifestError(
            f"{source.name}: `version` es obligatoria y no puede ir vacía"
        )

    registry = str(raw.get("registry") or "").strip().rstrip("/")
    if not registry:
        raise PlatformImageManifestError(
            f"{source.name}: falta `registry`. Sin él la referencia caería a "
            "Docker Hub sin decirlo, y el compose generado no tiene otro sitio "
            "de donde bajar estas imágenes."
        )

    digests_raw = raw.get("digests") or {}
    if not isinstance(digests_raw, dict):
        raise PlatformImageManifestError(
            f"{source.name}: `digests` debe ser un objeto app → digest"
        )

    digests: dict[str, str] = {}
    for app, digest in sorted(digests_raw.items()):
        app_name = str(app)
        if app_name not in PLATFORM_APPS:
            raise PlatformImageManifestError(
                f"{source.name}: {app_name!r} no es una imagen de plataforma. Un "
                "slug con una errata pasaría la validación de forma y moriría en "
                f"el host del operador. Las que hay son {list(PLATFORM_APPS)}."
            )
        text = str(digest)
        if not _DIGEST_RE.match(text):
            raise PlatformImageManifestError(
                f"{source.name}: digest inválido para {app_name!r}: {text!r} "
                "(se espera `sha256:` + 64 hex en minúscula)"
            )
        digests[app_name] = text

    if digests:
        faltan = [app for app in PLATFORM_APPS if app not in digests]
        if faltan:
            raise PlatformImageManifestError(
                f"{source.name}: pin a medias, faltan {faltan}. Media plataforma "
                "pineada es peor que ninguna porque PARECE pineada: quien audite "
                "el compose ve digests y da la procedencia por cerrada."
            )

    return PlatformImageManifest(version=version, registry=registry, digests=digests)


def seal_installer_reference(text: str, reference: str) -> str:
    """Sellar el artefacto de arranque con la referencia publicada del instalador.

    Éste es **el punto de enganche** que el artefacto descargable pide a gritos:
    `docker/bootstrap/docker-compose.generate.yml` lleva el hueco
    ``${INSTALLER_IMAGE_DIGEST:-}`` a la vista y el runbook 09 §«Dónde acaba el
    digest» dice quién lo rellena — «el pipeline al publicar, y nunca a mano».
    Sin esta función esa frase sería una promesa sin mecanismo, y el fichero que
    la gente descarga iría por **tag mutable**: quien lo bajase mañana podría
    recibir otro contenido bajo el mismo nombre.

    Cómo se usa (lo hace el job `installer` de ``release-images.yml``, después de
    publicar la imagen y de que Trivy la dé por buena)::

        python -m installer_backend.platform_release \\
            --registry ghcr.io/daycry --version v1.0.0 \\
            --digest api-server=sha256:… … \\
            --installer-digest sha256:… \\
            --bootstrap docker/bootstrap/docker-compose.generate.yml

    Se hace con una sustitución **verificada** y no con un ``sed`` en el YAML del
    workflow por la misma razón por la que el manifiesto lo escribe una CLI: un
    reemplazo que no comprueba lo que reemplaza es indistinguible de uno que no
    reemplazó nada. Por eso aquí se exige encontrar **exactamente una** línea y
    que la referencia traiga digest; cualquier otra cosa aborta el paso y deja la
    release en rojo, en vez de publicar un artefacto que dice ir sellado.
    """
    if "@sha256:" not in reference or not _DIGEST_RE.match(reference.split("@", 1)[1]):
        raise PlatformImageManifestError(
            f"la referencia con la que sellar el artefacto no lleva `@sha256:…`: "
            f"{reference!r}. Sellar con un tag es escribir a mano lo que ya estaba."
        )

    matches = list(_INSTALLER_IMAGE_LINE.finditer(text))
    if len(matches) != 1:
        raise PlatformImageManifestError(
            f"se esperaba UNA línea `image: …/installer:<tag>` en el artefacto de "
            f"arranque y se han encontrado {len(matches)}. El fichero ha cambiado "
            "de forma: sellarlo a ciegas dejaría un artefacto por tag mutable "
            "diciendo que va por digest."
        )

    match = matches[0]
    return text[: match.start()] + f"{match['indent']}image: {reference}" + text[match.end() :]


__all__ = [
    "MANIFEST_PATH",
    "PLATFORM_APPS",
    "REGISTRY_ENV_VAR",
    "TAG_ENV_VAR",
    "PlatformImageManifest",
    "PlatformImageManifestError",
    "load_platform_manifest",
    "seal_installer_reference",
]
