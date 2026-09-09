"""`project.integrations` (`task_mk_20`, MK-05): el JSONB es cerrado y con formato.

Una clave desconocida es un 422, no un dato que se guarda y nadie lee; una clave
de issue que no es `ABC-123` tampoco entra. Y lo que se guarda es lo MÍNIMO: sin
`None` sueltos, para que `{}` signifique exactamente «sin anclas».
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.schemas.integrations import validate_integrations_payload
from api_server.schemas.projects import ProjectCreateRequest, ProjectUpdateRequest
from pydantic import ValidationError


def _update(**kwargs: Any) -> ProjectUpdateRequest:
    return ProjectUpdateRequest(**kwargs)


def test_a_full_valid_payload_is_kept_verbatim() -> None:
    payload = {
        "jira": {"project_key": "PLAT", "parent_issue_key": "PLAT-120"},
        "confluence": {"space_key": "ENG", "root_page_id": "123456"},
    }
    assert validate_integrations_payload(payload) == payload


def test_the_anchor_keys_are_optional_and_none_is_dropped() -> None:
    out = validate_integrations_payload({"jira": {"project_key": "PLAT", "parent_issue_key": None}})
    assert out == {"jira": {"project_key": "PLAT"}}


def test_empty_and_none_mean_no_anchors() -> None:
    assert validate_integrations_payload({}) == {}
    assert validate_integrations_payload(None) == {}


@pytest.mark.parametrize(
    "payload, fragment",
    [
        ({"github": {"repo": "x"}}, "github"),
        ({"jira": {"project_key": "PLAT", "epic": "PLAT-1"}}, "jira.epic"),
        ({"jira": {"project_key": "plat"}}, "jira.project_key"),
        ({"jira": {"project_key": "PLAT", "parent_issue_key": "PLAT120"}}, "parent_issue_key"),
        ({"jira": {"project_key": "PLAT", "parent_issue_key": "plat-120"}}, "parent_issue_key"),
        ({"confluence": {"space_key": "ENG", "root_page_id": "abc"}}, "root_page_id"),
        ({"confluence": {"root_page_id": "1"}}, "space_key"),
    ],
)
def test_unknown_keys_and_bad_formats_are_rejected(payload: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        validate_integrations_payload(payload)


def test_a_list_is_not_an_integrations_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validate_integrations_payload([])  # type: ignore[arg-type]


def test_the_update_request_validates_and_keeps_none_as_no_change() -> None:
    assert _update().integrations is None
    assert _update(integrations={}).integrations == {}
    ok = _update(integrations={"jira": {"project_key": "PLAT", "parent_issue_key": "PLAT-7"}})
    assert ok.integrations == {"jira": {"project_key": "PLAT", "parent_issue_key": "PLAT-7"}}
    with pytest.raises(ValidationError, match=r"jira\.epic"):
        _update(integrations={"jira": {"project_key": "PLAT", "epic": "PLAT-7"}})


def test_the_create_request_defaults_to_no_anchors_and_validates() -> None:
    assert ProjectCreateRequest(name="p").integrations == {}
    with pytest.raises(ValidationError, match="root_page_id"):
        ProjectCreateRequest(
            name="p", integrations={"confluence": {"space_key": "ENG", "root_page_id": "x"}}
        )
