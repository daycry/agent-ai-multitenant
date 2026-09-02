"""«Ya existe un PR para esa rama» no es un fallo: es la URL que buscábamos.

Auditoría 2026-09-01 (D-01), `task_cv_14`. El auto-PR se reintenta —re-veredicto
del plan, reencolado del reconciler— y el proveedor contesta que ya hay un PR
abierto para esa rama: GitHub con 422 («A pull request already exists»), GitLab
con 409 («Another open merge request already exists»). El opener lo trataba como
error y el plan acababa con `pr_error` y sin URL, cuando el PR que se le debía
está abierto en el proveedor. Ahora se recupera la URL del PR existente.
"""

from __future__ import annotations

import json

import httpx
import pytest
from workers.pr_openers import PrError, open_pull_request

pytestmark = pytest.mark.unit


def _github(calls: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [{"message": "A pull request already exists for o:plan/abc."}],
                },
            )
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            assert request.url.params["head"] == "o:plan/abc"
            assert request.url.params["state"] == "open"
            return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7"}])
        return httpx.Response(500, text="unexpected")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_github_422_already_exists_recovers_the_open_pr_url() -> None:
    calls: list[httpx.Request] = []
    url = open_pull_request(
        provider="github",
        remote_url="https://github.com/o/r.git",
        token="tok",
        head="plan/abc",
        base="main",
        title="Plan: x",
        body="b",
        client=_github(calls),
    )
    assert url == "https://github.com/o/r/pull/7"
    assert [c.method for c in calls] == ["POST", "GET"]


def test_github_422_for_another_reason_is_still_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [{"message": "No commits between main and plan/abc"}],
            },
        )

    with pytest.raises(PrError, match="No commits"):
        open_pull_request(
            provider="github",
            remote_url="https://github.com/o/r.git",
            token="tok",
            head="plan/abc",
            base="main",
            title="t",
            body="b",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )


def test_github_already_exists_but_lookup_finds_nothing_keeps_the_original_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                422, json={"errors": [{"message": "A pull request already exists"}]}
            )
        return httpx.Response(200, json=[])

    with pytest.raises(PrError, match="422"):
        open_pull_request(
            provider="github",
            remote_url="https://github.com/o/r.git",
            token="tok",
            head="plan/abc",
            base="main",
            title="t",
            body="b",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )


def test_gitlab_409_already_exists_recovers_the_open_mr_url() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                409,
                json={
                    "message": [
                        "Another open merge request already exists for this source branch: !3"
                    ]
                },
            )
        if request.method == "GET":
            # El path del proyecto viaja percent-encoded (`o%2Fr`); httpx lo
            # decodifica en `.path`, así que se mira el `raw_path` del cable.
            assert request.url.raw_path.startswith(b"/api/v4/projects/o%2Fr/merge_requests")
            assert request.url.params["source_branch"] == "plan/abc"
            assert request.url.params["state"] == "opened"
            return httpx.Response(
                200, json=[{"web_url": "https://gitlab.com/o/r/-/merge_requests/3"}]
            )
        return httpx.Response(500)

    url = open_pull_request(
        provider="gitlab",
        remote_url="https://gitlab.com/o/r.git",
        token="tok",
        head="plan/abc",
        base="main",
        title="t",
        body="b",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert url == "https://gitlab.com/o/r/-/merge_requests/3"
    assert [c.method for c in calls] == ["POST", "GET"]
    assert json.loads(calls[0].content)["source_branch"] == "plan/abc"
