"""Reescribir el manifiesto de digests de las seis imágenes de plataforma.

Gemelo de :mod:`shared_test_runtimes.release` (ADR 0148) para la otra familia de
imágenes. Esta CLI es **la única mano que escribe** ``platform_images.json``, y
la invoca el job ``installer`` de ``.github/workflows/release-images.yml``
después de publicar las seis y de que Trivy las haya dado por buenas:

```bash
python -m installer_backend.platform_release \\
    --registry ghcr.io/daycry --version v1.0.0 \\
    --digest api-server=sha256:… --digest workers=sha256:… …
```

Por qué existe en vez de un ``sed`` en el workflow: es la condición 1 del ADR
0148 —«nada de digest sin vía de refresco»— y una vía de refresco que no valida
lo que escribe no vale de mucho. Un ``docker buildx imagetools inspect`` que
devuelve vacío, o un mensaje de error del registry, acabaría versionado como si
fuera un digest y el compose de cada instalación intentaría resolverlo.

**Se ejecuta en el mismo job que construye la imagen del instalador, y eso es
deliberado.** El manifiesto llega al repo por PR, que se mergea más tarde; si el
instalador se construyera en otro job desde el checkout, la imagen publicada
pinearía los digests de la release ANTERIOR y encima lo parecería todo correcto.
Reescribir el fichero *antes* del build es lo que hace que la imagen publicada
pinee exactamente lo que se acaba de publicar.

Escribe de forma **idempotente y ordenada**: dos pasadas con los mismos digests
dejan el fichero byte a byte igual, de modo que el job sólo abre PR cuando algo
cambió de verdad. Sin eso, cada release propondría un PR vacío y el mecanismo se
volvería ruido que nadie mira.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from installer_backend.platform_images import (
    MANIFEST_PATH,
    PLATFORM_APPS,
    PlatformImageManifestError,
    load_platform_manifest,
    seal_installer_reference,
)

# Marca de origen del fichero. Se reescribe SIEMPRE: es lo que impide que alguien
# lo encuentre y lo edite creyendo que es configuración.
_GENERATED_BY = (
    ".github/workflows/release-images.yml (job `installer`) vía "
    "`python -m installer_backend.platform_release`. NO EDITAR A MANO: un digest "
    "escrito por una persona no tiene vía de refresco y congela sus CVEs para "
    "siempre (ADR 0148, condición 1)."
)


def _parse_digest(pair: str) -> tuple[str, str]:
    app, sep, digest = pair.partition("=")
    if not sep or not app.strip() or not digest.strip():
        raise argparse.ArgumentTypeError(f"se espera `app=sha256:…`, recibido {pair!r}")
    return app.strip(), digest.strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m installer_backend.platform_release",
        description=(
            "Reescribe el manifiesto de digests de las imágenes de plataforma "
            "(ADR 0148 aplicado a las seis, orden duro del ADR 0161)."
        ),
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--registry", required=True, help="p. ej. ghcr.io/daycry")
    parser.add_argument("--version", required=True, help="tag publicado, p. ej. v1.0.0")
    parser.add_argument(
        "--digest",
        action="append",
        default=[],
        type=_parse_digest,
        metavar="APP=sha256:…",
        help="digest publicado de una app; repetible",
    )
    # La SÉPTIMA imagen. No va en `digests` porque no es una app del compose
    # generado: es la imagen que ESCRIBE ese compose, y su digest lo necesita el
    # artefacto de arranque descargable, no el stack instalado.
    parser.add_argument(
        "--installer-digest",
        default=None,
        metavar="sha256:…",
        help="digest publicado de la imagen del instalador (para sellar --bootstrap)",
    )
    parser.add_argument(
        "--bootstrap",
        type=Path,
        default=None,
        help=(
            "artefacto de arranque descargable a sellar "
            "(docker/bootstrap/docker-compose.generate.yml); exige --installer-digest"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    path: Path = args.manifest

    digests = dict(sorted(args.digest))
    registry = args.registry.strip().rstrip("/")
    version = args.version.strip()
    payload: dict[str, object] = {
        "_generated_by": _GENERATED_BY,
        "_state": (
            f"{len(digests)} de {len(PLATFORM_APPS)} imágenes de plataforma "
            f"publicadas en `{registry}` con el tag `{version}`; el compose que "
            "genera el instalador las referencia por digest, así que un host "
            "sólo puede levantar exactamente lo que publicó este pipeline."
        ),
        "registry": registry,
        "version": version,
        "digests": digests,
    }

    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    # Validar sobre un fichero aparte y mover: un manifiesto rechazado NUNCA
    # llega a tocar el que el generador va a leer.
    staged = path.with_suffix(path.suffix + ".staged")
    staged.write_text(rendered, encoding="utf-8")
    try:
        # Las tres verificaciones que impiden generar un compose mentiroso: que
        # cada digest tenga forma de digest, que cada slug sea una app real, y
        # que estén TODAS. Cinco de seis es un fallo del pipeline, no una release.
        load_platform_manifest(staged)
    except PlatformImageManifestError as exc:
        staged.unlink(missing_ok=True)
        raise SystemExit(f"manifiesto rechazado, {path.name} sin tocar: {exc}") from exc
    staged.replace(path)

    _seal_bootstrap(args.bootstrap, args.installer_digest, registry=registry, version=version)
    return 0


def _seal_bootstrap(
    artifact: Path | None,
    installer_digest: str | None,
    *,
    registry: str,
    version: str,
) -> None:
    """Sellar el artefacto de arranque con el digest de la imagen del instalador.

    Va DESPUÉS del manifiesto y no antes porque el digest del instalador sólo
    existe una vez publicada su imagen, que es lo último que hace la release. El
    manifiesto describe lo que el instalador va a *descargar*; esto describe al
    instalador mismo, y lo consume quien baja el fichero sin clonar el repo.
    """
    if artifact is None and installer_digest is None:
        return
    if artifact is None or installer_digest is None:
        raise SystemExit(
            "`--bootstrap` y `--installer-digest` van juntos: sellar sin digest no "
            "sella nada, y un digest que no se escribe en ningún sitio no protege "
            "a nadie."
        )
    reference = f"{registry}/installer:{version}@{installer_digest.strip()}"
    try:
        sealed = seal_installer_reference(artifact.read_text(encoding="utf-8"), reference)
    except PlatformImageManifestError as exc:
        raise SystemExit(f"artefacto de arranque sin sellar ({artifact.name}): {exc}") from exc
    artifact.write_text(sealed, encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover - punto de entrada de la CLI
    raise SystemExit(main())
