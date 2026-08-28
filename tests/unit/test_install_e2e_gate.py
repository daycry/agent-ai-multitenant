"""El e2e nocturno de instalación dice la verdad: ni se salta, ni cambia de perfil.

El hueco que estas guardas cierran (2026-08-27)
-----------------------------------------------
`tests/e2e/test_install_from_scratch.py` existe desde el 2026-06-17 y **nunca se
ha ejecutado**: su gate (`E2E_INSTALL=1` + daemon Docker) cae en el SETUP de dos
fixtures de sesión encadenadas, así que pytest recolecta los cuatro casos, los
salta y **sale 0**. Ningún workflow exportaba la variable, y el precio ya está
cobrado: el compose generado montaba once rutas que nadie creaba —Postgres
nacía sin `pgvector` ni sus roles— durante meses, y el único test capaz de verlo
daba verde en cada ejecución.

Cinco cosas hacen falta para que ese verde signifique algo, y ninguna se sostiene
sola:

1. **Que el gate no pueda pasar saltándose** — `scripts/check_e2e_install_report.py`
   lee el JUnit XML y falla si un caso exigido falta o lleva ``<skipped>``.
   Aquí se prueba su LÓGICA contra informes de mentira (`tmp_path`), no contra un
   run real: un test que dependiera de un runner Linux con Docker sería
   permanentemente rojo, y «una suite que siempre falla tampoco es una suite»
   (`docs/03-guides/verificar-antes-de-implementar.md` §4).

   Las formas que se le pasan NO están inventadas: se midieron con
   `pytest --junitxml` de verdad (pytest 9.1.1) — el salto de fixture, el `xfail`,
   el error de colección y, el que más importaba, el **error de teardown**, que es
   donde cae la purga del uninstall (deploy-3) y que pytest emite como `<error>`
   colgando de un `<testcase>` que además consta como pasado.
2. **Que la lista de casos exigidos no envejezca** — el script la lleva escrita
   porque en el runner sólo hay informe; quien impide que se quede corta es la
   comparación por `ast` de §2. Sin ella, un quinto test del e2e entraría sin
   gate y la guarda encogería sin ruido.
3. **Que el perfil con el que instala CI no se aleje del que usa la gente** —
   §3. Un perfil «de CI» libre acaba certificando una instalación que nadie hace.
4. **Que el informe sea el de ESTA corrida** — §6. El gate lee un XML y no tiene
   con qué distinguirlo del que dejó otra ejecución; en el self-hosted al que
   apunta el input `runner` el workspace persiste y el informe lo escribe root,
   así que ni el `git clean` del checkout lo quita. Se cierra por el otro lado:
   el workflow lo BORRA antes de invocar pytest, y entonces «no existe» pasa a
   significar «pytest no lo escribió».
5. **Que el job pueda llegar a dar un veredicto** — §6. Un job que agota el reloj
   queda `cancelled`: ni verde ni rojo, y de un `schedule` cancelado GitHub no
   avisa a nadie. La defensa son los topes por paso, pero sólo valen si suman
   menos que el reloj del job — eso es aritmética, y envejece al primer paso que
   se añada, así que la comprueba un test. Y en la misma sección: que los
   prerrequisitos bloqueantes se miren ARRIBA y no 25 minutos y seis imágenes más
   tarde, porque un job que se pone rojo por ruido acaba desactivado, que es
   perder la cobertura por la otra puerta.

Lo que NO se prueba aquí, y hay que decirlo: que la instalación funcione. Eso
sólo lo dice la primera ejecución real del workflow en un runner Linux.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKER = _REPO_ROOT / "scripts" / "check_e2e_install_report.py"
_E2E_MODULE = _REPO_ROOT / "tests" / "e2e" / "test_install_from_scratch.py"
_PROFILES = _REPO_ROOT / "scripts" / "install-profiles"
_CI_PROFILE = _PROFILES / "ci-e2e.yaml"
_MINIMAL_PROFILE = _PROFILES / "minimal.yaml"


def _gate() -> Any:
    """El script de gate, importado como módulo para leer sus constantes."""

    spec = importlib.util.spec_from_file_location("check_e2e_install_report", _CHECKER)
    assert spec is not None and spec.loader is not None, f"no se puede cargar {_CHECKER}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_gate(report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CHECKER), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )


def _report(tmp_path: Path, cases: str, name: str = "report.xml") -> Path:
    """Un JUnit XML con la forma EXACTA que emite pytest.

    Medida con `pytest --junitxml` sobre un módulo de prueba (pytest 9.1.1): el
    `classname` es la ruta del módulo en puntos y el salto —tanto el del cuerpo
    de un test como el de una fixture— es un hijo
    ``<skipped type="pytest.skip" message="…">``.
    """

    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest tests"><testsuite name="pytest">'
        f"{cases}"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _passing(names: tuple[str, ...], module: str) -> str:
    return "".join(f'<testcase classname="{module}" name="{n}" time="0.1" />' for n in names)


# ---------------------------------------------------------------------------
# §1 — la lógica del gate
# ---------------------------------------------------------------------------
def test_the_gate_exists_and_its_required_list_is_not_empty() -> None:
    """No-vacuidad: con la lista vacía todo lo de abajo pasaría por nada."""
    assert _CHECKER.is_file(), f"falta el gate {_CHECKER}"
    gate = _gate()
    assert len(gate.REQUIRED_TESTS) >= 4, (
        f"REQUIRED_TESTS se quedó en {gate.REQUIRED_TESTS}: el gate exigiría menos "
        "casos de los que el e2e tiene, que es pasar en vacío con otra cara"
    )


def test_a_full_report_passes(tmp_path: Path) -> None:
    gate = _gate()
    report = _report(tmp_path, _passing(gate.REQUIRED_TESTS, gate.REQUIRED_MODULE))

    result = _run_gate(report)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "EJECUTARON y pasaron" in result.stdout


def test_a_skipped_required_case_fails_the_gate(tmp_path: Path) -> None:
    """EL caso. Es lo que produce hoy un run sin `E2E_INSTALL=1`: cuatro casos
    recolectados, cuatro saltados en el setup de la fixture, y pytest saliendo 0."""
    gate = _gate()
    skipped = "".join(
        f'<testcase classname="{gate.REQUIRED_MODULE}" name="{n}">'
        '<skipped type="pytest.skip" message="E2E_INSTALL!=1: e2e de instalación NO '
        'ejecutado." />'
        "</testcase>"
        for n in gate.REQUIRED_TESTS
    )
    report = _report(tmp_path, skipped)

    result = _run_gate(report)

    assert result.returncode == 1, "un e2e ENTERO saltado pasó el gate"
    assert "SALTADO" in result.stderr
    assert "E2E_INSTALL" in result.stderr, "el mensaje no dice qué reponer"


def test_one_missing_case_fails_the_gate(tmp_path: Path) -> None:
    """El caso de dentro: el login se auto-salta si el revelado no se parseó
    (`test_install_from_scratch.py:54-55`) y se pierde la única aserción que
    prueba que la credencial sembrada autentica."""
    gate = _gate()
    report = _report(tmp_path, _passing(gate.REQUIRED_TESTS[:-1], gate.REQUIRED_MODULE))

    result = _run_gate(report)

    assert result.returncode == 1, "un informe al que le falta un caso pasó el gate"
    assert gate.REQUIRED_TESTS[-1] in result.stderr


def test_an_extra_skipped_case_fails_the_gate(tmp_path: Path) -> None:
    """Cero tolerados: el workflow invoca un solo módulo, donde no hay salto
    legítimo. Si la invocación se amplía, esto se pone rojo — a propósito."""
    gate = _gate()
    cases = _passing(gate.REQUIRED_TESTS, gate.REQUIRED_MODULE) + (
        '<testcase classname="tests.e2e.test_worktree_execution" '
        'name="test_worktree_execution_loop_end_to_end">'
        '<skipped type="pytest.skip" message="requiere un modelo capaz" />'
        "</testcase>"
    )
    report = _report(tmp_path, cases)

    result = _run_gate(report)

    assert result.returncode == 1
    assert "test_worktree_execution" in result.stderr


def test_a_failing_case_fails_the_gate(tmp_path: Path) -> None:
    """Este script no puede ser nunca el que diga «bien» sobre una suite roja."""
    gate = _gate()
    cases = _passing(gate.REQUIRED_TESTS[:-1], gate.REQUIRED_MODULE) + (
        f'<testcase classname="{gate.REQUIRED_MODULE}" name="{gate.REQUIRED_TESTS[-1]}">'
        '<failure message="login con la credencial revelada falló: 401" />'
        "</testcase>"
    )
    report = _report(tmp_path, cases)

    result = _run_gate(report)

    assert result.returncode == 1
    assert "ROJO" in result.stderr


def test_a_missing_report_fails_the_gate(tmp_path: Path) -> None:
    """Sin informe no hay verde que discutir: pytest murió antes de ejecutar."""
    result = _run_gate(tmp_path / "no-escrito.xml")

    assert result.returncode == 1
    assert "no existe" in result.stderr


def test_a_teardown_error_fails_the_gate(tmp_path: Path) -> None:
    """El caso de deploy-3, que no tenía guarda: la purga vive en el TEARDOWN.

    `installed_stack` comprueba el desinstalado DESPUÉS del `yield` —rc==0 y la
    raíz de datos borrada—, así que si la purga no limpia, lo que pytest emite no
    es un `<failure>` sino un `<error message="failed on teardown with …">`
    colgando del MISMO `<testcase>` que además consta como pasado. Medido con
    pytest 9.1.1: `1 passed, 1 error`.

    Sin esta aserción, la única prueba de que el gate mira los `<error>` y no sólo
    los `<failure>` era leerse el código. Es justo la mitad del e2e que acredita
    deploy-3.
    """
    gate = _gate()
    cases = _passing(gate.REQUIRED_TESTS[1:], gate.REQUIRED_MODULE) + (
        f'<testcase classname="{gate.REQUIRED_MODULE}" name="{gate.REQUIRED_TESTS[0]}" '
        'time="0.1">'
        '<error message="failed on teardown with &quot;AssertionError: la purga no '
        'eliminó la raíz de datos /data/agent-platform&quot;" />'
        "</testcase>"
    )
    report = _report(tmp_path, cases)

    result = _run_gate(report)

    assert result.returncode == 1, (
        "un e2e cuyo UNINSTALL no purgó la raíz de datos pasó el gate: el error de "
        "teardown es la única señal de deploy-3"
    )
    assert "ROJO" in result.stderr
    assert gate.REQUIRED_TESTS[0] in result.stderr


def test_an_empty_report_fails_the_gate(tmp_path: Path) -> None:
    """Cero casos recolectados. La red que CI usa contra la suite vaciada es el
    `exit 5` de pytest (ci.yml:320-322), y este job no la tiene cableada: el gate
    es lo único que queda, así que tiene que morder también aquí."""
    report = tmp_path / "vacio.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        '<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0" />'
        "</testsuites>",
        encoding="utf-8",
    )

    result = _run_gate(report)

    assert result.returncode == 1, "un informe sin un solo caso pasó el gate"
    assert "MENOS CASOS DE LOS EXIGIDOS" in result.stderr


def test_a_truncated_report_fails_the_gate(tmp_path: Path) -> None:
    """pytest muerto a media escritura (OOM, el reloj del paso, el runner caído).

    Un XML a medias es el modo de fallo con más pinta de accidente inocente, y el
    que más invita a envolver el paso en tolerancia. No hay nada que tolerar: sin
    informe entero no hay evidencia.
    """
    report = tmp_path / "a-medias.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuites><testsuite><testcase classname="tests.e2e',
        encoding="utf-8",
    )

    result = _run_gate(report)

    assert result.returncode == 1
    assert "no es un XML válido" in result.stderr


# ---------------------------------------------------------------------------
# §2 — la lista exigida no puede envejecer
# ---------------------------------------------------------------------------
def _e2e_test_names() -> tuple[str, ...]:
    tree = ast.parse(_E2E_MODULE.read_text(encoding="utf-8"))
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )


def test_the_required_list_matches_the_e2e_module() -> None:
    """Las dos listas no pueden divergir.

    Es la forma de `tests/unit/test_ci_e2e_subset.py`: el gate no se cree su
    propia idea del mundo, la contrasta contra la fuente. Un quinto test del e2e
    que entrara sin pasar por `REQUIRED_TESTS` quedaría FUERA del gate, y el job
    podría darlo por bueno habiéndolo saltado.
    """
    en_el_modulo = _e2e_test_names()
    assert len(en_el_modulo) >= 4, (
        f"sólo se han descubierto {en_el_modulo} en {_E2E_MODULE.name}: o el módulo "
        "encogió, o el descubrimiento por ast está roto"
    )

    exigidos = _gate().REQUIRED_TESTS

    assert sorted(exigidos) == sorted(en_el_modulo), (
        "scripts/check_e2e_install_report.py y el e2e dicen cosas distintas.\n"
        f"  exigidos por el gate: {sorted(exigidos)}\n"
        f"  en el módulo:         {sorted(en_el_modulo)}\n"
        "Añade el caso nuevo a REQUIRED_TESTS: lo que no está en esa lista puede "
        "saltarse sin que el job se entere."
    )


def test_the_required_module_points_at_a_real_file() -> None:
    """Un `classname` con una errata no casaría con ningún `<testcase>` y el gate
    fallaría siempre por la razón equivocada."""
    gate = _gate()
    esperado = _E2E_MODULE.relative_to(_REPO_ROOT).with_suffix("").as_posix().replace("/", ".")
    assert esperado == gate.REQUIRED_MODULE, (
        f"REQUIRED_MODULE es {gate.REQUIRED_MODULE!r} y el módulo está en {esperado!r}"
    )


# ---------------------------------------------------------------------------
# §3 — el perfil de CI no puede alejarse del que usa la gente
# ---------------------------------------------------------------------------
#: La ÚNICA divergencia permitida entre `ci-e2e.yaml` y `minimal.yaml`, con su
#: porqué. Apagar la voz quita 1,9 GB de descarga, dos servidores de modelos y el
#: volumen nombrado `whisper_models` que el uninstall NO borra; ninguna aserción
#: del e2e mira la voz. Cualquier otra diferencia tiene que pasar por editar esta
#: tabla y escribir aquí qué deja de cubrir el nocturno.
_ALLOWED_DIVERGENCE: dict[str, object] = {"resources.voice_mode": "none"}


def _flatten(doc: Any, prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    if isinstance(doc, dict):
        for key, value in doc.items():
            flat.update(_flatten(value, f"{prefix}{key}."))
        return flat
    flat[prefix.rstrip(".")] = doc
    return flat


def test_the_ci_profile_diverges_from_minimal_only_where_declared() -> None:
    assert _CI_PROFILE.is_file(), f"falta el perfil del nocturno: {_CI_PROFILE}"
    ci = _flatten(yaml.safe_load(_CI_PROFILE.read_text(encoding="utf-8")))
    minimal = _flatten(yaml.safe_load(_MINIMAL_PROFILE.read_text(encoding="utf-8")))

    # No-vacuidad: dos ficheros vacíos no divergen en nada y pasarían.
    assert len(minimal) >= 10, f"minimal.yaml se quedó en {len(minimal)} claves: {sorted(minimal)}"

    divergencias = {
        key: ci.get(key)
        for key in sorted(set(ci) | set(minimal))
        if ci.get(key) != minimal.get(key)
    }

    assert divergencias == _ALLOWED_DIVERGENCE, (
        "el perfil del e2e nocturno se ha alejado de minimal.yaml.\n"
        f"  divergencias encontradas: {divergencias}\n"
        f"  declaradas:               {_ALLOWED_DIVERGENCE}\n"
        "Un perfil de CI libre acaba certificando una instalación que nadie hace. "
        "Si la diferencia es deliberada, añádela a _ALLOWED_DIVERGENCE Y escribe en "
        "la cabecera de ci-e2e.yaml qué deja de cubrir el nocturno."
    )


def test_the_ci_profile_declares_the_data_root_the_workflow_prepares() -> None:
    """El job monta y prepara ESA ruta antes de instalar (`/data` no lo escribe
    el usuario `runner`, y el prereq exige 50 GiB libres midiendo ahí). Si el
    perfil cambia de sitio y el workflow no, el install muere en prereqs con
    exit 3 sin decir por qué."""
    doc = yaml.safe_load(_CI_PROFILE.read_text(encoding="utf-8"))
    data_root = (doc.get("storage") or {}).get("data_root")
    workflow = (_REPO_ROOT / ".github" / "workflows" / "install-e2e.yml").read_text(
        encoding="utf-8"
    )

    assert data_root, "el perfil del nocturno no declara storage.data_root"
    assert data_root in workflow, (
        f"el perfil instala en {data_root!r} y .github/workflows/install-e2e.yml no "
        "nombra esa ruta: el job no la prepararía (mkdir + montaje con espacio) y el "
        "install abortaría en prereqs"
    )


# ---------------------------------------------------------------------------
# §4 — el e2e sabe leer el revelado que el instalador imprime
# ---------------------------------------------------------------------------
def _conftest_label(name: str) -> str:
    """El literal de una constante `_LABEL_*` de tests/e2e/conftest.py, por `ast`.

    Se lee en vez de importarse: un conftest importado a mano desde otro test
    acaba cargado dos veces por pytest, con dos identidades del mismo módulo.
    """
    tree = ast.parse((_REPO_ROOT / "tests" / "e2e" / "conftest.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return str(node.value.value)
    raise AssertionError(f"tests/e2e/conftest.py ya no define {name}")


def test_the_e2e_parses_the_labels_the_installer_actually_prints() -> None:
    """El defecto que esto cierra, medido: el parser buscaba la subcadena
    ``"password"`` y el CLI imprime «Contraseña del administrador», así que NUNCA
    encontraba la contraseña y el test de login se saltaba solo. Las dos listas
    —lo que el instalador imprime y lo que el e2e parsea— no pueden divergir, y
    aquí se comparan sin necesidad de un runner con Docker."""
    from installer_backend.finalize import InstallCredentials, build_reveal

    payload = build_reveal(
        InstallCredentials(
            admin_username="admin@acme.com",
            admin_password="no-es-un-secreto",
            vault_root_token="hvs.fake",
            vault_unseal_keys=("k1",),
        )
    )
    impresas = {field.key: field.label_es for field in payload.credentials}

    assert impresas.get("admin_username") == _conftest_label("_LABEL_ADMIN_USERNAME")
    assert impresas.get("admin_password") == _conftest_label("_LABEL_ADMIN_PASSWORD"), (
        f"el instalador imprime {impresas.get('admin_password')!r} y el e2e busca "
        f"{_conftest_label('_LABEL_ADMIN_PASSWORD')!r}. Con las etiquetas desalineadas "
        "el revelado no se parsea y el login queda sin comprobar."
    )


def test_the_e2e_reveal_parser_reads_a_real_reveal_block() -> None:
    """Contra el bloque tal y como lo escribe `cli.py` (`  {label_es}: {secret}`)."""
    from installer_backend.finalize import InstallCredentials, build_reveal

    payload = build_reveal(
        InstallCredentials(
            admin_username="admin@acme.com",
            admin_password="Zx9-no-es-un-secreto",
            vault_root_token="hvs.fake",
            vault_unseal_keys=("k1", "k2"),
        )
    )
    bloque = "\n".join(
        ["=" * 60, payload.warning_es, "=" * 60]
        + [f"  {c.label_es}: {c.secret}" for c in payload.credentials]
        + [f"  Unseal key #{i}: {k}" for i, k in enumerate(payload.unseal_keys, start=1)]
        + ["=" * 60]
    )

    spec = importlib.util.spec_from_file_location(
        "_e2e_conftest_under_test", _REPO_ROOT / "tests" / "e2e" / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conftest)

    creds = conftest._parse_reveal(bloque)

    assert creds == {
        "admin_username": "admin@acme.com",
        "admin_password": "Zx9-no-es-un-secreto",
    }, f"el parseo del revelado devolvió {creds}"


def test_the_ci_profile_enables_no_llm_provider_that_needs_a_secret() -> None:
    """Por qué el nocturno no pide ni un `secrets.*`: el único proveedor es Ollama,
    al que `validate_providers` sólo le exige el `endpoint`. Si mañana alguien
    habilita claude_sdk / copilot / azure_foundry aquí, el job necesitará
    credenciales de verdad y este test lo dice antes de que el run muera."""
    doc = yaml.safe_load(_CI_PROFILE.read_text(encoding="utf-8"))
    providers = doc.get("providers") or {}
    habilitados = sorted(name for name, cfg in providers.items() if (cfg or {}).get("enabled"))

    assert habilitados == ["ollama"], (
        f"el perfil del nocturno habilita {habilitados}. Todo lo que no sea `ollama` "
        "exige credenciales (oauth_token / apim_endpoint + api_key), que el job NO "
        "tiene: hay que darlas de alta como secretos del repositorio y cablearlas en "
        ".github/workflows/install-e2e.yml antes de habilitarlo."
    )


# ---------------------------------------------------------------------------
# §5 — el informe tiene que ser el de ESTA corrida
# ---------------------------------------------------------------------------
def test_an_unreadable_report_fails_with_the_gates_own_message(tmp_path: Path) -> None:
    """Un informe que existe pero no se puede LEER.

    Pasa de verdad: pytest lo escribe bajo `sudo` y el gate lo lee como el
    usuario del runner, así que un umask hostil de root, o un directorio donde
    se esperaba un fichero, dejan al gate sin poder abrirlo. Hoy revienta con un
    traceback de `ElementTree`: sale 1 —falla cerrado, que es lo importante—
    pero el operador no ve el diagnóstico del gate sino una traza interna, y
    ésa es justo la forma que tiene un paso de acabar marcado como «ruido de
    infraestructura» y envuelto en tolerancia.
    """
    ilegible = tmp_path / "informe-que-es-un-directorio.xml"
    ilegible.mkdir()

    result = _run_gate(ilegible)

    assert result.returncode == 1, "un informe ilegible no puede acreditar nada"
    assert "Traceback" not in result.stderr, (
        "el gate murió con una traza de ElementTree en vez de su propio "
        f"diagnóstico:\n{result.stderr}"
    )
    assert "no se puede leer" in result.stderr, (
        f"el gate no dice que el informe existe pero no se abre:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# §6 — la forma del JOB: sin veredicto no hay nocturno
# ---------------------------------------------------------------------------
#: El fichero cuyo veredicto acredita deploy-1/2/3.
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "install-e2e.yml"
_E2E_MODULE_PATH = "tests/e2e/test_install_from_scratch.py"


def _job() -> dict[str, Any]:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    jobs = data.get("jobs") or {}
    assert len(jobs) == 1, (
        f"{_WORKFLOW.name} declara {sorted(jobs)}: estas guardas asumen UN job y "
        "habría que reescribirlas antes de partirlo"
    )
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    return job


def _steps() -> list[dict[str, Any]]:
    steps = [s for s in (_job().get("steps") or []) if isinstance(s, dict)]
    assert len(steps) >= 5, f"{_WORKFLOW.name} sólo declara {len(steps)} pasos: ¿se vació?"
    return steps


def _step_name(step: dict[str, Any]) -> str:
    return str(step.get("name") or step.get("uses") or "(sin nombre)")


def test_the_report_is_wiped_before_pytest_can_write_it() -> None:
    """El informe tiene que ser el de ESTA corrida, y el gate no puede saberlo.

    `check_e2e_install_report.py` lee un XML: no tiene forma de distinguir el que
    pytest acaba de escribir del que dejó otra corrida. En un runner hospedado el
    workspace nace limpio y da igual; el `workflow_dispatch` de este job invita
    explícitamente a un **self-hosted** (input `runner`), donde el workspace
    persiste — y donde además el informe lo escribe ROOT (`sudo env … pytest`),
    así que el `git clean` del checkout, que corre como el usuario del runner, no
    puede borrarlo.

    Con el informe viejo en su sitio, un pytest que muera antes de escribir el
    suyo deja al gate leyendo el veredicto de anteayer y anunciando «los 4 casos
    se EJECUTARON y pasaron». El job sale rojo igual —el paso de pytest falló—,
    pero el veredicto impreso miente, y es exactamente la frase que alguien
    citará mañana para dar por acreditado deploy-1/2/3.

    La salida es que el informe EXISTA sólo si pytest lo escribió: se borra
    antes, y entonces la rama «no existe» del gate pasa a significar lo que dice.
    """
    # Se seleccionan los pasos que ESCRIBEN el informe, no los que nombran el
    # módulo. La distinción se pagó el 2026-08-28: al añadir un paso de
    # `--collect-only` —que corre el mismo fichero para comprobar que pytest
    # arranca, y no produce XML— esta guarda pasó a ver dos pasos y falló por su
    # premisa, no por el defecto que vigila.
    #
    # `$JUNIT_REPORT` es el criterio correcto porque es exactamente el artefacto
    # que el gate lee después: un paso que no lo escribe no puede dejar uno viejo
    # en su sitio, que es de lo que este test protege.
    pasos = [
        s
        for s in _steps()
        if _E2E_MODULE_PATH in s.get("run", "") and "$JUNIT_REPORT" in s.get("run", "")
    ]
    assert len(pasos) == 1, (
        f"{len(pasos)} pasos escriben $JUNIT_REPORT corriendo {_E2E_MODULE_PATH}: "
        "esta guarda asume uno solo. Si de verdad hacen falta dos, cada uno tiene "
        "que borrar el informe antes y este test tiene que comprobarlo en los dos."
    )
    run = pasos[0]["run"]

    borrado = run.find('rm -f "$JUNIT_REPORT"')
    invocacion = run.find("-m pytest")

    assert borrado != -1, (
        f"el paso «{_step_name(pasos[0])}» no borra $JUNIT_REPORT antes de correr "
        "pytest: un informe heredado de otra corrida acreditaría ésta"
    )
    assert borrado < invocacion, (
        "el borrado de $JUNIT_REPORT tiene que ir ANTES de invocar pytest; está en "
        f"la posición {borrado} y pytest en la {invocacion}"
    )


def test_every_step_carries_a_clock_and_they_fit_in_the_jobs_own() -> None:
    """`cancelled` no es rojo, y de un nocturno cancelado no se entera nadie.

    El propio fichero lo razona (citando `ci.yml:490-495` y `:1197-1204`): un job
    que agota su `timeout-minutes` queda `cancelled` —ni verde ni rojo—, y GitHub
    sólo avisa por correo de los `schedule` que FALLAN. La defensa que declara son
    los topes por paso: «con topes por paso, lo que se agota FALLA».

    Esa frase sólo es cierta si el reloj del job es más largo que la suma de los
    topes que sus pasos pueden consumir. Si no, el que llega primero es el del job
    y la defensa no llega a actuar — con el agravante de que el paso «Gate
    anti-falso-verde» corre `if: always()`, así que en una cancelación se ejecuta,
    imprime su veredicto… y el job se queda igualmente en gris.

    Un paso SIN tope rompe la cuenta entera, así que se exigen las dos mitades.
    """
    tope_job = _job().get("timeout-minutes")
    assert isinstance(tope_job, int), (
        f"el job de {_WORKFLOW.name} no declara `timeout-minutes`: sin él un cuelgue "
        "consume las 6 h del máximo de Actions"
    )

    sin_tope = [_step_name(s) for s in _steps() if not isinstance(s.get("timeout-minutes"), int)]
    assert not sin_tope, (
        f"pasos sin `timeout-minutes`: {sin_tope}. Cada uno es tiempo que el reloj "
        "del job puede agotar sin que ningún paso llegue a fallar, y un job "
        "`cancelled` no avisa a nadie"
    )

    suma = sum(int(s["timeout-minutes"]) for s in _steps())
    assert suma <= tope_job, (
        f"los topes por paso suman {suma} min y el job se corta a los {tope_job}. El "
        "reloj del job llegaría primero y el nocturno acabaría en `cancelled`, que no "
        "es rojo y del que no avisa nadie. O el job aguanta la suma de sus pasos, o "
        "los topes por paso son decorativos."
    )


def _holgadas() -> Any:
    """Unas lecturas de host que cumplen holgadamente TODOS los prerrequisitos."""
    from installer_backend.prereqs import HostReadings

    return HostReadings(
        docker_version=(99, 0),
        compose_version=(99, 0),
        total_ram_bytes=64 * 1024**3,
        free_disk_bytes=500 * 1024**3,
        gpu_present=False,
    )


def _required_prereq_keys() -> tuple[str, ...]:
    """Los prerrequisitos que el instalador trata como BLOQUEANTES, derivados.

    Se corren los checks reales contra unas lecturas que lo cumplen todo y se
    conserva el que se declara `required`. Descubrimiento, no lista blanca: un
    prerrequisito duro que se añada mañana entra solo en la guarda de abajo.
    """
    from installer_backend.prereqs import PREREQ_CHECKS, PrereqThresholds

    holgadas = _holgadas()
    thresholds = PrereqThresholds()
    claves = tuple(
        resultado.key
        for check in PREREQ_CHECKS
        if (resultado := check(holgadas, thresholds)).required
    )
    assert len(claves) >= 4, (
        f"sólo se han descubierto {claves} prerrequisitos bloqueantes: o encogieron, "
        "o el descubrimiento está roto y esta guarda pasaría en vacío"
    )
    return claves


def test_the_workflow_preflights_every_blocking_prerequisite() -> None:
    """Los prerrequisitos duros se comprueban ARRIBA, no 25 minutos más tarde.

    Los cinco son bloqueantes de verdad: `cli.py` construye `RealPrereqChecker`
    sin `thresholds`, así que un FAIL aborta con exit 3 **antes de tocar Docker**.
    Sin preflight no desaparecen: reaparecen dentro del `assert` de la fixture
    `installed_stack` como un volcado del stdout de `install.sh` —después de haber
    construido y empujado seis imágenes— con un mensaje que habla del stack y no
    del runner. Ésa es la forma en que un job se gana la fama de «rojo por ruido»,
    y de ahí a desactivarlo hay un paso: perder la cobertura por la otra puerta.

    Lo que se exige NO es que el workflow nombre los cinco —una lista escrita a
    mano se queda corta el día que aparezca el sexto— sino que corra el checker
    del propio instalador ENTERO (`check_all`) y falle por `blocking`. La cuarta
    aserción es la que cierra el círculo: comprueba en proceso que ese
    `check_all` cubre de verdad todas las claves descubiertas, así que estrechar
    el checker rompe esto en vez de dejar un preflight con la misma pinta y menos
    alcance.
    """
    pasos = _steps()
    preflight = [(i, s) for i, s in enumerate(pasos) if "RealPrereqChecker" in s.get("run", "")]
    assert len(preflight) == 1, (
        f"{_WORKFLOW.name} tiene {len(preflight)} pasos que corran el checker de "
        "prerrequisitos del instalador; se espera exactamente uno"
    )
    indice, paso = preflight[0]
    run = paso["run"]

    # 1. El checker entero, y gateado por su propio criterio de bloqueo.
    for pieza in ("SystemHostProbe", "check_all()", ".blocking"):
        assert pieza in run, (
            f"el preflight «{_step_name(paso)}» no usa `{pieza}`: si no corre el "
            "checker completo y no falla por `blocking`, está comprobando otra cosa "
            "distinta de la que aborta el install"
        )

    # 2. La evidencia, impresa: lo que no sale en el log del run no está comprobado.
    assert "PREREQ " in run, (
        f"el preflight «{_step_name(paso)}» no imprime el resultado de cada "
        "prerrequisito; un check mudo no se puede auditar desde el log"
    )

    # 3. Antes de lo caro: un preflight después del build no ahorra nada.
    construir = [i for i, s in enumerate(pasos) if "docker build" in s.get("run", "")]
    assert construir, f"{_WORKFLOW.name} ya no construye imágenes: ¿sigue siendo este job?"
    assert indice < min(construir), (
        f"el preflight es el paso {indice} y la primera construcción de imágenes el "
        f"{min(construir)}: llegando después, el runner que no cumple gasta igual los "
        "40 minutos de build antes de que nadie se lo diga"
    )

    # 4. Y ese `check_all` cubre las cinco claves bloqueantes de verdad.
    from installer_backend.prereqs import RealPrereqChecker

    holgadas = _holgadas()

    class _SondaHolgada:
        def read(self) -> Any:
            return holgadas

    cubiertos = {r.key for r in RealPrereqChecker(_SondaHolgada()).check_all()}
    faltan = [clave for clave in _required_prereq_keys() if clave not in cubiertos]
    assert not faltan, (
        f"el `check_all()` que corre el preflight no evalúa {faltan}, que SÍ abortan "
        "el install con exit 3 (scripts/install.sh:25). El preflight tendría la misma "
        "pinta y menos alcance, que es como una guarda encoge sin ruido."
    )


def test_the_ci_profile_only_cites_guards_that_exist() -> None:
    """La cabecera de `ci-e2e.yaml` cita el test que la sujeta.

    Si ese fichero no existe, lo que hay escrito es una promesa: quien vaya a
    editar el perfil lo busca, no lo encuentra y concluye —razonablemente— que
    puede tocar lo que quiera. Es el patrón de la guarda que no muerde, trasladado
    a la documentación.
    """
    import re

    texto = _CI_PROFILE.read_text(encoding="utf-8")
    citados = sorted(set(re.findall(r"tests/[\w/]+\.py", texto)))

    assert citados, f"{_CI_PROFILE.name} ya no cita ninguna guarda: ¿quién lo sujeta?"
    fantasmas = [ruta for ruta in citados if not (_REPO_ROOT / ruta).is_file()]
    assert not fantasmas, (
        f"la cabecera de {_CI_PROFILE.name} cita ficheros que no existen: {fantasmas}. "
        "Una guarda citada y ausente es peor que ninguna cita"
    )
