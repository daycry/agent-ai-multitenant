"""ADR 0153 (C): qué hace el gate con una categoría que la política NO lista.

Hasta hoy, las dos vías que evalúan la política resolvían lo no listado con un
``"auto"`` fijo — **fail-open**: lo que la política no nombra, corre sin humano.
Y lo que acaba en ``projects.human_approval_policy`` nombraba 3 de 13 categorías
en los proyectos nacidos de plantilla, así que el default decidía casi todo.

Lo que se fija aquí:

  1. la decisión de lo no listado la escribe la POLÍTICA (``unlisted_category``);
  2. si la clave no está, se DERIVA del ``preset`` (mismo criterio que la
     siembra: estricto en ``production``/``customer-external``, laxo en
     ``sandbox``/``development``);
  3. si tampoco hay preset reconocible —o el valor de la clave no se entiende—
     se falla **CERRADO**: parar y preguntar es recuperable, dejar correr una
     acción sensible no lo es;
  4. el gate dice POR QUÉ paró, porque una aprobación sin motivo se aprueba sin
     leer.

**Y se ejercitan los DOS espejos.** ``api_server.db.approval_repo`` y
``agent_runtime.approval`` no se importan entre sí (el sandbox es otro proceso,
sin BD), así que arreglar uno solo deja el agujero abierto justo donde corre el
código NO confiable. Cada caso de la tabla pasa por los dos y, además, un test
compara sus respuestas una a una: si alguien toca uno y olvida el otro, esto se
pone rojo antes de que llegue a un run.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_runtime.approval import (
    requires_human,
)
from agent_runtime.approval import (
    unlisted_category_reason as runtime_unlisted_category_reason,
)
from api_server.db.approval_repo import (
    HUMAN_QUESTION_CATEGORY,
    requires_human_approval,
    unlisted_category_reason,
)

pytestmark = pytest.mark.unit


def _cats(**overrides: str) -> dict[str, str]:
    """Un mapa `categories` PARCIAL — que es justo el caso real que motiva todo."""
    return {"code_changes": "auto", "git_push": "human_required", **overrides}


#: ``(id, política, categoría, ¿exige humano?)``. La tabla es la especificación:
#: cada fila es una rama distinta de la resolución, y las dos implementaciones
#: espejo tienen que dar el MISMO resultado en todas.
_CASES: tuple[tuple[str, dict[str, Any] | None, str, bool], ...] = (
    # --- la categoría SÍ está listada: manda el mapa, no el default ---------
    ("listada-human_required", {"preset": "sandbox", "categories": _cats()}, "git_push", True),
    (
        "listada-auto-bajo-preset-estricto",
        {"preset": "production", "categories": _cats()},
        "code_changes",
        False,
    ),
    (
        "listada-con-espacios-y-mayusculas",
        {"preset": "sandbox", "categories": {"git_push": " Human_Required "}},
        "git_push",
        True,
    ),
    # --- no listada + clave explícita: manda la clave, no el preset ---------
    (
        "no-listada-clave-human_required-bajo-preset-laxo",
        {"preset": "sandbox", "categories": _cats(), "unlisted_category": "human_required"},
        "data_export_pii",
        True,
    ),
    (
        "no-listada-clave-auto-bajo-preset-estricto",
        {"preset": "production", "categories": _cats(), "unlisted_category": "auto"},
        "data_export_pii",
        False,
    ),
    # --- no listada, sin clave: se DERIVA del preset ------------------------
    (
        "no-listada-preset-production",
        {"preset": "production", "categories": _cats()},
        "data_export_pii",
        True,
    ),
    (
        "no-listada-preset-customer-external",
        {"preset": "customer-external", "categories": _cats()},
        "user_management",
        True,
    ),
    (
        "no-listada-preset-sandbox",
        {"preset": "sandbox", "categories": _cats()},
        "data_export_pii",
        False,
    ),
    (
        "no-listada-preset-development",
        {"preset": "development", "categories": _cats()},
        "external_http_post",
        False,
    ),
    (
        "no-listada-preset-con-ruido",
        {"preset": "  Production  ", "categories": _cats()},
        "data_export_pii",
        True,
    ),
    # --- no listada y NO se sabe interpretar: fail-CLOSED -------------------
    (
        "no-listada-preset-desconocido",
        {"preset": "mi-preset-de-la-casa", "categories": _cats()},
        "data_export_pii",
        True,
    ),
    ("no-listada-sin-preset", {"categories": _cats()}, "data_export_pii", True),
    (
        "no-listada-clave-ilegible",
        # Un typo de `human_required` bajo un preset laxo. Creerle al preset
        # dejaría pasar la acción sobre una política cuyo autor pedía lo
        # contrario: sin entender el valor, se para.
        {"preset": "sandbox", "categories": _cats(), "unlisted_category": "human"},
        "data_export_pii",
        True,
    ),
    (
        "categories-no-es-un-mapa",
        {"preset": "sandbox", "categories": "todo"},
        "data_export_pii",
        False,
    ),
    # --- la forma «mapa desnudo», que el contrato sigue aceptando -----------
    ("mapa-desnudo-listada", {"git_push": "human_required"}, "git_push", True),
    ("mapa-desnudo-no-listada", {"git_push": "human_required"}, "data_export_pii", True),
    # --- sin política: territorio del ADR 0104, NO de este ------------------
    ("politica-ausente", None, "data_export_pii", False),
    ("politica-vacia", {}, "data_export_pii", False),
)


@pytest.mark.parametrize(
    "policy,category,expected",
    [pytest.param(p, c, e, id=i) for i, p, c, e in _CASES],
)
def test_api_server_mirror(policy: dict[str, Any] | None, category: str, expected: bool) -> None:
    assert requires_human_approval(policy, category) is expected


@pytest.mark.parametrize(
    "policy,category,expected",
    [pytest.param(p, c, e, id=i) for i, p, c, e in _CASES],
)
def test_sandbox_mirror(policy: dict[str, Any] | None, category: str, expected: bool) -> None:
    """El espejo del runtime — el que corre AL LADO del código no confiable."""
    assert requires_human(policy, category) is expected


def test_the_two_mirrors_never_disagree() -> None:
    """La guarda anti-deriva: un solo lado arreglado es el agujero de siempre.

    No se importan entre sí (el sandbox no tiene BD ni api-server), así que
    NADA en el código impide que se separen. Esto sí.
    """
    for case_id, policy, category, _ in _CASES:
        assert requires_human_approval(policy, category) == requires_human(
            policy, category
        ), case_id
        assert unlisted_category_reason(policy, category) == runtime_unlisted_category_reason(
            policy, category
        ), case_id


def test_the_table_actually_exercises_both_branches() -> None:
    """Guarda anti-vacío: una tabla que solo tuviera casos `False` pasaría igual
    contra la implementación vieja (fail-open) sin probar nada."""
    decisions = {expected for _, _, _, expected in _CASES}
    assert decisions == {True, False}
    assert len(_CASES) >= 15


# ---------------------------------------------------------------------------
# El motivo — «un humano que recibe una aprobación sin motivo la aprueba sin leer»
# ---------------------------------------------------------------------------
def test_no_reason_when_the_policy_does_list_the_category() -> None:
    """La categoría listada se explica sola: la política la nombra y la decide."""
    policy = {"preset": "production", "categories": _cats()}
    assert unlisted_category_reason(policy, "git_push") is None
    assert runtime_unlisted_category_reason(policy, "git_push") is None


def test_no_reason_when_the_gate_does_not_stop() -> None:
    policy = {"preset": "sandbox", "categories": _cats()}
    assert unlisted_category_reason(policy, "data_export_pii") is None


def test_reason_names_the_category_and_the_preset_it_derived_from() -> None:
    policy = {"preset": "production", "categories": _cats()}
    reason = unlisted_category_reason(policy, "data_export_pii")
    assert reason is not None
    assert "data_export_pii" in reason
    assert "production" in reason


def test_reason_points_at_the_explicit_key_when_it_is_the_one_deciding() -> None:
    policy = {"preset": "sandbox", "categories": _cats(), "unlisted_category": "human_required"}
    reason = unlisted_category_reason(policy, "data_export_pii")
    assert reason is not None
    assert "unlisted_category" in reason
    # Y NO culpa al preset, que aquí no ha decidido nada.
    assert "sandbox" not in reason


def test_reason_says_it_is_failing_closed_when_the_preset_is_unknown() -> None:
    """La rama que más va a doler en soporte: hay que poder leer qué arreglar."""
    reason = unlisted_category_reason({"preset": "raro", "categories": _cats()}, "data_export_pii")
    assert reason is not None
    assert "raro" in reason
    assert "fail-closed" in reason


def test_reason_says_it_is_failing_closed_when_there_is_no_preset() -> None:
    reason = unlisted_category_reason({"categories": _cats()}, "data_export_pii")
    assert reason is not None
    assert "fail-closed" in reason


def test_reason_flags_an_unreadable_unlisted_value() -> None:
    policy = {"preset": "sandbox", "categories": _cats(), "unlisted_category": "human"}
    reason = unlisted_category_reason(policy, "data_export_pii")
    assert reason is not None
    assert "human" in reason
    assert "fail-closed" in reason


# ---------------------------------------------------------------------------
# El motivo llega HASTA EL HUMANO, que es lo único que lo hace valer
# ---------------------------------------------------------------------------
class _FakeResult:
    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return []


class _FakeSession:
    """Lo justo para ejercitar `request_approval_if_needed` sin Postgres.

    `session.get(Task, ...)` devuelve None a propósito: la transición de la
    tarea es del ADR 0020 y ya está cubierta en la suite de integración; aquí lo
    que se afirma es qué se PERSISTE en la solicitud.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def get(self, _model: Any, _pk: Any) -> None:
        return None

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult()

    async def flush(self) -> None:
        return None


async def _park(policy: dict[str, Any] | None, category: str) -> Any:
    from types import SimpleNamespace
    from uuid import uuid4

    from api_server.db.approval_repo import request_approval_if_needed

    session = _FakeSession()
    execution = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), task_id=uuid4(), status=None)
    project = SimpleNamespace(id=uuid4(), human_approval_policy=policy)
    return await request_approval_if_needed(
        session,  # type: ignore[arg-type]
        execution=execution,  # type: ignore[arg-type]
        project=project,  # type: ignore[arg-type]
        category=category,
        action={"tool": "http_post", "args": {"url": "https://example.test"}},
    )


@pytest.mark.asyncio
async def test_the_request_carries_the_reason_it_stopped() -> None:
    """«Un humano que recibe una aprobación sin motivo la aprueba sin leer»."""
    request = await _park({"preset": "production", "categories": _cats()}, "data_export_pii")

    assert request is not None
    assert "gate_reason" in request.action
    assert "data_export_pii" in request.action["gate_reason"]
    assert "production" in request.action["gate_reason"]


@pytest.mark.asyncio
async def test_the_reason_is_a_sibling_key_and_never_touches_the_fingerprint() -> None:
    """ADR 0135: lo que se hashea es `tool` + `args` verbatim.

    Meter el motivo dentro de `args` cambiaría la huella y ninguna aprobación
    previa se podría canjear — el bucle aprobar→re-aparcar, reabierto por una
    anotación cosmética.
    """
    request = await _park({"preset": "production", "categories": _cats()}, "data_export_pii")

    assert request is not None
    assert request.action["args"] == {"url": "https://example.test"}
    assert request.action["tool"] == "http_post"


@pytest.mark.asyncio
async def test_a_listed_category_persists_exactly_the_action_it_used_to() -> None:
    """La solicitud de siempre no cambia ni un byte: el motivo solo aparece
    donde hace falta explicarlo."""
    request = await _park({"preset": "production", "categories": _cats()}, "git_push")

    assert request is not None
    assert request.action == {"tool": "http_post", "args": {"url": "https://example.test"}}


# ---------------------------------------------------------------------------
# La vía de escritura: un typo no puede acabar en la BD
# ---------------------------------------------------------------------------
# Sin esto, `unlisted_category: "human_requiered"` se acepta con un 200, el gate
# lo lee como ilegible y para TODO lo no listado. Correcto por seguridad y
# opaco para quien lo escribió: no hay pantalla donde se vea el typo.
@pytest.mark.parametrize(
    "policy",
    [
        {"categories": {}, "unlisted_category": "human_requiered"},
        {"categories": {}, "unlisted_category": True},
        {"categories": {"unlisted_category": "auto"}},
    ],
    ids=["typo", "no-es-cadena", "colada-dentro-de-categories"],
)
def test_the_api_rejects_a_policy_the_gate_could_not_read(policy: dict[str, Any]) -> None:
    import pydantic
    from api_server.schemas.projects import ProjectUpdateRequest

    with pytest.raises(pydantic.ValidationError):
        ProjectUpdateRequest(human_approval_policy=policy)


@pytest.mark.parametrize(
    "policy",
    [
        {"categories": _cats(), "unlisted_category": "auto"},
        {"categories": _cats(), "unlisted_category": "human_required"},
        # Sin la clave sigue siendo válida: la resuelve el preset (o el
        # fail-closed). Exigirla aquí rompería toda política ya escrita.
        {"categories": _cats()},
        # Y una categoría no canónica NO se rechaza: `all` y `external_http` los
        # escriben plantillas built-in vivas, y este validador no es su juez.
        {"categories": {"all": "auto"}, "unlisted_category": "auto"},
    ],
    ids=["auto", "human_required", "sin-clave", "categoria-no-canonica"],
)
def test_the_api_accepts_the_policies_that_already_exist(policy: dict[str, Any]) -> None:
    from api_server.schemas.projects import ProjectCreateRequest

    request = ProjectCreateRequest(name="P", human_approval_policy=policy)
    assert request.human_approval_policy == policy


# ---------------------------------------------------------------------------
# Lo que este ADR NO toca
# ---------------------------------------------------------------------------
def test_ask_human_is_still_always_human_whatever_the_policy_says() -> None:
    """ADR 0114: `human_question` no lo decide la política, ni la clave nueva."""
    all_auto = {
        "preset": "sandbox",
        "categories": {HUMAN_QUESTION_CATEGORY: "auto"},
        "unlisted_category": "auto",
    }
    assert requires_human_approval(all_auto, HUMAN_QUESTION_CATEGORY) is True


def test_a_project_without_policy_is_not_this_adr_s_business() -> None:
    """El ADR 0104 ya lo resolvió: sin política se hereda el preset por defecto,
    y esa resolución vive en el worker. Fallar cerrado AQUÍ gatearía todo run de
    un proyecto recién creado antes de que el preset llegue a aplicarse."""
    assert requires_human_approval(None, "production_deploy") is False
    assert requires_human(None, "production_deploy") is False
