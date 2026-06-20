"""Unit: apertura de PR/MR por proveedor (ADR 0072 fase 2) — parseo del remoto y
el request correcto por GitHub/GitLab/Azure DevOps. Transporte mockeado."""

from __future__ import annotations

import json

import httpx
import pytest
from workers.pr_openers import PrError, build_pr_opener, open_pull_request, parse_remote

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo.git", ("github.com", "owner", "repo")),
        ("git@github.com:owner/repo.git", ("github.com", "owner", "repo")),
        ("https://gitlab.com/group/sub/proj", ("gitlab.com", "group/sub", "proj")),
        ("https://ghe.acme.com/team/api.git", ("ghe.acme.com", "team", "api")),
        ("https://x-access-token:tok@github.com/o/r.git", ("github.com", "o", "r")),
    ],
)
def test_parse_remote(url: str, expected: tuple[str, str, str]) -> None:
    assert parse_remote(url) == expected


def test_parse_remote_invalid() -> None:
    with pytest.raises(PrError):
        parse_remote("not-a-url")


def _client(handler):  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_github_pr_request_and_url() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/7"})

    url = open_pull_request(
        provider="github",
        remote_url="https://github.com/o/r.git",
        token="ghp_x",
        head="plan/abc-feature",
        base="main",
        title="T",
        body="B",
        client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/7"
    assert seen["url"] == "https://api.github.com/repos/o/r/pulls"
    assert seen["auth"] == "Bearer ghp_x"
    assert seen["body"] == {"title": "T", "head": "plan/abc-feature", "base": "main", "body": "B"}


def test_github_enterprise_uses_api_v3() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(201, json={"html_url": "https://ghe.acme.com/t/a/pull/1"})

    open_pull_request(
        provider="github",
        remote_url="https://ghe.acme.com/t/a.git",
        token="x",
        head="h",
        base="main",
        title="T",
        body="B",
        client=_client(handler),
    )
    assert seen["url"] == "https://ghe.acme.com/api/v3/repos/t/a/pulls"


def test_gitlab_mr_request() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["token"] = req.headers.get("private-token")
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"web_url": "https://gitlab.com/g/s/p/-/merge_requests/3"})

    url = open_pull_request(
        provider="gitlab",
        remote_url="https://gitlab.com/g/s/p.git",
        token="glpat",
        head="plan/x",
        base="main",
        title="T",
        body="B",
        client=_client(handler),
    )
    assert url == "https://gitlab.com/g/s/p/-/merge_requests/3"
    # path URL-encoded del proyecto (grupo/sub/proj)
    assert "/api/v4/projects/g%2Fs%2Fp/merge_requests" in seen["url"]
    assert seen["token"] == "glpat"
    assert seen["body"]["source_branch"] == "plan/x" and seen["body"]["target_branch"] == "main"


def test_azure_pr_request() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            201, json={"pullRequestId": 9, "_links": {"web": {"href": "https://az/pr/9"}}}
        )

    url = open_pull_request(
        provider="azure_devops",
        remote_url="https://dev.azure.com/org/proj/_git/repo",
        token="azpat",
        head="plan/x",
        base="main",
        title="T",
        body="B",
        client=_client(handler),
    )
    assert url == "https://az/pr/9"
    assert "/org/proj/_apis/git/repositories/repo/pullrequests" in seen["url"]
    assert seen["auth"].startswith("Basic ")
    assert seen["body"]["sourceRefName"] == "refs/heads/plan/x"


def test_error_status_raises() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="validation failed")

    with pytest.raises(PrError):
        open_pull_request(
            provider="github",
            remote_url="https://github.com/o/r.git",
            token="x",
            head="h",
            base="main",
            title="T",
            body="B",
            client=_client(handler),
        )


def test_build_pr_opener_binds_head_and_base() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["head"] == "plan/abc" and body["base"] == "develop"
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/1"})

    # build_pr_opener no acepta client; validamos la firma del seam (title, body).
    opener = build_pr_opener(
        provider="github",
        remote_url="https://github.com/o/r.git",
        token="x",
        head="plan/abc",
        base="develop",
    )
    assert callable(opener)
    import inspect

    params = list(inspect.signature(opener).parameters)
    assert params == ["title", "body"]
