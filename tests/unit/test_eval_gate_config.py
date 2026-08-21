"""El gate de evals no puede decir PASS sin haber medido nada (`task_gov_04`).

El defecto que motiva este fichero, encontrado el 2026-08-19 en el
reconocimiento de `gov-01`: `api_server.evals.ci_run.main` devolvía
`EXIT_GATE_PASSED` cuando `diff_provider is None`, con el mensaje «no live diff
provider available — nothing to gate, treating as pass». O sea que la rama
**viva** del workflow —la que se activa cuando el operador da de alta la
credencial del proveedor— no podía bloquear nada: sin productor de diff decía
que todo estaba bien. Y no hay ni un productor real en el repo (`DiffProvider`
sólo se inyecta desde los tests), así que ese camino era el único que la rama
viva podía tomar.

Es el patrón «una medida que miente», y es peor que no tener gate: el check sale
**verde**, así que nadie va a mirar por qué no protege.

## Por qué la salida es fail-closed CON estado propio

Lo resuelve la cadena de precedencia de `CLAUDE.md` §«Qué manda cuando dos
documentos se contradicen»: manda el **ADR 0038** (`accepted`), y el código lo
contradecía — «el código no gana por estar desplegado».

El ADR 0038 §3 enumera las salidas del merge-gate y **no incluye** la que el
código inventó:

  * `REGRESSED` más allá del umbral → exit no-cero que bloquea el merge;
  * `IMPROVED` / `UNCHANGED` → exit 0;
  * **sin secreto de proveedor** → el *workflow* toma la rama `--dry-run`
    (valida config, sale 0) y hace skip-with-notice.

Un cuarto camino «sin proveedor, sin `--dry-run`, exit 0» no está en el ADR. Y
el ADR sí descarta expresamente el fail-closed **incondicional** («fallar si no
hay proveedor… bloquear todo merge sería inviable»), que es la razón por la que
la rama `--dry-run` se queda donde está: ahí la ausencia de comprobación es una
declaración **explícita** del invocante, no un silencio.

De modo que la frontera correcta no es «con o sin proveedor», es **qué afirma el
invocante**:

| Invocación                  | Afirma                        | Salida        |
| --------------------------- | ----------------------------- | ------------- |
| `--dry-run`                 | «no vengo a gatear»           | 0 (PASSED)    |
| sin `--dry-run`, con diff   | «medí, y este es el veredicto»| 0 / 1         |
| sin `--dry-run`, sin diff   | «vengo a gatear» — y no pudo  | 2 (INCONCLUSIVE) |

Y el tercer estado es no-cero **y** distinguible del 1 a propósito, porque las
dos acciones que exige del humano son distintas: `1` = el prompt empeoró
(arréglalo); `2` = el gate no pudo medir (arregla el gate). Un fail-closed que
reusara el `1` diría «regresión» de algo que nunca se evaluó.

Es la misma forma que el modo del SCA en `ci.yml` (`continue-on-error: false`
explícito, guardado por `test_security_scan_is_an_enforcing_gate`): el modo
declarado **a la vista** y una guarda estática que impide deshacerlo en
silencio. Aquí las guardas son las de la sección «El workflow» de abajo.

Todo es ESTÁTICO / en proceso: parsea el YAML del workflow y llama a la CLI con
un productor inyectado. Sin LLM, sin BD, sin runner de GitHub.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from api_server.db import domain
from api_server.db.evals import EvalResult, EvalResultVerdict, EvalRun
from api_server.evals.ci_run import (
    EXIT_GATE_BLOCKED,
    EXIT_GATE_INCONCLUSIVE,
    EXIT_GATE_PASSED,
    GateOutcome,
    inconclusive_gate,
    main,
)
from api_server.evals.diff import DiffVerdict, RunDiff, diff_runs

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval-on-prompt-change.yml"

#: El job que aplica el gate dentro del workflow.
GATE_JOB = "eval-on-prompt-change"

#: Las tres variables que la CLI necesita para poder evaluar algo. Antes de
#: `task_gov_04` el workflow las INTERPOLABA sin definirlas nunca, así que la
#: rama viva llamaba a la CLI con un agente inventado por el default `:-` y un
#: dataset/baseline VACÍOS.
REQUIRED_EVAL_VARS = ("EVAL_SUBJECT_AGENT", "EVAL_GOLDEN_DATASET", "EVAL_BASELINE_RUN")

#: Variables de shell interpoladas en un `run:` — `${FOO}` / `${FOO:-bar}`, pero
#: NO la expresión `${{ … }}` de GitHub Actions.
_SHELL_VAR = re.compile(r"\$\{(?!\{)([A-Za-z_][A-Za-z0-9_]*)")

#: `api_server.db.domain` se importa por su EFECTO: registra los FK targets del
#: ORM de evals (agents / tasks / executions) en el mapper registry, sin lo cual
#: instanciar `EvalRun` / `EvalResult` falla. Se referencia aqui para que sea un
#: uso real y no un import que un linter retire por "no usado".
_MAPPERS_PRIMED = domain.__name__

_PASS = EvalResultVerdict.PASS.value
_FAIL = EvalResultVerdict.FAIL.value


# ---------------------------------------------------------------------------
# Helpers — diff scripted (mismo patrón que tests/unit/test_ci_eval_gate.py)
# ---------------------------------------------------------------------------
def _result(*, item_id: UUID, verdict: str) -> EvalResult:
    return EvalResult(item_id=item_id, verdict=verdict)


def _diff(*, base: list[str], candidate: list[str], threshold: str = "0") -> RunDiff:
    """Un `RunDiff` scripted sobre filas sin persistir (ni BD ni LLM)."""
    dataset = uuid4()
    items = [uuid4() for _ in base]
    return diff_runs(
        EvalRun(id=uuid4(), dataset_id=dataset),
        EvalRun(id=uuid4(), dataset_id=dataset),
        [_result(item_id=i, verdict=v) for i, v in zip(items, base, strict=True)],
        [_result(item_id=i, verdict=v) for i, v in zip(items, candidate, strict=True)],
        pass_rate_regression_threshold=Decimal(threshold),
    )


def _regressed_diff() -> RunDiff:
    """2/2 pass -> 1/2 pass con umbral 0: una regresión medida de verdad."""
    return _diff(base=[_PASS, _PASS], candidate=[_PASS, _FAIL])


_LIVE_ARGV = ["--agent", "backend-dev", "--dataset", "ds-1", "--baseline-run", "run-1"]


# ---------------------------------------------------------------------------
# La CLI — un gate que no midió no puede decir que todo está bien
# ---------------------------------------------------------------------------
def test_gate_without_a_diff_provider_does_not_pass() -> None:
    """El corazón del hallazgo: sin productor de diff, la salida NO es PASS.

    Nada se comparó, así que no hay ninguna base para afirmar que el cambio de
    prompt no empeora la calidad. Este test es el que se pone rojo si alguien
    vuelve a `return EXIT_GATE_PASSED` en ese camino.
    """
    code = main(_LIVE_ARGV)
    assert code != EXIT_GATE_PASSED, (
        "el gate salió en verde sin haber medido nada: sin `diff_provider` no "
        "hay diff, no hay veredicto y no hay nada que certificar. Un check "
        "verde que no comprueba es peor que no tener gate."
    )


def test_gate_without_a_diff_provider_is_inconclusive_not_a_regression() -> None:
    """Y el no-cero es el suyo (`2`), no el de una regresión (`1`).

    Las dos situaciones piden acciones distintas del humano que lee el check:
    `1` = el prompt empeoró (arregla el prompt); `2` = el gate no pudo medir
    (arregla la configuración del gate). Reusar el `1` diría «regresión» de algo
    que nunca se evaluó.
    """
    code = main(_LIVE_ARGV)
    assert code == EXIT_GATE_INCONCLUSIVE
    assert code != EXIT_GATE_BLOCKED


def test_the_three_exit_codes_are_distinguishable() -> None:
    """Si dos códigos coinciden, el workflow no puede distinguir los estados."""
    codes = (EXIT_GATE_PASSED, EXIT_GATE_BLOCKED, EXIT_GATE_INCONCLUSIVE)
    assert len(set(codes)) == 3, f"códigos de salida solapados: {codes}"
    assert EXIT_GATE_PASSED == 0, "el paso debe seguir siendo el 0 de siempre"


def test_inconclusive_decision_carries_no_verdict_and_does_not_pass() -> None:
    """La decisión inconclusa no finge un veredicto ni un umbral.

    `verdict` / `threshold` vienen del `RunDiff`, y aquí no hay `RunDiff`:
    rellenarlos con `UNCHANGED` / el default sería exactamente la mentira que
    este fichero persigue, sólo escrita en el dataclass.
    """
    decision = inconclusive_gate("nada que medir")
    assert decision.outcome is GateOutcome.INCONCLUSIVE
    assert decision.blocked is True
    assert decision.verdict is None
    assert decision.threshold is None
    assert decision.exit_code == EXIT_GATE_INCONCLUSIVE


def test_the_inconclusive_message_says_what_to_do(capsys: pytest.CaptureFixture[str]) -> None:
    """El log dice que no se midió y cómo declarar eso a propósito.

    Un rechazo mudo se «arregla» quitando el gate; el mensaje tiene que apuntar
    a la rama `--dry-run`, que es la forma legítima de no gatear.
    """
    main(_LIVE_ARGV)
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "inconclusive" in out.lower()


def test_the_dry_run_branch_still_passes() -> None:
    """`--dry-run` sigue saliendo 0 — y eso es correcto, no una excepción.

    `task_gov_04` lo dice expreso («no quites la rama `--dry-run`») y el
    ADR 0038 §3 la adopta: existe para que un fork sin credenciales no falle. Su
    contrato no es «no hay regresión», es «no vengo a gatear».
    """
    assert main([*_LIVE_ARGV, "--dry-run"]) == EXIT_GATE_PASSED


def test_a_measured_regression_still_blocks() -> None:
    """Con un diff de verdad, el gate sigue haciendo su trabajo de siempre."""
    code = main(_LIVE_ARGV, diff_provider=lambda _args: _regressed_diff())
    assert code == EXIT_GATE_BLOCKED


def test_a_measured_pass_still_passes() -> None:
    """Y un diff sin caída sigue saliendo en 0 — el endurecimiento no lo toca."""
    diff = _diff(base=[_PASS, _FAIL], candidate=[_PASS, _FAIL])
    assert diff.verdict is DiffVerdict.UNCHANGED
    assert main(_LIVE_ARGV, diff_provider=lambda _args: diff) == EXIT_GATE_PASSED


@pytest.mark.parametrize("flag", ["--agent", "--dataset", "--baseline-run"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_required_arguments_are_rejected(flag: str, blank: str) -> None:
    """Un argumento en blanco no llega al gate: se rechaza al parsear.

    La rama viva del workflow interpolaba `${EVAL_GOLDEN_DATASET}` sin
    definirla, así que la CLI recibía `--dataset ""`. `required=True` de argparse
    se conforma con la cadena vacía: la bandera está presente. Es la trampa nº4
    de `verificar-antes-de-implementar.md` (una guarda que pasa vacía) aplicada a
    los argumentos.
    """
    argv = list(_LIVE_ARGV)
    argv[argv.index(flag) + 1] = blank
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code != EXIT_GATE_PASSED


# ---------------------------------------------------------------------------
# El workflow — la rama viva tiene que poder funcionar de verdad
# ---------------------------------------------------------------------------
@pytest.fixture
def workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{WORKFLOW.name}: el YAML de primer nivel no es un mapping"
    # PyYAML (YAML 1.1) resuelve la clave desnuda ``on:`` como el booleano True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


@pytest.fixture
def gate_job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow.get("jobs") or {}
    assert GATE_JOB in jobs, (
        f"{WORKFLOW.name} debe declarar el job '{GATE_JOB}' (jobs: {list(jobs)})"
    )
    job = jobs[GATE_JOB]
    assert isinstance(job, dict), f"el job '{GATE_JOB}' no es un mapping"
    return job


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _step_with(job: dict[str, Any], needle: str) -> dict[str, Any]:
    matches = [s for s in _steps(job) if needle in (s.get("run") or "")]
    assert len(matches) == 1, (
        f"esperaba EXACTAMENTE un paso cuyo `run` contenga {needle!r}, encontré {len(matches)}"
    )
    return matches[0]


def _cli_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in _steps(job) if "api_server.evals.ci_run" in (s.get("run") or "")]


def test_the_workflow_invokes_the_gate_cli(gate_job: dict[str, Any]) -> None:
    """No-vacuidad: si el descubrimiento no encuentra los pasos, todo lo de
    abajo pasaría solo."""
    steps = _cli_steps(gate_job)
    assert len(steps) == 2, (
        "esperaba las DOS ramas de la CLI (dry-run sin secreto + gate vivo con "
        f"secreto), encontré {len(steps)} pasos que la invocan"
    )


def test_the_live_branch_defines_every_variable_it_interpolates(gate_job: dict[str, Any]) -> None:
    """El hallazgo hermano: la rama viva usaba variables que nadie define.

    `${EVAL_GOLDEN_DATASET}` y `${EVAL_BASELINE_RUN}` se interpolaban en el
    `run:` sin aparecer en ningún `env:`, así que se expandían a la cadena vacía
    y el gate corría contra un dataset que no existe. Aunque el operador diera
    de alta el secreto, la casilla no podía funcionar.
    """
    live = next(s for s in _cli_steps(gate_job) if "--dry-run" not in (s.get("run") or ""))
    env = {**(gate_job.get("env") or {}), **(live.get("env") or {})}
    used = set(_SHELL_VAR.findall(live["run"]))
    missing = sorted(v for v in used if v not in env)
    assert not missing, (
        "la rama viva interpola variables que no define en su `env:`: "
        f"{missing}. Se expanden a la cadena vacía y el gate corre contra "
        "argumentos vacíos."
    )
    assert set(REQUIRED_EVAL_VARS) <= used, (
        f"la rama viva debe pasar los tres argumentos del eval desde {REQUIRED_EVAL_VARS}, "
        f"usa {sorted(used)}"
    )


def test_the_live_branch_has_no_fallback_defaults(gate_job: dict[str, Any]) -> None:
    """Y no los suple con un `:-default` inventado.

    `--agent "${EVAL_SUBJECT_AGENT:-changed-prompt-agent}"` hacía que la rama
    viva evaluase un agente que no existe en ningún tenant en vez de decir que
    falta configuración. Un default silencioso convierte un error de config en
    un resultado sin sentido.
    """
    live = next(s for s in _cli_steps(gate_job) if "--dry-run" not in (s.get("run") or ""))
    offenders = [v for v in REQUIRED_EVAL_VARS if f"${{{v}:-" in live["run"]]
    assert not offenders, (
        f"la rama viva rellena con un default las variables {offenders}: si falta "
        "configuración el gate debe decirlo, no evaluar algo inventado"
    )


def test_the_dry_run_branch_is_still_there(gate_job: dict[str, Any]) -> None:
    """La rama sin credenciales no se retira al endurecer el gate.

    Es la única forma legítima de salir en 0 sin medir, y `task_gov_04` la
    protege expresamente: sin ella un fork sin secretos fallaría.
    """
    dry = [s for s in _cli_steps(gate_job) if "--dry-run" in (s.get("run") or "")]
    assert len(dry) == 1, f"esperaba una rama `--dry-run`, encontré {len(dry)}"


def test_the_gate_job_declares_its_mode(gate_job: dict[str, Any]) -> None:
    """El modo (informe vs gate) está A LA VISTA, como en el SCA de `ci.yml`.

    Mismo criterio que `test_security_scan_declares_its_gate_mode`: este test no
    decide el modo, exige que esté escrito. Volver a informe debe ser una
    decisión visible, no la ausencia de una línea.
    """
    assert "continue-on-error" in gate_job, (
        f"el job '{GATE_JOB}' debe declarar `continue-on-error` explícitamente "
        "(true = informe; false = gate que bloquea el merge)"
    )


def test_the_gate_job_is_enforcing(gate_job: dict[str, Any]) -> None:
    """Y el modo declarado es GATE.

    Lo que este test NO puede comprobar, igual que su gemelo del SCA: que el
    check esté en los *required status checks* de la protección de rama. Eso
    vive en la configuración de GitHub y sigue siendo del operador.
    """
    assert gate_job["continue-on-error"] is False, (
        f"el job '{GATE_JOB}' está en modo informe "
        f"(continue-on-error: {gate_job['continue-on-error']!r}): una regresión "
        "de calidad no rompería el build. El ADR 0038 §3 dice que un cambio de "
        "prompt que empeora la calidad se bloquea en CI."
    )


def test_provider_detection_also_requires_the_dataset_config(gate_job: dict[str, Any]) -> None:
    """La rama viva se activa con secreto **y** configuración, no sólo secreto.

    Si se activara sólo con el secreto, el operador que da de alta la credencial
    sin sembrar el dataset golden se encontraría el gate en `2` (inconcluso) en
    cada PR. Con las dos condiciones, ese caso cae en la rama `--dry-run` con un
    aviso que dice exactamente qué falta.
    """
    detect = _step_with(gate_job, "has_provider=")
    env = detect.get("env") or {}
    missing = sorted(v for v in REQUIRED_EVAL_VARS if v not in env)
    assert not missing, (
        f"el paso de detección no lee {missing}: no puede saber si la rama viva "
        "tiene con qué correr"
    )
    run = detect["run"]
    unchecked = sorted(v for v in REQUIRED_EVAL_VARS if f'"${v}"' not in run)
    assert not unchecked, f"el paso de detección no comprueba que {unchecked} estén definidas"
