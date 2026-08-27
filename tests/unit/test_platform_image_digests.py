"""Las seis imágenes de plataforma se pinean por digest, y sin release no rompen.

Hermano de `packages/shared-test-runtimes/src/shared_test_runtimes/images.py`
(ADR 0148) para la otra familia de imágenes del repo: las seis que el compose
generado por el instalador descarga de un registry.

**El hueco que motiva este fichero.** El ADR 0148 razonó el pin por digest para
las 14 imágenes de runtime y lo dejó implementado; las seis de plataforma se
quedaron con `${PLATFORM_REGISTRY:-…}/<app>:${PLATFORM_IMAGE_TAG:-…}`
(`compose_generator.py:99-100`), o sea **tag mutable**. El ADR 0161
(`docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md`)
§«Lo que hay hoy, medido» punto 3 lo nombra: es «la misma objeción con la que el
0148 condenó el statu quo de las 14, sin resolver en las 6». Y su orden duro dice
que el instalador NO se publica antes de cerrar esto: un instalador verificado
que descarga seis imágenes sin verificar mueve el eslabón débil un paso, no lo
quita.

**La propiedad que más se comprueba aquí es la degradación**, no el pin. El
criterio de `images.py:34-37` —«vacío significa vacío»— es lo que hace que el
mecanismo se pueda mergear el día antes de la primera release: mientras
`digests` esté vacío, la referencia vuelve **exactamente** a la de hoy y nadie
se entera. Un mecanismo de pin que aborte sin manifiesto publicado deja el
producto sin instalar hasta que exista una release, que es justo al revés de
cómo se llega a tener una.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


def _manifest_text(**overrides: object) -> str:
    from installer_backend.platform_images import PLATFORM_APPS

    payload: dict[str, object] = {
        "registry": "ghcr.io/daycry",
        "version": "v1.0.0",
        "digests": dict.fromkeys(PLATFORM_APPS, _DIGEST_A),
    }
    payload.update(overrides)
    return json.dumps(payload)


def _write(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "platform_images.json"
    path.write_text(_manifest_text(**overrides), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Vacío significa vacío: sin release publicada, la referencia es la de hoy.
# ---------------------------------------------------------------------------
def test_sin_digests_la_referencia_cae_al_tag_de_siempre(tmp_path: Path) -> None:
    """Sin release, `is_pinned` es False y el compose sale byte a byte igual."""
    from installer_backend.platform_images import load_platform_manifest

    manifest = load_platform_manifest(_write(tmp_path, digests={}))

    assert manifest.is_pinned is False
    assert manifest.reference("api-server") == (
        "${PLATFORM_REGISTRY:-ghcr.io/daycry}/api-server:${PLATFORM_IMAGE_TAG:-v1.0.0}"
    )
    assert "@sha256:" not in manifest.reference("api-server")


def test_sin_digests_no_se_exige_nada_mas(tmp_path: Path) -> None:
    """La degradación no puede depender de campos que sólo la release rellena."""
    from installer_backend.platform_images import load_platform_manifest

    manifest = load_platform_manifest(_write(tmp_path, digests={}))
    assert manifest.digest_for("workers") is None


# ---------------------------------------------------------------------------
# Con release publicada: manda el digest, y el tag deja de fingir que es un mando.
# ---------------------------------------------------------------------------
def test_con_digests_la_referencia_lleva_tag_y_digest(tmp_path: Path) -> None:
    """`repo:tag@sha256:…`: el tag viaja para que sea auditable (ADR 0148)."""
    from installer_backend.platform_images import load_platform_manifest

    manifest = load_platform_manifest(_write(tmp_path))

    assert manifest.is_pinned is True
    assert (
        manifest.reference("workers")
        == "${PLATFORM_REGISTRY:-ghcr.io/daycry}/workers:v1.0.0@" + _DIGEST_A
    )


def test_pineado_el_tag_es_literal_y_no_una_variable(tmp_path: Path) -> None:
    """Un `${PLATFORM_IMAGE_TAG:-…}` junto a un digest fijo es un mando que miente.

    Docker resuelve por digest e IGNORA el tag. Dejar la variable pintada al lado
    invita a que alguien edite `PLATFORM_IMAGE_TAG` en el `.env`, vea el compose
    cambiar y siga bajando exactamente la misma imagen.
    """
    from installer_backend.platform_images import load_platform_manifest

    reference = load_platform_manifest(_write(tmp_path)).reference("admin-panel")
    assert "PLATFORM_IMAGE_TAG" not in reference


def test_pineado_el_registry_sigue_siendo_reapuntable(tmp_path: Path) -> None:
    """Camino del host sin salida a internet: mirror sí, digest intacto (ADR 0148).

    Reapuntar el registry no debilita nada: si el mirror sirve otra cosa, el pull
    por digest falla, que es justo lo que se quiere.
    """
    from installer_backend.platform_images import load_platform_manifest

    reference = load_platform_manifest(_write(tmp_path)).reference("watchdog")
    assert reference.startswith("${PLATFORM_REGISTRY:-ghcr.io/daycry}/watchdog:")


# ---------------------------------------------------------------------------
# Lo que se rechaza, y por qué cada rechazo evita un pull contra quién sabe qué.
# ---------------------------------------------------------------------------
def test_un_digest_con_forma_rara_se_rechaza(tmp_path: Path) -> None:
    """El error de `imagetools inspect` no puede colarse como si fuera un digest."""
    from installer_backend.platform_images import (
        PLATFORM_APPS,
        PlatformImageManifestError,
        load_platform_manifest,
    )

    digests = dict.fromkeys(PLATFORM_APPS, _DIGEST_A)
    digests["workers"] = "ERROR: manifest unknown"
    path = _write(tmp_path, digests=digests)

    with pytest.raises(PlatformImageManifestError, match="workers"):
        load_platform_manifest(path)


def test_un_slug_que_no_es_una_app_de_plataforma_se_rechaza(tmp_path: Path) -> None:
    """Una errata en el slug pasaría la validación de forma y moriría en el host."""
    from installer_backend.platform_images import (
        PLATFORM_APPS,
        PlatformImageManifestError,
        load_platform_manifest,
    )

    digests = dict.fromkeys(PLATFORM_APPS, _DIGEST_A)
    digests["api-sever"] = _DIGEST_B
    path = _write(tmp_path, digests=digests)

    with pytest.raises(PlatformImageManifestError, match="api-sever"):
        load_platform_manifest(path)


def test_un_pin_a_medias_se_rechaza(tmp_path: Path) -> None:
    """Media plataforma pineada es peor que ninguna: PARECE pineada.

    Quien audite la instalación ve digests en el compose y da la procedencia por
    cerrada, cuando uno de los seis servicios sigue bajando un tag mutable.
    """
    from installer_backend.platform_images import (
        PLATFORM_APPS,
        PlatformImageManifestError,
        load_platform_manifest,
    )

    digests = dict.fromkeys(PLATFORM_APPS, _DIGEST_A)
    del digests["watchdog"]
    path = _write(tmp_path, digests=digests)

    with pytest.raises(PlatformImageManifestError, match="watchdog"):
        load_platform_manifest(path)


def test_sin_registry_no_hay_manifiesto(tmp_path: Path) -> None:
    """Sin registry, la referencia caería a Docker Hub sin decirlo."""
    from installer_backend.platform_images import (
        PlatformImageManifestError,
        load_platform_manifest,
    )

    with pytest.raises(PlatformImageManifestError, match="registry"):
        load_platform_manifest(_write(tmp_path, registry=""))


def test_sin_version_no_hay_manifiesto(tmp_path: Path) -> None:
    from installer_backend.platform_images import (
        PlatformImageManifestError,
        load_platform_manifest,
    )

    with pytest.raises(PlatformImageManifestError, match="version"):
        load_platform_manifest(_write(tmp_path, version=""))


def test_un_json_ilegible_se_rechaza_diciendo_donde(tmp_path: Path) -> None:
    from installer_backend.platform_images import (
        PlatformImageManifestError,
        load_platform_manifest,
    )

    path = tmp_path / "platform_images.json"
    path.write_text("{no es json", encoding="utf-8")
    with pytest.raises(PlatformImageManifestError, match=r"platform_images\.json"):
        load_platform_manifest(path)
