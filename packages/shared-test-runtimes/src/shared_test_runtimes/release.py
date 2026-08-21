"""Reescribir el manifiesto de release de las imágenes de runtime (ADR 0148).

Esta CLI es **la única mano que escribe** ``runtime_images.json``, y la invoca
el job ``refresh-digests`` de ``.github/workflows/build-runtime-templates.yml``
después de publicar las 14 imágenes y de que Trivy las haya dado por buenas:

```bash
python -m shared_test_runtimes.release \
    --registry ghcr.io/daycry --version v1 \
    --digest python-pytest=sha256:… --digest node-jest=sha256:… …
```

Por qué existe en vez de un `sed` en el workflow: es la condición 1 del ADR
0148 —«nada de digest sin vía de refresco»— y una vía de refresco que no valida
lo que escribe no vale de mucho. Un `docker buildx imagetools inspect` que
devuelve vacío, o un mensaje de error del registry, acabaría versionado como si
fuera un digest y el worker intentaría resolverlo en cada tarea. Aquí se
comprueba la forma antes de tocar el fichero.

Escribe de forma **idempotente y ordenada**: dos pasadas con los mismos digests
dejan el fichero byte a byte igual, de modo que el job solo abre PR cuando algo
cambió de verdad.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from shared_test_runtimes.catalog import assert_manifest_covers_catalog
from shared_test_runtimes.images import MANIFEST_PATH, RuntimeImageManifestError, load_manifest

# Marca de origen del fichero. Se reescribe SIEMPRE: es lo que impide que
# alguien encuentre el JSON y lo edite creyendo que es config.
_GENERATED_BY = (
    ".github/workflows/build-runtime-templates.yml (job `refresh-digests`) vía "
    "`python -m shared_test_runtimes.release`. NO EDITAR A MANO: un digest escrito "
    "por una persona no tiene vía de refresco y congela sus CVEs para siempre "
    "(ADR 0148, condición 1)."
)


def _parse_digest(pair: str) -> tuple[str, str]:
    slug, sep, digest = pair.partition("=")
    if not sep or not slug.strip() or not digest.strip():
        raise argparse.ArgumentTypeError(f"se espera `slug=sha256:…`, recibido {pair!r}")
    return slug.strip(), digest.strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m shared_test_runtimes.release",
        description="Reescribe el manifiesto de digests de las imágenes de runtime (ADR 0148).",
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--registry", required=True, help="p. ej. ghcr.io/daycry")
    parser.add_argument("--version", required=True, help="tag versionado publicado, p. ej. v1")
    parser.add_argument(
        "--digest",
        action="append",
        default=[],
        type=_parse_digest,
        metavar="SLUG=sha256:…",
        help="digest publicado de una plantilla; repetible",
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
            f"{len(digests)} plantillas publicadas en `{registry}` con el tag "
            f"`{version}`; el catálogo las referencia por digest y el worker las "
            f"descarga por digest o aborta (ADR 0148)."
        ),
        "registry": registry,
        "version": version,
        "digests": digests,
    }

    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    # Validar sobre un fichero aparte y mover: un manifiesto rechazado NUNCA
    # llega a tocar el que el catálogo va a leer.
    staged = path.with_suffix(path.suffix + ".staged")
    staged.write_text(rendered, encoding="utf-8")
    try:
        # Las dos verificaciones que impiden publicar un catálogo mentiroso: que
        # cada digest tenga forma de digest, y que estén TODAS las plantillas. Un
        # slug con una errata pasaría la primera y moriría al importar el
        # catálogo — o sea, después de mergear el PR, en producción.
        assert_manifest_covers_catalog(load_manifest(staged, env={}))
    except RuntimeImageManifestError as exc:
        staged.unlink(missing_ok=True)
        raise SystemExit(f"manifiesto rechazado, {path.name} sin tocar: {exc}") from exc
    staged.replace(path)
    return 0


if __name__ == "__main__":  # pragma: no cover - punto de entrada de la CLI
    raise SystemExit(main())
