"""Referencia inmutable de las 14 imágenes de runtime (ADR 0148).

Hasta el 2026-08-01 el catálogo componía ``agent-runtime-<slug>:v1`` con un
``_IMAGE_TAG`` constante y **cada host construía su propia variante**: dos
instalaciones del mismo commit ejecutaban el código NO confiable de sus tenants
en imágenes distintas y nadie podía decir cuál. Sin esa respuesta el Principio
Rector 2 —aislamiento por contenedor— no lo puede auditar nadie.

Lo que este módulo fija es el mecanismo firmado en el ADR 0148 (opción a):

* La referencia de cada plantilla sale de un **manifiesto de release**
  (``runtime_images.json``) que **reescribe el pipeline**, nunca una mano: un
  digest a mano sería la congelación de CVEs del riesgo 3 de `prod-11`.
* Cuando el manifiesto trae digests, la referencia es
  ``<registry>/agent-runtime-<slug>:<version>@sha256:<digest>`` y lo es para
  **las 14**: un pin a medias es peor que ninguno, porque parece que hay
  garantía donde no la hay.
* Mientras no exista release publicada el manifiesto está vacío y el catálogo
  vuelve al nombre local — el estado de hoy, dicho en voz alta en el fichero en
  vez de escondido en una constante.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "packages" / "shared-test-runtimes" / "src" / "shared_test_runtimes"
MANIFEST_PATH = PACKAGE_DIR / "runtime_images.json"

_A_DIGEST = "sha256:" + "ab" * 32
_ANOTHER_DIGEST = "sha256:" + "cd" * 32


def _write(path: Path, **payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# split_reference — parsear una referencia de imagen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("agent-runtime-go-test:v1", ("agent-runtime-go-test", "v1", None)),
        ("agent-runtime-go-test", ("agent-runtime-go-test", None, None)),
        (
            f"ghcr.io/agentic-platform/agent-runtime-go-test:v1@{_A_DIGEST}",
            ("ghcr.io/agentic-platform/agent-runtime-go-test", "v1", _A_DIGEST),
        ),
        (
            f"ghcr.io/agentic-platform/agent-runtime-go-test@{_A_DIGEST}",
            ("ghcr.io/agentic-platform/agent-runtime-go-test", None, _A_DIGEST),
        ),
        # Un registry con puerto lleva `:` en el HOST, no un tag. Partir por el
        # último `:` sin mirar el `/` es el bug clásico de este parseo.
        (
            "localhost:5000/agent-runtime-go-test",
            ("localhost:5000/agent-runtime-go-test", None, None),
        ),
        (
            "localhost:5000/agent-runtime-go-test:v2",
            ("localhost:5000/agent-runtime-go-test", "v2", None),
        ),
    ],
)
def test_split_reference_separates_repo_tag_and_digest(
    reference: str, expected: tuple[str, str | None, str | None]
) -> None:
    from shared_test_runtimes.images import split_reference

    assert split_reference(reference) == expected


def test_pinned_pull_reference_drops_the_tag() -> None:
    """La forma canónica para tirar de un digest es ``repo@sha256:…``.

    El tag viaja en el catálogo porque un humano necesita saber qué versión
    corre (y Dependabot proponer la siguiente), pero quien resuelve es el
    digest: pasarle al daemon la referencia canónica evita depender de cómo
    normalice cada versión de docker una referencia con tag Y digest.
    """
    from shared_test_runtimes.images import pinned_pull_reference

    ref = f"ghcr.io/agentic-platform/agent-runtime-go-test:v1@{_A_DIGEST}"
    canonical = f"ghcr.io/agentic-platform/agent-runtime-go-test@{_A_DIGEST}"
    assert pinned_pull_reference(ref) == canonical


def test_pinned_pull_reference_is_none_when_there_is_no_digest() -> None:
    """Sin digest no hay procedencia que verificar: quien llama decide.

    Es el caso de la imagen de runtime propia de un proyecto (ADR 0129), que se
    construye en el host y no vive en ningún registry.
    """
    from shared_test_runtimes.images import pinned_pull_reference

    assert pinned_pull_reference("agent-runtime-go-test:v1") is None


# ---------------------------------------------------------------------------
# El manifiesto de release
# ---------------------------------------------------------------------------


def test_manifest_without_digests_falls_back_to_the_local_name(tmp_path: Path) -> None:
    """Estado previo a la primera release publicada: build local por host.

    No es el destino, es el punto de partida — y está DICHO en un fichero que se
    lee, no deducido de una constante enterrada en el catálogo.
    """
    from shared_test_runtimes.images import load_manifest

    path = _write(tmp_path / "m.json", version="v1", registry="", digests={})
    manifest = load_manifest(path, env={})

    assert manifest.is_pinned is False
    assert manifest.reference("go-test") == "agent-runtime-go-test:v1"
    assert manifest.digest_for("go-test") is None


def test_manifest_with_digests_composes_registry_version_and_digest(tmp_path: Path) -> None:
    from shared_test_runtimes.images import load_manifest

    path = _write(
        tmp_path / "m.json",
        version="v1.2.0",
        registry="ghcr.io/agentic-platform",
        digests={"go-test": _A_DIGEST},
    )
    manifest = load_manifest(path, env={})

    assert manifest.is_pinned is True
    assert manifest.reference("go-test") == (
        f"ghcr.io/agentic-platform/agent-runtime-go-test:v1.2.0@{_A_DIGEST}"
    )
    assert manifest.digest_for("go-test") == _A_DIGEST


def test_manifest_rejects_a_digest_without_a_registry(tmp_path: Path) -> None:
    """Un digest sin registry no se puede descargar de ningún sitio.

    Fallar aquí en voz alta es preferible a componer una referencia que el
    worker intentará resolver contra Docker Hub.
    """
    from shared_test_runtimes.images import RuntimeImageManifestError, load_manifest

    path = _write(tmp_path / "m.json", version="v1", registry="", digests={"go-test": _A_DIGEST})
    with pytest.raises(RuntimeImageManifestError, match="registry"):
        load_manifest(path, env={})


@pytest.mark.parametrize("bogus", ["sha256:cafe", "abcd" * 16, "sha512:" + "ab" * 32, ""])
def test_manifest_rejects_a_malformed_digest(tmp_path: Path, bogus: str) -> None:
    from shared_test_runtimes.images import RuntimeImageManifestError, load_manifest

    path = _write(
        tmp_path / "m.json",
        version="v1",
        registry="ghcr.io/agentic-platform",
        digests={"go-test": bogus},
    )
    with pytest.raises(RuntimeImageManifestError, match="digest"):
        load_manifest(path, env={})


def test_manifest_rejects_an_empty_version(tmp_path: Path) -> None:
    from shared_test_runtimes.images import RuntimeImageManifestError, load_manifest

    path = _write(tmp_path / "m.json", version="", registry="", digests={})
    with pytest.raises(RuntimeImageManifestError, match="version"):
        load_manifest(path, env={})


def test_registry_env_var_relocates_the_repository_keeping_the_digest(tmp_path: Path) -> None:
    """La palanca del host air-gapped (ADR 0148, § «Decisión del operador»).

    El mirror local cambia DÓNDE se busca; el digest sigue diciendo QUÉ tiene
    que salir de allí. Por eso reapuntar el registry no debilita nada: si el
    mirror sirve otra cosa, el pull por digest falla.
    """
    from shared_test_runtimes.images import REGISTRY_ENV_VAR, load_manifest

    path = _write(
        tmp_path / "m.json",
        version="v1",
        registry="ghcr.io/agentic-platform",
        digests={"go-test": _A_DIGEST},
    )
    manifest = load_manifest(path, env={REGISTRY_ENV_VAR: "registry.interna:5000/mirror"})

    assert manifest.reference("go-test") == (
        f"registry.interna:5000/mirror/agent-runtime-go-test:v1@{_A_DIGEST}"
    )


# ---------------------------------------------------------------------------
# El manifiesto VERSIONADO en el paquete
# ---------------------------------------------------------------------------


def test_the_shipped_manifest_loads_and_says_it_is_generated() -> None:
    """El fichero del repo es válido y declara quién lo escribe.

    Condición 1 del ADR 0148: «nada de digest sin vía de refresco». Un JSON sin
    marca de origen invita a editarlo a mano, que es exactamente la congelación
    de CVEs que la condición prohíbe.
    """
    from shared_test_runtimes.images import load_manifest

    assert MANIFEST_PATH.is_file(), f"falta el manifiesto de release en {MANIFEST_PATH}"
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    generated_by = str(raw.get("_generated_by", ""))
    assert "build-runtime-templates.yml" in generated_by, (
        "el manifiesto debe declarar el workflow que lo reescribe: es la vía de "
        "refresco que la condición 1 del ADR 0148 exige"
    )
    load_manifest(MANIFEST_PATH, env={})


def test_the_shipped_manifest_covers_every_template_when_it_is_pinned() -> None:
    """Pin a medias = no hay pin, pero con apariencia de garantía.

    Si el pipeline publica 13 de 14 y el catálogo deja la que falta con tag
    mutable, la pregunta «¿qué imagen ejecutó el código de este tenant?» sigue
    sin respuesta justo donde no se mira.
    """
    from shared_test_runtimes.catalog import CATALOG
    from shared_test_runtimes.images import load_manifest

    manifest = load_manifest(MANIFEST_PATH, env={})
    if not manifest.is_pinned:
        pytest.skip("todavía no hay release publicada: el manifiesto no trae digests")
    missing = sorted(tid for tid in CATALOG if manifest.digest_for(tid) is None)
    assert not missing, f"plantillas sin digest en el manifiesto: {missing}"


def test_the_catalog_refuses_a_partially_pinned_manifest(tmp_path: Path) -> None:
    """La guarda anterior mira el fichero de hoy; ésta mira el mecanismo.

    Que el manifiesto del repo esté completo no impide que el pipeline publique
    uno cojo mañana. El catálogo se niega a componerse a medias.
    """
    from shared_test_runtimes.catalog import assert_manifest_covers_catalog
    from shared_test_runtimes.images import RuntimeImageManifestError, load_manifest

    path = _write(
        tmp_path / "m.json",
        version="v1",
        registry="ghcr.io/agentic-platform",
        digests={"go-test": _A_DIGEST},
    )
    with pytest.raises(RuntimeImageManifestError, match="python-pytest"):
        assert_manifest_covers_catalog(load_manifest(path, env={}))


# ---------------------------------------------------------------------------
# El refresco: la CLI que corre el pipeline
# ---------------------------------------------------------------------------


def _every_template_argv(path: Path, *, skip: str | None = None) -> list[str]:
    """Los argumentos que pasa el pipeline: las 14 plantillas con su digest."""
    from shared_test_runtimes.catalog import list_ids

    argv = [
        "--manifest",
        str(path),
        "--registry",
        "ghcr.io/agentic-platform",
        "--version",
        "v1",
    ]
    for index, tid in enumerate(list_ids()):
        if tid == skip:
            continue
        argv += ["--digest", f"{tid}=sha256:{index:02d}" + "ef" * 31]
    return argv


def test_release_cli_rewrites_the_manifest_with_the_published_digests(tmp_path: Path) -> None:
    from shared_test_runtimes.catalog import list_ids
    from shared_test_runtimes.release import main

    path = _write(tmp_path / "m.json", version="v1", registry="", digests={})
    rc = main(_every_template_argv(path))

    assert rc == 0
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["registry"] == "ghcr.io/agentic-platform"
    assert sorted(written["digests"]) == sorted(list_ids())
    assert "build-runtime-templates.yml" in written["_generated_by"]


def test_release_cli_refuses_to_publish_a_manifest_missing_a_template(tmp_path: Path) -> None:
    """Trece de catorce no se escriben: se rechaza la pasada entera.

    Sin esto, un `imagetools inspect` que falla en una plantilla —o un slug con
    una errata— produce un manifiesto cojo que el PR mergea y que revienta al
    importar el catálogo, ya en producción. El sitio barato de descubrirlo es el
    job que lo escribe.
    """
    from shared_test_runtimes.release import main

    path = _write(tmp_path / "m.json", version="v1", registry="", digests={})
    with pytest.raises(SystemExit, match="rust-cargo"):
        main(_every_template_argv(path, skip="rust-cargo"))
    assert json.loads(path.read_text(encoding="utf-8"))["digests"] == {}


def test_release_cli_rejects_a_malformed_digest(tmp_path: Path) -> None:
    """El pipeline es la única mano que escribe aquí: que valide lo que escribe.

    Un `imagetools inspect` que devuelve vacío o un mensaje de error acabaría
    versionado como digest si nadie mira.
    """
    from shared_test_runtimes.release import main

    path = _write(tmp_path / "m.json", version="v1", registry="", digests={})
    with pytest.raises(SystemExit):
        main(
            [
                "--manifest",
                str(path),
                "--registry",
                "ghcr.io/agentic-platform",
                "--version",
                "v1",
                "--digest",
                "go-test=not-a-digest",
            ]
        )


def test_release_cli_is_idempotent(tmp_path: Path) -> None:
    """Dos pasadas iguales dejan el mismo fichero → el PR de refresco no existe.

    Sin esto el pipeline abriría un PR en cada push aunque no cambie nada.
    """
    from shared_test_runtimes.release import main

    path = _write(tmp_path / "m.json", version="v1", registry="", digests={})
    args = _every_template_argv(path)
    main(args)
    first = path.read_text(encoding="utf-8")
    main(args)
    assert path.read_text(encoding="utf-8") == first
