#!/usr/bin/env python
"""Gate de SCA: lee el informe JSON de `pip-audit` y decide si el paso pasa.

Plan prod-11 (`task_pip_audit_05`). Lo invoca el job `security-scan` de
`.github/workflows/ci.yml` justo después de `pip-audit`.

Por qué existe
--------------
El paso de CI corría `pip-audit --strict --skip-editable`, y esas dos banderas
son incompatibles en presencia de instalaciones editables:

* `--skip-editable` marca como OMITIDA cada distribución instalada con
  `pip install -e` — las 13 locales del monorepo (api-server, shared-*,
  agent-runtime…), que no existen en PyPI y por tanto no son auditables.
* `--strict` significa, literalmente, «falla la auditoría entera si la
  recolección de dependencias falla en CUALQUIER dependencia»
  (`pip_audit/_cli.py`: `if args.strict: _fatal(f"{spec.name}: {spec.skip_reason}")`).

Es decir: `--strict` convertía en fatal justo lo que `--skip-editable` acababa
de omitir. El paso moría SIEMPRE en la primera editable por orden alfabético
—«agent-runtime: distribution marked as editable»— sin llegar a auditar ni un
solo paquete. Un rojo permanente que no era una vulnerabilidad y que, peor,
tapaba las vulnerabilidades que sí había: nadie vio nunca la lista.

Qué hace este script (y por qué NO es debilitar la guarda)
----------------------------------------------------------
Lo que `--strict` aportaba de verdad es una idea sana: **no dar por buena una
auditoría incompleta**. Si pip-audit no puede resolver una dependencia de
terceros —no está en PyPI, la red la bloquea, el paquete no tiene versión— y
sólo lo susurra en un log, el verde miente.

Este script mantiene esa exigencia con más precisión que `--strict`:

* tolera las omisiones cuyo motivo es «editable» —nuestras propias
  distribuciones, la excepción deliberada y documentada—,
* y falla ante CUALQUIER otro motivo de omisión, que es exactamente el caso que
  `--strict` protegía,
* y falla, por supuesto, si hay vulnerabilidades (pip-audit ya sale con código 1
  en ese caso; aquí se repite para que el log diga en una línea legible qué
  paquete y qué CVE, en vez de dejarlo enterrado en el JSON).

Las excepciones vigentes viven en `.pip-audit-ignore`, con justificación y fecha
de revisión obligatorias; pip-audit las filtra antes de escribir el JSON, así
que este script no las vuelve a mirar.

Uso
---
    pip-audit --skip-editable --format=json --output pip-audit.json
    python scripts/check_pip_audit_report.py pip-audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Único motivo de omisión aceptable: nuestras 13 distribuciones locales,
# instaladas con `pip install -e` en el mismo job. El texto lo produce
# pip_audit._dependency_source.pip; se compara en minúsculas y por subcadena
# para no atarse a la redacción exacta de una versión concreta de la
# herramienta.
ALLOWED_SKIP_SUBSTRING = "editable"


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(
            f"ERROR: no existe {path}. pip-audit no llegó a escribir el informe: "
            "el paso ha fallado antes de auditar (mira su salida más arriba).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except json.JSONDecodeError as exc:
        print(f"ERROR: {path} no es JSON válido ({exc}).", file=sys.stderr)
        raise SystemExit(1) from None
    if not isinstance(data, dict) or "dependencies" not in data:
        print(
            f"ERROR: {path} no tiene la forma que produce `pip-audit --format=json` "
            "(falta la clave 'dependencies').",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="JSON de `pip-audit --format=json`")
    args = parser.parse_args(argv)

    data = _load(args.report)
    dependencies: list[dict[str, Any]] = data.get("dependencies") or []

    audited = 0
    skipped_editable: list[str] = []
    skipped_unexpected: list[tuple[str, str]] = []
    vulnerable: list[tuple[str, str, list[dict[str, Any]]]] = []

    for dep in dependencies:
        name = str(dep.get("name", "?"))
        skip_reason = dep.get("skip_reason")
        if skip_reason:
            if ALLOWED_SKIP_SUBSTRING in str(skip_reason).lower():
                skipped_editable.append(name)
            else:
                skipped_unexpected.append((name, str(skip_reason)))
            continue
        audited += 1
        vulns: list[dict[str, Any]] = dep.get("vulns") or []
        if vulns:
            vulnerable.append((name, str(dep.get("version", "?")), vulns))

    print(
        f"pip-audit: {audited} distribuciones auditadas, "
        f"{len(skipped_editable)} omitidas por editables (locales del monorepo)."
    )

    problems = False
    # Actions mezcla stdout y stderr: sin vaciar el búfer, el resumen de arriba
    # aparecería DESPUÉS de la lista de problemas y el log se lee al revés.
    sys.stdout.flush()

    if skipped_unexpected:
        problems = True
        print(
            "\nAUDITORÍA INCOMPLETA — dependencias omitidas por un motivo que NO es "
            "'editable'. Un verde con esto dentro no dice nada sobre ellas:",
            file=sys.stderr,
        )
        for name, reason in sorted(skipped_unexpected):
            print(f"  - {name}: {reason}", file=sys.stderr)

    if vulnerable:
        problems = True
        total = sum(len(v) for _, _, v in vulnerable)
        print(
            f"\nVULNERABILIDADES CONOCIDAS — {total} en {len(vulnerable)} paquetes.",
            file=sys.stderr,
        )
        for name, version, vulns in sorted(vulnerable):
            for vuln in vulns:
                fixes = ", ".join(vuln.get("fix_versions") or []) or "SIN VERSIÓN CORREGIDA"
                aliases = ", ".join(vuln.get("aliases") or [])
                alias_txt = f" ({aliases})" if aliases else ""
                print(
                    f"  - {name}=={version}: {vuln.get('id')}{alias_txt} -> corrige: {fixes}",
                    file=sys.stderr,
                )
        print(
            "\nCriterio por defecto: ACTUALIZAR la dependencia "
            "(docs/06-runbooks/triage-vulnerabilidades.md). Si de verdad no hay versión "
            "que lo corrija, la excepción va en `.pip-audit-ignore` CON justificación y "
            "`# review: YYYY-MM-DD`.",
            file=sys.stderr,
        )

    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
