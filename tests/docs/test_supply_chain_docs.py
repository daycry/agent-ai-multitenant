"""Guardas de la DOCUMENTACIÓN de la cadena de suministro (plan prod-11).

`tests/unit/test_supply_chain_config.py` verifica la mecánica: que las actions
van pineadas por SHA, que los `FROM` llevan digest, que `security-scan` invoca
pip-audit y `npm audit`, que las ignore-lists llevan fecha de revisión. Este
módulo verifica la otra mitad, la que decide si el gate sobrevive: que **una
persona sepa qué hacer** cuando el escáner se pone rojo.

Los dos documentos que cubre:

* `docs/06-runbooks/triage-vulnerabilidades.md` — el procedimiento
  (`task_runbook_13`).
* `docs/04-reference/cadena-suministro.md` — la referencia de qué se escanea,
  dónde y con qué umbral (`task_runbook_13`, resumen en 04-reference).

Y el ADR de distribución de imágenes runtime (`task_registry_adr_12`).

El invariante que de verdad importa aquí es de **descubrimiento**, no de
subcadena: la lista de escáneres y de ficheros de excepción se deriva de los
workflows y de la raíz del repo, no se escribe a mano en el test. Añadir un
escáner a CI —o un fichero de ignore nuevo— sin documentarlo pone esto en rojo.
Cada guarda afirma además que encontró algo (trampa nº4 de
`docs/03-guides/verificar-antes-de-implementar.md`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
RUNBOOK = REPO_ROOT / "docs" / "06-runbooks" / "triage-vulnerabilidades.md"
REFERENCE = REPO_ROOT / "docs" / "04-reference" / "cadena-suministro.md"
REFERENCE_INDEX = REPO_ROOT / "docs" / "04-reference" / "README.md"
ADR_DIR = REPO_ROOT / "docs" / "05-architecture-decisions"
REGISTRY_ADR = ADR_DIR / "0148-distribucion-imagenes-runtime-por-digest.md"

# Escáneres que este repo PUEDE llegar a tener, con la marca que los delata en
# un workflow y el nombre con el que deben aparecer documentados. La guarda solo
# exige documentar los que están REALMENTE configurados (se descubren abajo):
# la lista es el vocabulario, no la expectativa.
_SCANNER_FINGERPRINTS: dict[str, tuple[str, ...]] = {
    "pip-audit": ("pip-audit",),
    "npm audit": ("npm audit",),
    "Trivy": ("trivy-action", "trivy image"),
    "uv lock --check": ("uv lock --check",),
    "Grype": ("grype",),
    "osv-scanner": ("osv-scanner",),
    "safety": ("safety check", "safety scan"),
}


def _workflow_text() -> str:
    files = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    assert files, f"no hay workflows bajo {WORKFLOWS_DIR}"
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def _configured_scanners() -> set[str]:
    """Escáneres que los workflows invocan DE VERDAD, por descubrimiento."""
    text = _workflow_text().lower()
    return {
        name
        for name, marks in _SCANNER_FINGERPRINTS.items()
        if any(mark.lower() in text for mark in marks)
    }


def _ignore_files() -> set[str]:
    """Ficheros de excepción versionados en la raíz del repo."""
    return {
        p.name
        for p in REPO_ROOT.glob(".*ignore")
        if p.is_file() and p.name not in {".gitignore", ".dockerignore", ".prettierignore"}
    }


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    _, raw, body = text.split("---", 2)
    data = yaml.safe_load(raw)
    return (data if isinstance(data, dict) else {}), body


# ---------------------------------------------------------------------------
# task_runbook_13 — el runbook de triage
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runbook_text() -> str:
    assert RUNBOOK.is_file(), (
        "falta docs/06-runbooks/triage-vulnerabilidades.md (task_runbook_13): sin "
        "criterio escrito, un gate SCA muere en dos semanas por fatiga de alertas"
    )
    return RUNBOOK.read_text(encoding="utf-8")


def test_runbook_triage_has_frontmatter_and_says_when_to_use_it(runbook_text: str) -> None:
    """Frontmatter YAML + el disparador, como el resto del corpus de runbooks."""
    frontmatter, body = _split_frontmatter(runbook_text)
    missing = {"title", "docs_language", "audience"} - set(frontmatter)
    assert not missing, f"triage-vulnerabilidades.md: frontmatter sin {sorted(missing)}"
    assert "Cuándo usarlo" in body, (
        "el runbook debe abrir diciendo CUÁNDO se usa: quien llega aquí lo hace "
        "con un job en rojo delante, no leyendo por gusto"
    )


def test_runbook_triage_documents_every_scanner_configured_in_ci(runbook_text: str) -> None:
    """Cada escáner que CI invoca aparece nombrado en el runbook.

    Descubrimiento, no lista blanca: si mañana alguien añade Grype o osv-scanner
    a un workflow y no lo documenta, quien reciba el rojo no sabrá qué hacer con
    él. Esta guarda lo pone en rojo antes.
    """
    configured = _configured_scanners()
    assert len(configured) >= 4, (
        "la guarda dejó de encontrar los escáneres de los workflows " f"(vio {sorted(configured)})"
    )
    lowered = runbook_text.lower()
    missing = sorted(name for name in configured if name.lower() not in lowered)
    # Mensaje a una variable: como concatenación dentro del `assert`, black y
    # ruff-format la parten distinto y se pelean en bucle (gotcha documentado).
    sin_documentar = ", ".join(missing)
    assert (
        not missing
    ), f"escáneres configurados en CI y NO documentados en el runbook: {sin_documentar}"


def test_runbook_triage_documents_every_versioned_ignore_file(runbook_text: str) -> None:
    """Cada fichero de excepciones de la raíz está nombrado en la política.

    Una supresión que vive en un fichero que el runbook no menciona es una
    supresión que nadie revisará nunca.
    """
    files = _ignore_files()
    assert files >= {
        ".trivyignore",
        ".pip-audit-ignore",
    }, f"la guarda dejó de encontrar las ignore-lists de la raíz (vio {sorted(files)})"
    missing = sorted(name for name in files if name not in runbook_text)
    assert not missing, "ficheros de excepción no documentados en el runbook: " + ", ".join(missing)


def test_runbook_triage_pins_the_mandatory_exception_format(runbook_text: str) -> None:
    """El formato `# review: YYYY-MM-DD` y el calendario de revisión son explícitos.

    Es el único freno contra que la lista de excepciones crezca para siempre; la
    guarda `test_sca_ignore_lists_exist_and_document_every_exception` lo exige en
    los ficheros, y aquí se exige que el humano sepa por qué.
    """
    assert re.search(r"#\s*review:\s*YYYY-MM-DD", runbook_text), (
        "el runbook debe fijar el formato literal `# review: YYYY-MM-DD` de las "
        "entradas de las ignore-lists"
    )
    assert "Calendario de revisión" in runbook_text, (
        "sin calendario de revisión, una fecha obligatoria en cada entrada no "
        "sirve de nada: nadie mira si vencieron"
    )


def test_runbook_triage_explains_how_the_gate_becomes_mandatory(runbook_text: str) -> None:
    """El paso de modo informe a gate está escrito, y dice que es humano.

    `task_sca_gate_08` no lo puede cerrar un agente: quitar `continue-on-error` y
    tocar branch protection pide permisos de administración del repo. El runbook
    es donde queda dicho qué falta exactamente.
    """
    assert "continue-on-error" in runbook_text, (
        "el runbook debe nombrar el `continue-on-error` del job para que el paso a "
        "gate sea un cambio localizable"
    )
    assert "branch protection" in runbook_text.lower(), (
        "el runbook debe decir que el gate se cierra en branch protection (y que "
        "eso exige permisos de administración)"
    )


def test_runbook_triage_forbids_lowering_the_threshold(runbook_text: str) -> None:
    """Prohibición explícita del atajo que mata cualquier gate SCA.

    Bajar `--audit-level`, quitar la superficie del job o subir el `severity` de
    Trivy resuelve el rojo sin resolver la vulnerabilidad. Que esté prohibido por
    escrito es lo que convierte las guardas estáticas en política.
    """
    assert re.search(r"[Nn]unca.*(umbral|superficie)", runbook_text, re.S), (
        "el runbook debe prohibir explícitamente resolver un rojo bajando el "
        "umbral del escáner o quitando la superficie del job"
    )


# ---------------------------------------------------------------------------
# task_runbook_13 — el resumen en 04-reference
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reference_text() -> str:
    assert REFERENCE.is_file(), (
        "falta docs/04-reference/cadena-suministro.md (task_runbook_13): el plan "
        "pide un resumen de referencia de qué se escanea, dónde y con qué umbral"
    )
    return REFERENCE.read_text(encoding="utf-8")


def test_reference_documents_every_scanner_with_its_threshold(reference_text: str) -> None:
    configured = _configured_scanners()
    assert len(configured) >= 4, f"la guarda dejó de encontrar los escáneres (vio {configured})"
    lowered = reference_text.lower()
    missing = sorted(name for name in configured if name.lower() not in lowered)
    assert not missing, "escáneres sin fila en la referencia: " + ", ".join(missing)
    for threshold in ("HIGH,CRITICAL", "--audit-level=high"):
        assert threshold in reference_text, f"la referencia no fija el umbral `{threshold}`"


def test_reference_names_the_reproducibility_and_immutability_artifacts(
    reference_text: str,
) -> None:
    """La referencia cubre las dos mitades, no solo el escaneo.

    De nada sirve escanear si el árbol escaneado no es el que se despliega
    (`uv.lock` + `constraints.txt`) o si la base de la imagen puede cambiar bajo
    los pies (`@sha256:` en los `FROM`, SHA de commit en las actions).
    """
    for artifact in ("uv.lock", "constraints.txt", "@sha256:", "dependabot.yml"):
        assert artifact in reference_text, f"la referencia no menciona `{artifact}`"


def test_reference_and_runbook_link_to_each_other(reference_text: str, runbook_text: str) -> None:
    assert "triage-vulnerabilidades.md" in reference_text, (
        "la referencia debe enviar al runbook: es el documento que dice QUÉ HACER "
        "con un rojo, y quien llega a la referencia con un job roto lo necesita"
    )
    assert "cadena-suministro.md" in runbook_text, "el runbook debe enlazar la referencia"


def test_reference_is_indexed_in_the_reference_readme() -> None:
    """Un documento fuera del índice de 04-reference es un documento invisible."""
    index = REFERENCE_INDEX.read_text(encoding="utf-8")
    assert "cadena-suministro.md" in index, (
        "docs/04-reference/README.md no indexa cadena-suministro.md: la carpeta se "
        "navega por ese índice, no por `ls`"
    )


def test_reference_records_the_known_residual_backlog(reference_text: str) -> None:
    """El backlog heredado que impide cerrar el gate está DICHO, no escondido.

    A 2026-07-31 `npm audit --audit-level=high` sale en rojo en las dos
    superficies incluso con `next` en 14.2.35 (último parche de la línea): el
    único fix es `next 16`, un major con roturas. Un lector que no lo encuentre
    escrito concluirá que el escaneo npm está limpio.
    """
    assert re.search(r"\bnext\b", reference_text), "la referencia no nombra `next`"
    assert re.search(
        r"\b16\b", reference_text
    ), "la referencia debe decir que el fix de los avisos de `next` es la major 16"


# ---------------------------------------------------------------------------
# task_registry_adr_12 — ADR de distribución de las imágenes runtime
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry_adr() -> tuple[dict[str, object], str]:
    assert REGISTRY_ADR.is_file(), (
        "falta el ADR de distribución de imágenes runtime (task_registry_adr_12): "
        "hoy cada host construye su propia variante irreproducible de las 14 "
        "imágenes donde corre el código NO confiable"
    )
    return _split_frontmatter(REGISTRY_ADR.read_text(encoding="utf-8"))


def test_registry_adr_is_signed_and_points_at_the_plan(
    registry_adr: tuple[dict[str, object], str],
) -> None:
    """Firmado por un humano el 2026-08-01, y con la opción elegida escrita.

    Este test nació afirmando `proposed`, y esa afirmación era correcta mientras
    lo fue: dónde vive el registry, quién publica y si el host de un tenant
    puede tirar de internet son decisiones de PRODUCTO, no de toolchain, y un
    agente no las firma (el ADR 0147, lockfile, sí nació `accepted` porque solo
    cambia con qué versiones se construye).

    Firmado el asunto, la guarda no se retira: se **invierte**. Lo que ahora
    vigila es que nadie devuelva el ADR a `proposed` —que sería borrar una
    decisión humana— y que la elección conste en el cuerpo, no solo en el
    frontmatter. Un `status: accepted` sin decisión escrita es exactamente el
    pecado documental que este repo persigue.
    """
    frontmatter, body = registry_adr
    assert frontmatter.get("status") == "accepted", (
        "el ADR de registry lo firmó un humano el 2026-08-01; devolverlo a "
        f"`proposed` borraría esa decisión (status actual: {frontmatter.get('status')!r})"
    )
    assert frontmatter.get("plan_referenced") == "prod-11-cadena-suministro"
    assert (
        "Decisión del operador" in body
    ), "el cuerpo debe recoger la decisión firmada, no solo el frontmatter"


def test_registry_adr_offers_the_three_options_with_a_recommendation(
    registry_adr: tuple[dict[str, object], str],
) -> None:
    """Las tres opciones del plan, y una recomendación razonada.

    Un ADR `proposed` sin opciones comparables no es una decisión pendiente: es
    una nota. Y sin recomendación obliga al humano a rehacer el análisis.
    """
    _, body = registry_adr
    # Los tres nombres son los del plan prod-11 («GHCR / registry self-hosted en
    # el stack / seguir build-local documentado»), no invención del test.
    for option in ("GHCR", "self-hosted", "build-local"):
        assert option.lower() in body.lower(), f"el ADR no plantea la opción `{option}`"
    assert "Recomendación" in body, "el ADR no deja una recomendación razonada"


def test_registry_adr_names_the_status_quo_it_replaces(
    registry_adr: tuple[dict[str, object], str],
) -> None:
    """El ADR ancla el problema en el código concreto, no en abstracto.

    `_IMAGE_TAG = "v1"` en el catálogo y `push: false` en el workflow de la
    matriz son las dos líneas que hacen que las imágenes de runtime sean
    irreproducibles entre hosts. Si el ADR no las nombra, nadie sabrá qué tocar
    cuando la decisión se firme.
    """
    _, body = registry_adr
    for anchor in ("_IMAGE_TAG", "push: false", "catalog.py"):
        assert anchor in body, f"el ADR no ancla el statu quo en `{anchor}`"


def test_registry_adr_does_not_claim_implementation() -> None:
    """El ADR NO puede haber tocado el catálogo: la decisión no está tomada.

    Añadir hoy un campo `digest` opcional que nadie puebla sería exactamente el
    patrón dominante de esta base —mecanismo entregado, cero llamantes— y encima
    prejuzgaría la opción. El campo entra cuando el ADR se firme.
    """
    catalog = (
        REPO_ROOT
        / "packages"
        / "shared-test-runtimes"
        / "src"
        / "shared_test_runtimes"
        / "catalog.py"
    )
    text = catalog.read_text(encoding="utf-8")
    assert '_IMAGE_TAG = "v1"' in text, (
        "la guarda dejó de encontrar el statu quo del catálogo: si `_IMAGE_TAG` ya "
        "no está, la decisión del ADR 0148 se tomó y este test hay que reescribirlo "
        "junto al ADR (que pasaría a `accepted`)"
    )
