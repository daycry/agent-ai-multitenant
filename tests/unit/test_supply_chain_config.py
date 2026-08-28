"""Guardas estáticas de la cadena de suministro (plan prod-11).

La auditoría de producción (2026-06-10) encontró la cobertura SCA a **cero en las
cuatro superficies** del repo: pip sin `pip-audit`, npm sin `npm audit`, imágenes
sin Trivy y ningún `dependabot.yml`. Encima, todo lo que el build descarga iba por
referencia **mutable**: `actions/checkout@v5` (quien controle ese repo controla
nuestro CI), `FROM python:3.12-slim` sin digest, y un `curl … | php` sin checksum
metiendo Composer en dos imágenes de runtime.

Este módulo es la red que evita que eso vuelva sin que nadie lo note. Todos los
tests son ESTÁTICOS (parsean YAML / Dockerfiles / package.json): corren en
cualquier máquina, sin runner de GitHub ni docker.

Cada test de inventario lleva una aserción de que **encontró algo** — la trampa
nº4 de `docs/03-guides/verificar-antes-de-implementar.md`: una guarda cuyo
descubrimiento deja de encontrar infractores pasa vacíamente y envejece sin avisar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"
DOCKER_DIR = REPO_ROOT / "docker"
TRIVYIGNORE = REPO_ROOT / ".trivyignore"
PIP_AUDIT_IGNORE = REPO_ROOT / ".pip-audit-ignore"
CONSTRAINTS = REPO_ROOT / "constraints.txt"

# Superficies npm del monorepo (task_next_update_01 / task_npm_audit_06).
NPM_SURFACES = ("apps/admin-panel", "apps/installer")

# next: el suelo NO es «el último parche de la línea 14», es la primera versión
# que sale limpia del gate.
#
# Historia corta, porque el número de aquí se ha quedado obsoleto una vez y sería
# fácil repetirlo: el suelo estuvo en `14.2.35` (último parche de 14.2.x, cierra
# la crítica GHSA-955p-x3mx-jcvp y el postcss embebido). El 2026-08-10 se midió
# que **el rango vulnerable de los avisos `high` vivos abarcaba toda la línea 14
# y toda la 15 hasta 16.3.0-preview**: o sea que 14.2.35 dejó de ser un suelo
# seguro sin que cambiara una sola línea del repo, y esta guarda lo habría dado
# por bueno. Un suelo que sólo cierra las CVEs del día que se escribió no es una
# guarda anti-regresión, es un comentario.
#
# Hoy (2026-08-19) las dos superficies van en `15.5.23` y `npm audit --omit=dev
# --audit-level=high` sale en **exit 0 en ambas** (medido, ver
# task_next_update_01). El suelo se sube a esa versión: degradar a cualquier
# 14.x —todas dentro del rango vulnerable medido— rompe la suite en vez de
# esperar a que lo cace un `npm audit` que nadie mira.
MIN_NEXT = (15, 5, 23)

# El job de CI que orquesta el SCA (task_pip_audit_05 / 06 / 07).
SECURITY_SCAN_JOB = "security-scan"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: el YAML de primer nivel no es un mapping"
    # PyYAML (YAML 1.1) resuelve la clave desnuda ``on:`` como el booleano True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _jobs(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jobs = data.get("jobs") or {}
    return {k: v for k, v in jobs.items() if isinstance(v, dict)}


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") or "" for s in _steps(job))


def _uses(job: dict[str, Any]) -> list[str]:
    return [s["uses"] for s in _steps(job) if isinstance(s.get("uses"), str)]


def _pip_audit_invocations(job: dict[str, Any]) -> list[str]:
    """Líneas que EJECUTAN pip-audit, con las continuaciones `\\` ya unidas.

    Sin unir las continuaciones, una guarda que lea línea a línea puede dar por
    ausente una bandera que sí está, sólo porque el comando se partió en dos
    líneas para que se lea.
    """
    joined = re.sub(r"\\\n\s*", " ", _run_text(job))
    return [line.strip() for line in joined.splitlines() if re.match(r"^\s*pip-audit\b", line)]


def _dockerfiles_under_docker() -> list[Path]:
    return sorted(DOCKER_DIR.rglob("Dockerfile*"))


def _from_refs(dockerfile: Path) -> list[tuple[int, str]]:
    """(lineno, image_ref) de cada FROM externo del Dockerfile.

    Excluye los FROM que referencian una etapa previa del MISMO Dockerfile
    (multi-stage) y los que resuelven un ARG (``FROM ${BASE_IMAGE}``): esos no
    son descargas de un registry y no llevan digest.
    """
    stages: set[str] = set()
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        m = re.match(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", line, re.I)
        if not m:
            continue
        ref, alias = m.group(1), m.group(2)
        if alias:
            stages.add(alias.lower())
        if ref.lower() in stages or ref.startswith("$"):
            continue
        out.append((lineno, ref))
    return out


@pytest.fixture(scope="module")
def workflows() -> dict[str, dict[str, Any]]:
    files = _workflow_files()
    assert files, f"no hay workflows bajo {WORKFLOWS_DIR}"
    return {p.name: _load_yaml(p) for p in files}


# ---------------------------------------------------------------------------
# task_next_update_01 — next parcheado en las dos superficies npm
# ---------------------------------------------------------------------------


def _parse_pin(spec: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", spec)
    assert m is not None, f"no puedo leer una versión de '{spec}'"
    return tuple(int(g) for g in m.groups())


def test_npm_surfaces_pin_a_patched_next() -> None:
    """`next` debe estar en 14.2.35+ en las DOS superficies npm.

    El síntoma visible de gap5-2 era `next 14.2.5` congelado con una crítica
    (GHSA-955p-x3mx-jcvp, divulgación no autenticada de Server Functions) y fix
    disponible en la misma línea de parches. Esta guarda hace que degradarlo sea
    un test rojo, no un `npm audit` que nadie mira.
    """
    seen = 0
    problems: list[str] = []
    for surface in NPM_SURFACES:
        manifest = REPO_ROOT / surface / "package.json"
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        pin = (data.get("dependencies") or {}).get("next")
        if pin is None:
            continue
        seen += 1
        if _parse_pin(pin) < MIN_NEXT:
            problems.append(f"{surface}: next {pin} < {'.'.join(map(str, MIN_NEXT))}")
    assert seen >= 2, f"la guarda dejó de encontrar las superficies npm (vio {seen})"
    assert not problems, "next vulnerable:\n" + "\n".join(problems)


def test_npm_surfaces_pin_a_matching_eslint_config_next() -> None:
    """`eslint-config-next` va pineado exacto a la misma versión que `next`.

    Está pineado sin caret en los dos manifests; si se desincroniza del `next`
    real, `next lint` corre con las reglas de otra minor.
    """
    seen = 0
    problems: list[str] = []
    for surface in NPM_SURFACES:
        manifest = REPO_ROOT / surface / "package.json"
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        next_pin = (data.get("dependencies") or {}).get("next")
        eslint_pin = (data.get("devDependencies") or {}).get("eslint-config-next")
        if next_pin is None or eslint_pin is None:
            continue
        seen += 1
        if _parse_pin(eslint_pin) != _parse_pin(next_pin):
            problems.append(f"{surface}: eslint-config-next {eslint_pin} != next {next_pin}")
    assert seen >= 2, f"la guarda dejó de encontrar las superficies npm (vio {seen})"
    assert not problems, "eslint-config-next desincronizado:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# task_dependabot_02 — dependabot.yml cubre las cuatro superficies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dependabot() -> dict[str, Any]:
    assert DEPENDABOT.is_file(), (
        ".github/dependabot.yml no existe: sin vía reactiva, cada digest y cada SHA "
        "pineado por este plan congela sus CVEs para siempre (riesgo 3 de prod-11)"
    )
    return _load_yaml(DEPENDABOT)


def _updates(cfg: dict[str, Any], ecosystem: str) -> list[dict[str, Any]]:
    return [
        u
        for u in (cfg.get("updates") or [])
        if isinstance(u, dict) and u.get("package-ecosystem") == ecosystem
    ]


def _declared_dirs(cfg: dict[str, Any], ecosystem: str) -> set[str]:
    """Directorios cubiertos por un ecosistema, expandiendo los globs.

    Dependabot admite `directory:` (uno) y `directories:` (lista con globs
    `*`/`**`). Las entradas de este repo usan la forma plural para no repetir
    19 bloques idénticos, así que la guarda tiene que expandirlos igual que
    Dependabot para saber si un directorio queda de verdad cubierto.
    """
    out: set[str] = set()
    for update in _updates(cfg, ecosystem):
        raw = update.get("directories") or [update.get("directory")]
        for entry in raw:
            if not isinstance(entry, str):
                continue
            declared = entry.rstrip("/") or "/"
            if "*" not in declared:
                out.add(declared)
                continue
            for match in REPO_ROOT.glob(declared.lstrip("/")):
                if match.is_dir():
                    out.add("/" + match.relative_to(REPO_ROOT).as_posix())
    return out


def test_dependabot_covers_the_four_ecosystems(dependabot: dict[str, Any]) -> None:
    assert dependabot.get("version") == 2, "dependabot.yml debe declarar `version: 2`"
    present = {
        u.get("package-ecosystem") for u in (dependabot.get("updates") or []) if isinstance(u, dict)
    }
    missing = {"pip", "npm", "docker", "github-actions"} - present
    assert not missing, f"dependabot.yml no cubre los ecosistemas: {sorted(missing)}"


def test_dependabot_registers_every_python_distribution(dependabot: dict[str, Any]) -> None:
    """Cada `pyproject.toml` del árbol tiene su entrada `pip`.

    Falla si alguien añade una distribución Python nueva sin registrarla: ese
    paquete quedaría fuera del refresco automático de dependencias.
    """
    covered = _declared_dirs(dependabot, "pip")
    expected: set[str] = set()
    for pyproject in REPO_ROOT.glob("apps/*/pyproject.toml"):
        expected.add("/" + pyproject.parent.relative_to(REPO_ROOT).as_posix())
    for pyproject in REPO_ROOT.glob("apps/*/*/pyproject.toml"):
        expected.add("/" + pyproject.parent.relative_to(REPO_ROOT).as_posix())
    for pyproject in REPO_ROOT.glob("packages/*/pyproject.toml"):
        expected.add("/" + pyproject.parent.relative_to(REPO_ROOT).as_posix())
    expected.add("/docker/agent-runtimes/agent-runtime")
    expected.add("/")  # raíz: pyproject.toml + requirements-dev.txt del toolchain
    assert len(expected) >= 12, (
        f"la guarda dejó de encontrar las distribuciones Python (vio {len(expected)})"
    )
    missing = sorted(expected - covered)
    assert not missing, "distribuciones Python sin entrada en dependabot.yml: " + ", ".join(missing)


def test_dependabot_registers_every_npm_surface(dependabot: dict[str, Any]) -> None:
    covered = _declared_dirs(dependabot, "npm")
    missing = [s for s in NPM_SURFACES if "/" + s not in covered]
    assert not missing, f"superficies npm sin entrada en dependabot.yml: {missing}"


def test_dependabot_registers_every_docker_directory(dependabot: dict[str, Any]) -> None:
    """Cada directorio con Dockerfile bajo `docker/` tiene su entrada `docker`.

    Es la contraparte del digest-pinning (task_digest_pin_11): un digest sin
    mecanismo de refresh es PEOR que un tag flotante.
    """
    covered = _declared_dirs(dependabot, "docker")
    expected = {
        "/" + p.parent.relative_to(REPO_ROOT).as_posix() for p in _dockerfiles_under_docker()
    }
    assert len(expected) >= 18, (
        f"la guarda dejó de encontrar los Dockerfiles de docker/ (vio {len(expected)})"
    )
    missing = sorted(expected - covered)
    assert not missing, "directorios Docker sin entrada en dependabot.yml: " + ", ".join(missing)


def test_dependabot_groups_prs_and_caps_the_volume(dependabot: dict[str, Any]) -> None:
    """Riesgo 1 de prod-11: 13 pyprojects + 2 npm + 20 Dockerfiles + actions pueden
    generar decenas de PRs semanales. Cada entrada agrupa y limita el volumen."""
    problems: list[str] = []
    updates = [u for u in (dependabot.get("updates") or []) if isinstance(u, dict)]
    assert updates, "dependabot.yml no declara ninguna entrada `updates`"
    for u in updates:
        label = f"{u.get('package-ecosystem')}:{u.get('directory')}"
        if not u.get("groups"):
            problems.append(f"{label}: sin `groups` (un PR por dependencia)")
        limit = u.get("open-pull-requests-limit")
        if not isinstance(limit, int) or limit > 5:
            problems.append(f"{label}: open-pull-requests-limit={limit!r} (max 5)")
    assert not problems, "dependabot sin control de volumen:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# task_actions_sha_03 — actions pineadas por SHA de commit
# ---------------------------------------------------------------------------

_SHA40 = re.compile(r"@[0-9a-f]{40}$")


def test_actions_pinned_by_commit_sha(workflows: dict[str, dict[str, Any]]) -> None:
    """Ningún `uses:` puede referenciar un tag mutable.

    Un tag de GitHub Action es una referencia MUTABLE: quien controle el repo de
    la action (o le roben el token) puede reapuntar `v5` a un commit que exfiltre
    los secretos de CI. El SHA de 40 caracteres es la única referencia inmutable.
    """
    seen = 0
    offenders: list[str] = []
    for name, data in workflows.items():
        for job_name, job in _jobs(data).items():
            for ref in _uses(job):
                if ref.startswith(("./", "docker://")):
                    continue  # action local / imagen: no hay tag de repo que pinear
                seen += 1
                if not _SHA40.search(ref):
                    offenders.append(f"{name}:{job_name}: uses: {ref}")
    assert seen >= 17, f"la guarda dejó de encontrar los `uses:` (vio {seen})"
    assert not offenders, "actions pineadas por tag MUTABLE:\n" + "\n".join(offenders)


def test_pinned_actions_carry_a_readable_tag_comment() -> None:
    """Cada `uses: owner/repo@<sha40>` lleva el tag legible en un comentario.

    Sin él nadie sabe qué versión es, y Dependabot (que reescribe el SHA y el
    comentario juntos) pierde la referencia humana.
    """
    seen = 0
    offenders: list[str] = []
    for path in _workflow_files():
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            m = re.search(r"uses:\s*(\S+)@([0-9a-f]{40})(.*)$", raw)
            if not m:
                continue
            seen += 1
            if not re.search(r"#\s*v?\d", m.group(3)):
                offenders.append(f"{path.name}:{lineno}: {m.group(1)} sin comentario `# vN`")
    assert seen >= 17, f"la guarda dejó de encontrar los `uses:` pineados (vio {seen})"
    assert not offenders, "SHAs sin tag legible:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# task_composer_checksum_04 — nada de `curl | php` sin verificar
# ---------------------------------------------------------------------------


def test_no_unverified_composer_installer() -> None:
    """Ningún Dockerfile puede canalizar el instalador de Composer a `php`.

    `curl -sS https://getcomposer.org/installer | php` es ejecución remota de
    código SIN verificar en la construcción de la imagen: un getcomposer.org
    comprometido (o un MITM sobre el build) ejecuta lo que quiera dentro de las
    imágenes que ejecutan código no confiable (Principio Rector 2).
    """
    offenders: list[str] = []
    for dockerfile in _dockerfiles_under_docker():
        text = dockerfile.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Los comentarios que EXPLICAN por qué se retiró el patrón no son
            # infractores; una continuación de `RUN` nunca empieza por `#`.
            if line.lstrip().startswith("#"):
                continue
            if "getcomposer.org/installer" in line and re.search(r"\|\s*php", line):
                rel = dockerfile.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, "instalador de Composer canalizado a php sin checksum: " + ", ".join(
        offenders
    )


def test_php_runtimes_get_composer_from_a_pinned_image() -> None:
    """Los dos runtimes PHP traen Composer de una etapa pineada por digest.

    Contraparte POSITIVA del test anterior: comprueba que Composer sigue
    ENTRANDO en la imagen (no que simplemente desapareció el `curl`) y que su
    procedencia es inmutable. Se exige la forma `FROM composer:<tag>@sha256:… AS
    <alias>` + `COPY --from=<alias>` y no un `COPY --from=composer:…@sha256:`
    en línea: Dependabot (ecosistema docker) solo parsea las líneas `FROM`, así
    que el digest en línea se congelaría sin vía de refresco.
    """
    seen = 0
    problems: list[str] = []
    for slug in ("php-phpunit", "php-pest"):
        dockerfile = DOCKER_DIR / "agent-runtimes" / slug / "Dockerfile"
        if not dockerfile.is_file():
            continue
        seen += 1
        text = dockerfile.read_text(encoding="utf-8")
        stage = re.search(
            r"^FROM\s+composer:[^\s@]+@sha256:[0-9a-f]{64}\s+AS\s+(\S+)", text, re.M | re.I
        )
        if stage is None:
            problems.append(f"{slug}: sin etapa `FROM composer:<tag>@sha256:… AS <alias>`")
            continue
        alias = re.escape(stage.group(1))
        if not re.search(rf"^COPY\s+--from={alias}\s+\S*composer\s+\S+composer\s*$", text, re.M):
            problems.append(f"{slug}: la etapa '{stage.group(1)}' existe pero nadie copia composer")
    assert seen == 2, f"la guarda dejó de encontrar los runtimes PHP (vio {seen})"
    assert not problems, "runtimes PHP sin Composer verificado:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# task_pip_audit_05 / task_npm_audit_06 / task_trivy_07 — el job security-scan
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return _load_yaml(WORKFLOWS_DIR / "ci.yml")


@pytest.fixture(scope="module")
def security_scan(ci: dict[str, Any]) -> dict[str, Any]:
    job = _jobs(ci).get(SECURITY_SCAN_JOB)
    assert job is not None, f"ci.yml no tiene el job '{SECURITY_SCAN_JOB}' (task_pip_audit_05)"
    return job


def _editable_targets(job: dict[str, Any]) -> set[str]:
    return set(re.findall(r'pip install -e "?([^"\s\[]+)', _run_text(job)))


def test_security_scan_runs_pip_audit(security_scan: dict[str, Any]) -> None:
    """El job debe INVOCAR pip-audit, no solo instalarlo.

    La primera versión de esta guarda buscaba la subcadena `pip-audit` en todo
    el `run:`, y el ciclo de mutación la pilló pasando con el comando de
    auditoría sustituido por un `echo`: el `pip install pip-audit` de la línea
    de arriba ya contenía la subcadena. Ahora se exige una línea que EMPIECE
    por `pip-audit` — la invocación real.
    """
    invocations = _pip_audit_invocations(security_scan)
    assert invocations, (
        f"el job '{SECURITY_SCAN_JOB}' debe EJECUTAR pip-audit sobre el entorno "
        "instalado (una línea `pip-audit …`, no solo instalarlo)"
    )
    assert any("--skip-editable" in inv for inv in invocations), (
        "pip-audit debe correr con --skip-editable: las 13 distribuciones locales "
        "se instalan con `pip install -e`, no existen en PyPI y no son auditables. "
        f"Invocaciones vistas: {invocations}"
    )


def test_pip_audit_does_not_combine_strict_with_skip_editable(
    security_scan: dict[str, Any],
) -> None:
    """`--strict` junto a `--skip-editable` es un rojo permanente, no una guarda.

    `--strict` significa «falla si la recolección falla en CUALQUIER
    dependencia», y una dependencia OMITIDA cuenta como fallo
    (`pip_audit/_cli.py`: ``if args.strict: _fatal(f"{spec.name}: {spec.skip_reason}")``).
    Con las dos banderas el paso moría siempre en la primera editable por orden
    alfabético —«agent-runtime: distribution marked as editable»— sin auditar ni
    un paquete: un rojo que no era una vulnerabilidad y que tapó durante semanas
    las que sí había.

    Lo que `--strict` aportaba —no dar por buena una auditoría incompleta— lo
    comprueba `scripts/check_pip_audit_report.py`, que sólo tolera las omisiones
    cuyo motivo es «editable». Esta guarda existe para que nadie "restaure" la
    bandera creyendo que endurece algo.
    """
    offenders = [
        inv
        for inv in _pip_audit_invocations(security_scan)
        if "--skip-editable" in inv and re.search(r"(^|\s)(--strict|-S)(\s|$)", inv)
    ]
    assert not offenders, (
        "pip-audit no puede combinar --strict con --skip-editable: la omisión de "
        "las editables locales se vuelve fatal y el paso muere sin auditar nada. "
        f"Invocaciones infractoras: {offenders}"
    )


def test_pip_audit_report_is_verified_by_the_checker(
    security_scan: dict[str, Any],
) -> None:
    """El JSON de pip-audit tiene que pasar por el verificador.

    Sin `--strict`, quien decide si la auditoría fue COMPLETA es
    `scripts/check_pip_audit_report.py`: si el paso deja de invocarlo, una
    dependencia que pip-audit no pudo resolver (fuera de PyPI, red bloqueada,
    sin versión) se omitiría con un simple aviso en el log y el verde no diría
    nada sobre ella.
    """
    run_text = _run_text(security_scan)
    assert "scripts/check_pip_audit_report.py" in run_text, (
        f"el job '{SECURITY_SCAN_JOB}' debe verificar el informe de pip-audit con "
        "`python scripts/check_pip_audit_report.py <json>`: es lo que sustituye a "
        "--strict y lo único que impide que una auditoría incompleta pase por verde"
    )
    assert (REPO_ROOT / "scripts" / "check_pip_audit_report.py").is_file(), (
        "falta scripts/check_pip_audit_report.py, que el job invoca"
    )
    assert any(
        "--format=json" in inv and "--output" in inv
        for inv in _pip_audit_invocations(security_scan)
    ), (
        "pip-audit debe escribir el informe con `--format=json --output <fichero>` "
        "para que el verificador pueda leerlo"
    )


def test_pip_audit_audits_the_same_tree_the_unit_job_installs(
    ci: dict[str, Any], security_scan: dict[str, Any]
) -> None:
    """pip-audit debe auditar lo que CI instala DE VERDAD, transitivas incluidas.

    Si `security-scan` instala menos distribuciones que `test-unit`, audita un
    árbol de dependencias más pequeño que el que se ejecuta: señal incompleta con
    aspecto de verde.
    """
    unit = _jobs(ci).get("test-unit")
    assert unit is not None, "ci.yml no tiene el job 'test-unit'"
    unit_targets = _editable_targets(unit)
    assert len(unit_targets) >= 10, (
        f"la guarda dejó de encontrar los editable installs de test-unit (vio {len(unit_targets)})"
    )
    missing = sorted(unit_targets - _editable_targets(security_scan))
    assert not missing, (
        f"'{SECURITY_SCAN_JOB}' no instala lo que test-unit sí instala, así que "
        "pip-audit audita un árbol incompleto: " + ", ".join(missing)
    )


def test_security_scan_runs_npm_audit_on_both_surfaces(security_scan: dict[str, Any]) -> None:
    steps = _steps(security_scan)
    problems: list[str] = []
    for surface in NPM_SURFACES:
        hit = any(
            (s.get("working-directory") == surface or surface in (s.get("run") or ""))
            and "npm audit" in (s.get("run") or "")
            for s in steps
        )
        if not hit:
            problems.append(surface)
    assert not problems, f"'{SECURITY_SCAN_JOB}' no corre `npm audit` en: " + ", ".join(problems)


def test_npm_audit_uses_the_agreed_threshold(security_scan: dict[str, Any]) -> None:
    """`npm audit --omit=dev --audit-level=high`: sin `--audit-level` el comando
    falla con cualquier `low` y el gate muere por fatiga de alertas; sin
    `--omit=dev` audita la toolchain de build, que no se despliega."""
    offenders: list[str] = []
    for step in _steps(security_scan):
        run = step.get("run") or ""
        for line in run.splitlines():
            if "npm audit" not in line:
                continue
            if "--audit-level=high" not in line or "--omit=dev" not in line:
                offenders.append(line.strip())
    assert not offenders, "`npm audit` sin --omit=dev/--audit-level=high:\n" + "\n".join(offenders)


# Jobs que construyen imágenes y NO necesitan su propio Trivy, con el motivo.
TRIVY_EXEMPT: dict[tuple[str, str], str] = {
    ("ci.yml", "test-integration"): (
        "construye agent-runtime:v1 / browser-runtime:v1 solo para que el e2e no "
        "se salte; esos MISMOS Dockerfiles los escanea ci.yml:build-images"
    ),
    ("install-e2e.yml", "install-e2e"): (
        "construye las SEIS de plataforma sólo para que el instalador tenga algo "
        "que bajar (no hay release publicada: platform_images.json trae `digests` "
        "vacío) y las sirve desde un registro local que muere con el runner: no "
        "publica nada. Esos MISMOS Dockerfiles ya los escanea Trivy en cada push "
        "—api-server en ci.yml:build-images— y en cada release —workers, "
        "orchestrator, notification-dispatcher, watchdog en release-images.yml:"
        "backend, y admin-panel en release-images.yml:admin-panel—. Repetirlo "
        "aquí no añadiría cobertura y sí ruido: teñiría de rojo el veredicto "
        "«¿instala?» por una CVE que ya bloquea en su propio gate"
    ),
}


def _builds_images(job: dict[str, Any]) -> bool:
    if any("docker/build-push-action" in u for u in _uses(job)):
        return True
    return bool(re.search(r"\bdocker build\b", _run_text(job)))


def test_image_building_jobs_are_scanned_by_trivy(workflows: dict[str, dict[str, Any]]) -> None:
    """Todo job que construya una imagen lleva un paso `trivy-action`.

    Descubrimiento, no lista blanca: un job nuevo que construya imágenes sin
    escanearlas pone este test en rojo. Las excepciones son explícitas y llevan
    motivo (TRIVY_EXEMPT).
    """
    seen = 0
    offenders: list[str] = []
    for name, data in workflows.items():
        for job_name, job in _jobs(data).items():
            if not _builds_images(job):
                continue
            if (name, job_name) in TRIVY_EXEMPT:
                continue
            seen += 1
            if not any("trivy-action" in u for u in _uses(job)):
                offenders.append(f"{name}:{job_name}")
    assert seen >= 3, f"la guarda dejó de encontrar los jobs que construyen imágenes (vio {seen})"
    assert not offenders, "jobs que construyen imágenes sin escanearlas: " + ", ".join(offenders)


def test_trivy_steps_gate_on_high_and_critical(workflows: dict[str, dict[str, Any]]) -> None:
    """Cada paso Trivy pide HIGH,CRITICAL + exit-code 1 + ignore-unfixed.

    `exit-code: 0` (el default de la action) sería un escáner que informa y no
    bloquea nunca: una guarda que no puede fallar. `ignore-unfixed: true` evita
    el riesgo 2 (CVEs sin fix en las bases `-slim`/`alpine` bloqueando PRs
    ajenos al problema).
    """
    seen = 0
    problems: list[str] = []
    for name, data in workflows.items():
        for job_name, job in _jobs(data).items():
            for step in _steps(job):
                if "trivy-action" not in (step.get("uses") or ""):
                    continue
                seen += 1
                where = f"{name}:{job_name}:{step.get('name', '?')}"
                cfg = step.get("with") or {}
                sev = str(cfg.get("severity", ""))
                if "HIGH" not in sev or "CRITICAL" not in sev:
                    problems.append(f"{where}: severity={sev!r} (falta HIGH,CRITICAL)")
                if str(cfg.get("exit-code", "0")) != "1":
                    problems.append(f"{where}: exit-code={cfg.get('exit-code')!r} (debe ser '1')")
                if str(cfg.get("ignore-unfixed", "")).lower() != "true":
                    problems.append(f"{where}: falta ignore-unfixed: true")
    assert seen >= 3, f"la guarda dejó de encontrar pasos trivy-action (vio {seen})"
    assert not problems, "pasos Trivy mal configurados:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# task_sca_gate_08 — política de excepciones con justificación y fecha
# ---------------------------------------------------------------------------

# La marca de revisión debe estar en una línea de comentario PROPIA
# (``# review: 2026-09-30``), no en el ejemplo indentado de la cabecera
# (``#     # review: …``). La primera versión de esta guarda usaba un
# `search` sobre todos los comentarios previos y el ciclo de mutación la pilló
# pasando VACÍAMENTE: cualquier entrada colada tras la cabecera heredaba la
# fecha del ejemplo documental. Trampa nº4 de verificar-antes-de-implementar.
_REVIEW_LINE = re.compile(r"^#\s*review:\s*\d{4}-\d{2}-\d{2}\s*$")
# Ventana de comentarios que se considera "la justificación de ESTA entrada".
_JUSTIFICATION_WINDOW = 8
# Separadores y líneas vacías de comentario: no cuentan como justificación.
_FILLER = re.compile(r"^#[\s\-=*_]*$")


def _ignore_entries(path: Path) -> list[tuple[int, str, list[str]]]:
    """(lineno, entrada, comentarios previos) de un fichero de ignore."""
    out: list[tuple[int, str, list[str]]] = []
    pending: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            pending = []
            continue
        if line.startswith("#"):
            pending.append(line)
            continue
        out.append((lineno, line, list(pending)))
        pending = []
    return out


@pytest.mark.parametrize("path", [TRIVYIGNORE, PIP_AUDIT_IGNORE])
def test_sca_ignore_lists_exist_and_document_every_exception(path: Path) -> None:
    """Las ignore-lists existen y CADA excepción lleva justificación + `review:`.

    Sin fecha de revisión obligatoria una supresión es permanente de facto, y el
    gate muere por acumulación silenciosa (Decisión clave «Política de
    excepciones SCA» de prod-11).
    """
    assert path.is_file(), (
        f"{path.name} debe existir (aunque esté vacío de excepciones): es el único "
        "sitio donde una supresión queda versionada y revisable"
    )
    problems: list[str] = []
    for lineno, entry, comments in _ignore_entries(path):
        window = comments[-_JUSTIFICATION_WINDOW:]
        if not any(_REVIEW_LINE.match(c) for c in window):
            problems.append(f"{path.name}:{lineno}: '{entry}' sin `# review: YYYY-MM-DD` propio")
            continue
        justification = " ".join(
            c for c in window if not _FILLER.match(c) and not _REVIEW_LINE.match(c)
        )
        if len(justification) < 40:
            problems.append(f"{path.name}:{lineno}: '{entry}' sin justificación legible")
    assert not problems, "excepciones SCA sin documentar:\n" + "\n".join(problems)


def test_security_scan_declares_its_gate_mode(security_scan: dict[str, Any]) -> None:
    """El modo del job (informe vs gate) es EXPLÍCITO, no un olvido.

    prod-11 lo arranca en modo informe (`continue-on-error: true`) durante la
    semana de triage y lo convierte en check obligatorio en task_sca_gate_08.
    Este test no decide el modo: exige que esté declarado a la vista, para que
    el paso a gate sea un cambio de una línea y no una arqueología.
    """
    assert "continue-on-error" in security_scan, (
        f"el job '{SECURITY_SCAN_JOB}' debe declarar `continue-on-error` "
        "explícitamente (true = modo informe de la semana de triage; "
        "false = gate de task_sca_gate_08)"
    )


def test_security_scan_is_an_enforcing_gate(security_scan: dict[str, Any]) -> None:
    """Y el modo declarado es GATE, no informe (task_sca_gate_08).

    El job nació con `continue-on-error: true` para no bloquear el día 1 todos
    los PRs con el backlog heredado de CVEs. Ese backlog está **vacío**, medido
    el 2026-08-19 en las tres superficies del job:

      * `pip-audit --skip-editable` → *No known vulnerabilities found* (exit 0);
      * `npm audit --omit=dev --audit-level=high` en `apps/admin-panel` →
        *found 0 vulnerabilities* (exit 0);
      * lo mismo en `apps/installer` (exit 0).

    Y las dos listas de excepciones (`.trivyignore`, `.pip-audit-ignore`) no
    tienen ni una entrada vigente, así que el verde no se apoya en ninguna
    supresión.

    Con el backlog a cero, dejar el job en modo informe ya no protege de nada:
    sólo hace que la próxima vulnerabilidad entre en `master` con el check en
    verde-tachado que nadie lee. Esta guarda impide que el flip se deshaga en
    silencio — volver a `true` exige tocar este test y explicar por qué.

    Lo que esta guarda NO puede comprobar: que el check esté en los *required
    status checks* de la protección de rama. Eso vive en la configuración de
    GitHub, no en el repo, y es el paso que sigue siendo del operador
    (nombre exacto del check: el `name:` del job, no su id).
    """
    mode = security_scan["continue-on-error"]
    assert mode is False, (
        f"el job '{SECURITY_SCAN_JOB}' sigue en modo informe "
        f"(continue-on-error: {mode!r}): un hallazgo SCA no rompe el build. "
        "El backlog que justificaba el modo informe está vacío desde el "
        "2026-08-19 (pip-audit y npm audit en exit 0 en las tres superficies, "
        "sin excepciones vigentes en .trivyignore ni .pip-audit-ignore). Si hay "
        "un motivo nuevo para volver al modo informe, escríbelo aquí y en "
        "docs/06-runbooks/triage-vulnerabilidades.md §6."
    )


# ---------------------------------------------------------------------------
# task_uv_lock_09 / task_ci_lock_10 — lockfile y builds reproducibles
# ---------------------------------------------------------------------------


def test_constraints_file_is_versioned_and_pins_exactly() -> None:
    """`constraints.txt` existe y TODAS sus líneas pinean con `==`.

    Es el artefacto que hace reproducible el `pip install -e` de CI y del
    agent-runtime. Una línea con rango dentro de un fichero de constraints es
    una resolución que vuelve a flotar.
    """
    assert CONSTRAINTS.is_file(), "falta constraints.txt en la raíz (task_uv_lock_09)"
    pins = 0
    problems: list[str] = []
    for lineno, raw in enumerate(CONSTRAINTS.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if "==" not in line:
            problems.append(f"constraints.txt:{lineno}: '{line}' no pinea con ==")
            continue
        if re.match(r"^[A-Za-z0-9._-]+\[", line):
            problems.append(
                f"constraints.txt:{lineno}: '{line}' lleva extras — pip rechaza "
                "un fichero de constraints con extras"
            )
        pins += 1
    assert pins >= 50, f"la guarda dejó de encontrar pines en constraints.txt (vio {pins})"
    assert not problems, "constraints.txt no reproducible:\n" + "\n".join(problems)


def test_root_dev_group_mirrors_requirements_dev() -> None:
    """El grupo `dev` del pyproject raíz y `requirements-dev.txt` dicen lo mismo.

    Hay dos listas del mismo toolchain porque cumplen papeles distintos:
    `requirements-dev.txt` es lo que instalan los scripts de bootstrap, y el
    grupo `dev` es lo que entra en la resolución de `uv.lock`. Si se
    desincronizan, el linter con el que se valida un commit deja de ser el que
    el lock pinea — y nadie se entera.
    """
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    group = {
        req.replace(" ", "").lower() for req in (data.get("dependency-groups") or {}).get("dev", [])
    }
    from_file = {
        line.split("#", 1)[0].replace(" ", "").lower()
        for line in (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }
    assert len(from_file) >= 8, (
        f"la guarda dejó de encontrar el toolchain en requirements-dev.txt (vio {len(from_file)})"
    )
    assert group == from_file, (
        "el grupo `dev` de pyproject.toml y requirements-dev.txt divergen.\n"
        f"solo en pyproject: {sorted(group - from_file)}\n"
        f"solo en requirements-dev.txt: {sorted(from_file - group)}"
    )


def test_uv_lock_is_versioned() -> None:
    assert (REPO_ROOT / "uv.lock").is_file(), (
        "falta uv.lock: sin lockfile la resolución de dependencias cambia entre "
        "builds y pip-audit/Dependabot dan señal sobre un árbol que no es el que se despliega"
    )


def test_ci_installs_python_deps_with_constraints(workflows: dict[str, dict[str, Any]]) -> None:
    """Ningún `pip install -e` de NINGÚN workflow puede resolver libre.

    Sin `-c constraints.txt` cada run de CI resuelve las transitivas por su
    cuenta: el verde de hoy no dice nada del árbol de mañana (quality-5). Se
    recorren todos los workflows, no solo ci.yml — eval-on-prompt-change.yml
    también instala la api-server y quedaría con otra resolución.
    """
    seen = 0
    offenders: list[str] = []
    for name, data in workflows.items():
        for job_name, job in _jobs(data).items():
            for step in _steps(job):
                for line in (step.get("run") or "").splitlines():
                    if "pip install -e" not in line:
                        continue
                    seen += 1
                    if "-c constraints.txt" not in line:
                        offenders.append(f"{name}:{job_name}: {line.strip()}")
    assert seen >= 20, f"la guarda dejó de encontrar los editable installs de CI (vio {seen})"
    assert not offenders, "pip install -e sin constraints:\n" + "\n".join(offenders)


def test_ci_checks_the_lock_for_drift(ci: dict[str, Any]) -> None:
    """Un paso de CI corre `uv lock --check`: si alguien cambia un rango de un
    pyproject.toml sin regenerar el lock, el lock miente y CI lo dice."""
    text = "\n".join(_run_text(job) for job in _jobs(ci).values())
    assert "uv lock --check" in text, (
        "ci.yml debe correr `uv lock --check` para detectar drift entre los "
        "pyproject.toml y uv.lock (task_ci_lock_10 c)"
    )


def test_agent_runtime_dockerfile_installs_with_constraints() -> None:
    """El agent-runtime instala con `-c constraints.txt`.

    Es la imagen donde corre el código de los agentes: si sus dependencias
    resuelven libres, dos builds del mismo commit dan dos imágenes distintas
    (test humano human_prod11_03).
    """
    dockerfile = DOCKER_DIR / "agent-runtimes" / "agent-runtime" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert re.search(r"^COPY\s+constraints\.txt\b", text, re.M), (
        "el Dockerfile del agent-runtime debe COPYar constraints.txt (contexto = raíz del repo)"
    )
    seen = 0
    offenders: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        # Los comentarios que MENCIONAN un `pip install` no instalan nada; una
        # continuación de `RUN` nunca empieza por `#`.
        if raw.lstrip().startswith("#"):
            continue
        if not re.search(r"\bpip install\b", raw):
            continue
        if "--upgrade pip" in raw:
            continue
        seen += 1
        if "-c /constraints.txt" not in raw and "-c constraints.txt" not in raw:
            offenders.append(f"{lineno}: {raw.strip()}")
    assert seen >= 4, f"la guarda dejó de encontrar los pip install del runtime (vio {seen})"
    assert not offenders, "pip install del agent-runtime sin constraints:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# task_digest_pin_11 — bases pineadas por digest
# ---------------------------------------------------------------------------

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def test_docker_bases_pinned_by_digest() -> None:
    """Todo `FROM` externo bajo `docker/` lleva `@sha256:`.

    Un tag de imagen es mutable: `python:3.12-slim` de hoy no es el de mañana, y
    son precisamente las 16 imágenes de runtime donde el Principio Rector 2
    deposita el aislamiento del código NO confiable.
    """
    seen = 0
    offenders: list[str] = []
    for dockerfile in _dockerfiles_under_docker():
        rel = dockerfile.relative_to(REPO_ROOT).as_posix()
        for lineno, ref in _from_refs(dockerfile):
            seen += 1
            if not _DIGEST.search(ref):
                offenders.append(f"{rel}:{lineno}: FROM {ref}")
    assert seen >= 20, f"la guarda dejó de encontrar los FROM de docker/ (vio {seen})"
    assert not offenders, "FROM sin digest bajo docker/:\n" + "\n".join(offenders)


def test_digest_pinned_bases_keep_their_tag_readable() -> None:
    """`FROM python:3.12-slim@sha256:…`: el tag va DENTRO de la referencia.

    Un `FROM python@sha256:…` sin tag es inauditable — nadie sabe qué versión
    corre — y Dependabot no puede proponer la siguiente.
    """
    seen = 0
    offenders: list[str] = []
    for dockerfile in _dockerfiles_under_docker():
        rel = dockerfile.relative_to(REPO_ROOT).as_posix()
        for lineno, ref in _from_refs(dockerfile):
            if not _DIGEST.search(ref):
                continue
            seen += 1
            name = ref.split("@", 1)[0]
            if ":" not in name.rsplit("/", 1)[-1]:
                offenders.append(f"{rel}:{lineno}: FROM {ref} (sin tag legible)")
    assert seen >= 20, f"la guarda dejó de encontrar FROM con digest (vio {seen})"
    assert not offenders, "FROM con digest pero sin tag:\n" + "\n".join(offenders)
