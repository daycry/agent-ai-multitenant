"""El manifiesto que viaja en el árbol, quién lo escribe y quién lo consume.

`test_platform_image_digests.py` comprueba la semántica del módulo sobre
manifiestos de laboratorio. Este fichero comprueba las tres uniones que hacen
que el mecanismo sirva de algo:

1. **El manifiesto versionado carga** y su lista de apps es la que el compose
   generado referencia de verdad — derivada del generador, no copiada aquí. Una
   lista escrita a mano fue el modo de fallo del `watchdog`, que entró el
   2026-08-02 y estuvo diez días sin publicarse porque dos sitios enumeraban las
   apps (`tests/unit/test_app_images_are_built_by_ci.py`).
2. **El generador consume el manifiesto.** Sin esto el módulo sería un JSON
   bonito: el compose seguiría componiendo la referencia por su cuenta y una
   instalación seguiría bajando un tag mutable.
3. **Sólo lo escribe el pipeline**, y valida antes de tocar el fichero. Es la
   condición 1 del ADR 0148: un digest sin vía de refresco es una CVE congelada
   para siempre, y una vía de refresco que no valida lo que escribe versiona el
   mensaje de error del registry como si fuera un digest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIGEST_A = "sha256:" + "a" * 64


def _config() -> object:
    from installer_backend.config import (
        InstallerConfig,
        OllamaProvider,
        ProvidersConfig,
        ResourceConfig,
        StorageConfig,
        SystemConfig,
        TenantConfig,
    )

    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com"),
        resources=ResourceConfig(gpu_enabled=True),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
    )


def _app_images_in_compose() -> dict[str, str]:
    """`app -> image` de los servicios cuya imagen la publica este repo."""
    from installer_backend.compose_generator import APP_IMAGE_REGISTRY, generate_compose

    compose = generate_compose(_config(), monitoring=True)  # type: ignore[arg-type]
    prefix = APP_IMAGE_REGISTRY + "/"
    found: dict[str, str] = {}
    for service in compose["services"].values():
        image = str(service.get("image", ""))
        if image.startswith(prefix):
            found[image[len(prefix) :].split(":", 1)[0]] = image
    return found


# ---------------------------------------------------------------------------
# (1) El manifiesto del árbol
# ---------------------------------------------------------------------------
def test_el_manifiesto_versionado_carga() -> None:
    """Se lee al importar el generador: un manifiesto roto debe doler AQUÍ."""
    from installer_backend.platform_images import load_platform_manifest

    manifest = load_platform_manifest()
    assert manifest.registry, "el manifiesto del árbol tiene que decir de dónde se baja"


def test_las_apps_del_manifiesto_son_las_que_el_compose_referencia() -> None:
    """La lista se DERIVA del generador; enumerarla a mano fue el defecto original."""
    from installer_backend.platform_images import PLATFORM_APPS

    en_compose = set(_app_images_in_compose())
    assert en_compose, "el compose generado no referencia ninguna imagen de este repo"
    assert en_compose == set(PLATFORM_APPS), (
        "el manifiesto de digests y el compose generado no hablan de las mismas "
        f"imágenes. Compose: {sorted(en_compose)}. Manifiesto: {sorted(PLATFORM_APPS)}. "
        "Una app en el compose y fuera del manifiesto se instala por tag mutable "
        "sin que nada avise; una en el manifiesto y fuera del compose es un digest "
        "que el pipeline resolverá contra una imagen que nadie levanta."
    )


def test_la_version_del_manifiesto_es_la_de_la_plataforma() -> None:
    """ADR 0160: un tag `vX.Y.Z` es «este stack, entero».

    Si el manifiesto se queda en `v1.0.0` después de un bump a `1.1.0`, el
    instalador genera un compose que baja la release anterior — y lo hace en
    silencio, porque `v1.0.0` existe en el registry y el `pull` funciona.
    """
    from installer_backend import __version__
    from installer_backend.platform_images import load_platform_manifest

    assert load_platform_manifest().version == f"v{__version__}"


# ---------------------------------------------------------------------------
# (2) El generador consume el manifiesto
# ---------------------------------------------------------------------------
def test_el_compose_generado_sale_del_manifiesto() -> None:
    """Cada `image:` de app es exactamente lo que dice el manifiesto."""
    from installer_backend.platform_images import load_platform_manifest

    manifest = load_platform_manifest()
    for app, image in sorted(_app_images_in_compose().items()):
        assert image == manifest.reference(app), (
            f"el compose compone la imagen de `{app}` por su cuenta en vez de "
            "leerla del manifiesto: el día que haya digests publicados, ese "
            "servicio seguiría bajando un tag mutable."
        )


def test_el_pin_llega_al_compose_cuando_hay_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """La prueba de que el mecanismo sirve: con digests, el compose los lleva.

    Se fuerza un manifiesto pineado en memoria porque hoy el del árbol está
    vacío a propósito (no hay release publicada). Sin esta prueba, todo lo demás
    seguiría verde el día que los digests existan y el compose los ignore.
    """
    from installer_backend import compose_generator
    from installer_backend.platform_images import (
        PLATFORM_APPS,
        PlatformImageManifest,
    )

    pinned = PlatformImageManifest(
        version="v1.0.0",
        registry="ghcr.io/daycry",
        digests=dict.fromkeys(PLATFORM_APPS, _DIGEST_A),
    )
    monkeypatch.setattr(compose_generator, "PLATFORM_IMAGES", pinned)

    for app, image in sorted(_app_images_in_compose().items()):
        assert image.endswith("@" + _DIGEST_A), (
            f"con release publicada, la imagen de `{app}` sigue sin digest: {image}"
        )


# ---------------------------------------------------------------------------
# (3) Sólo lo escribe el pipeline, y valida antes de tocar nada
# ---------------------------------------------------------------------------
def _run_release(*argv: str) -> int:
    from installer_backend.platform_release import main

    return main(argv)


def test_la_cli_de_release_escribe_un_manifiesto_valido(tmp_path: Path) -> None:
    from installer_backend.platform_images import PLATFORM_APPS, load_platform_manifest

    target = tmp_path / "platform_images.json"
    args = ["--manifest", str(target), "--registry", "ghcr.io/daycry", "--version", "v1.0.0"]
    for app in PLATFORM_APPS:
        args += ["--digest", f"{app}={_DIGEST_A}"]

    assert _run_release(*args) == 0
    manifest = load_platform_manifest(target)
    assert manifest.is_pinned is True
    assert set(manifest.digests) == set(PLATFORM_APPS)


def test_la_cli_de_release_es_idempotente(tmp_path: Path) -> None:
    """Dos pasadas iguales dejan el fichero byte a byte igual.

    Es lo que permite que el job abra PR **sólo cuando algo cambió de verdad**:
    con una escritura no determinista, cada release propondría un PR vacío y el
    mecanismo se volvería ruido que nadie mira.
    """
    from installer_backend.platform_images import PLATFORM_APPS

    target = tmp_path / "platform_images.json"
    args = ["--manifest", str(target), "--registry", "ghcr.io/daycry", "--version", "v1.0.0"]
    for app in PLATFORM_APPS:
        args += ["--digest", f"{app}={_DIGEST_A}"]

    _run_release(*args)
    first = target.read_bytes()
    _run_release(*args)
    assert target.read_bytes() == first


def test_la_cli_de_release_rechaza_sin_tocar_el_fichero(tmp_path: Path) -> None:
    """Un manifiesto rechazado NUNCA llega a pisar al que el generador lee."""
    from installer_backend.platform_images import PLATFORM_APPS

    target = tmp_path / "platform_images.json"
    target.write_text(
        json.dumps({"registry": "ghcr.io/daycry", "version": "v1.0.0", "digests": {}}),
        encoding="utf-8",
    )
    intacto = target.read_bytes()

    args = ["--manifest", str(target), "--registry", "ghcr.io/daycry", "--version", "v1.0.0"]
    for app in PLATFORM_APPS:
        args += ["--digest", f"{app}=no-es-un-digest"]

    with pytest.raises(SystemExit):
        _run_release(*args)
    assert target.read_bytes() == intacto
    assert not list(tmp_path.glob("*.staged")), "quedó basura del intento rechazado"


def test_la_cli_de_release_rechaza_un_pin_incompleto(tmp_path: Path) -> None:
    """Cinco de seis resueltos es un fallo del pipeline, no una release."""
    from installer_backend.platform_images import PLATFORM_APPS

    target = tmp_path / "platform_images.json"
    args = ["--manifest", str(target), "--registry", "ghcr.io/daycry", "--version", "v1.0.0"]
    for app in list(PLATFORM_APPS)[:-1]:
        args += ["--digest", f"{app}={_DIGEST_A}"]

    with pytest.raises(SystemExit):
        _run_release(*args)
    assert not target.exists()


# ---------------------------------------------------------------------------
# (4) El artefacto de arranque: la SÉPTIMA imagen se sella con su propio digest
# ---------------------------------------------------------------------------
#
# `docker/bootstrap/docker-compose.generate.yml` es el fichero descargable del
# camino sin clon (ADR 0161, envoltorio de la opción B). Lleva a la vista el
# hueco `${INSTALLER_IMAGE_DIGEST:-}`, y el runbook 09 §«Dónde acaba el digest»
# dice quién lo rellena: **el pipeline al publicar, nunca una mano**. Sin esa
# pieza, el artefacto que la gente descarga referencia un tag mutable y la frase
# del runbook es una promesa sin mecanismo.
_ARTIFACT_LINE = "    image: ghcr.io/daycry/installer:v1.0.0${INSTALLER_IMAGE_DIGEST:-}\n"


def test_sellar_sustituye_el_hueco_por_la_referencia_publicada() -> None:
    from installer_backend.platform_images import seal_installer_reference

    sealed = seal_installer_reference(
        "services:\n  generate:\n" + _ARTIFACT_LINE,
        f"ghcr.io/daycry/installer:v1.0.0@{_DIGEST_A}",
    )
    assert f"image: ghcr.io/daycry/installer:v1.0.0@{_DIGEST_A}" in sealed
    assert "INSTALLER_IMAGE_DIGEST" not in sealed


def test_sellar_es_idempotente_y_reemplaza_el_digest_viejo() -> None:
    """Una release nueva vuelve a sellar sobre el sello anterior.

    Si sólo supiera sustituir el hueco, el segundo sellado no encontraría nada
    que cambiar y el artefacto se quedaría pineado a la release anterior — un
    fichero que dice ir por digest y va por el equivocado.
    """
    from installer_backend.platform_images import seal_installer_reference

    viejo = f"ghcr.io/daycry/installer:v1.0.0@{_DIGEST_A}"
    nuevo = "ghcr.io/daycry/installer:v1.1.0@sha256:" + "b" * 64
    una = seal_installer_reference("    image: " + viejo + "\n", nuevo)
    dos = seal_installer_reference(una, nuevo)
    assert una == dos
    assert nuevo in dos
    assert _DIGEST_A not in dos


def test_sellar_se_niega_si_no_encuentra_la_linea() -> None:
    """Un artefacto que cambió de forma NO se sella a medias, se rechaza.

    El modo de fallo silencioso sería devolver el texto intacto: el job pasaría
    en verde y publicaría un artefacto por tag mutable diciendo que va sellado.
    """
    from installer_backend.platform_images import (
        PlatformImageManifestError,
        seal_installer_reference,
    )

    with pytest.raises(PlatformImageManifestError, match="installer"):
        seal_installer_reference(
            "services:\n  generate:\n    image: ghcr.io/daycry/api-server:v1.0.0\n",
            f"ghcr.io/daycry/installer:v1.0.0@{_DIGEST_A}",
        )


def test_sellar_exige_una_referencia_con_digest() -> None:
    from installer_backend.platform_images import (
        PlatformImageManifestError,
        seal_installer_reference,
    )

    with pytest.raises(PlatformImageManifestError, match="sha256"):
        seal_installer_reference(_ARTIFACT_LINE, "ghcr.io/daycry/installer:v1.0.0")


def test_la_cli_de_release_sella_el_artefacto_de_arranque(tmp_path: Path) -> None:
    """El punto de enganche completo: manifiesto + artefacto, en una pasada."""
    from installer_backend.platform_images import PLATFORM_APPS

    artifact = tmp_path / "docker-compose.generate.yml"
    artifact.write_text("services:\n  generate:\n" + _ARTIFACT_LINE, encoding="utf-8")
    target = tmp_path / "platform_images.json"

    args = [
        "--manifest",
        str(target),
        "--registry",
        "ghcr.io/daycry",
        "--version",
        "v1.0.0",
        "--installer-digest",
        _DIGEST_A,
        "--bootstrap",
        str(artifact),
    ]
    for app in PLATFORM_APPS:
        args += ["--digest", f"{app}={_DIGEST_A}"]

    assert _run_release(*args) == 0
    assert f"ghcr.io/daycry/installer:v1.0.0@{_DIGEST_A}" in artifact.read_text(encoding="utf-8")


def test_el_artefacto_del_arbol_se_puede_sellar() -> None:
    """La guarda que impide que el fichero real se aleje de lo que sabe sellar.

    Vive aquí y no en el test del artefacto porque lo que se comprueba es el
    contrato entre los dos: el día que alguien reordene ese `image:`, esto se
    entera ANTES de la release, no durante.
    """
    from installer_backend.platform_images import seal_installer_reference

    artifact = _REPO_ROOT / "docker" / "bootstrap" / "docker-compose.generate.yml"
    if not artifact.is_file():  # pragma: no cover - el artefacto se versiona
        pytest.skip("todavía no existe el artefacto de arranque")
    sealed = seal_installer_reference(
        artifact.read_text(encoding="utf-8"),
        f"ghcr.io/daycry/installer:v1.0.0@{_DIGEST_A}",
    )
    assert _DIGEST_A in sealed


def test_el_manifiesto_dice_que_no_se_edita_a_mano() -> None:
    """La marca de origen es lo que impide que alguien lo edite creyendo que es config."""
    from installer_backend.platform_images import MANIFEST_PATH

    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "release-images.yml" in str(raw.get("_generated_by", "")), (
        "el manifiesto no dice quién lo escribe; sin esa marca el siguiente que "
        "lo abra lo tratará como configuración y teclerá un digest a mano"
    )
