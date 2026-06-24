"""Apertura de Pull/Merge Request por proveedor (ADR 0072, fase 2).

Dado el remoto + token (PAT), abre un PR (GitHub / Azure DevOps) o MR (GitLab) de
``head`` → ``base`` y devuelve la URL. Provider-dispatch; agnóstico de self-hosted
(GitHub Enterprise usa ``/api/v3``; GitLab y Azure se derivan del host de la URL).
``httpx`` síncrono (corre en el worker, fuera del event loop).

El token es el MISMO PAT que se guarda en Vault para el git del proyecto, así que
no añade superficie de credenciales.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

__all__ = ["PrError", "build_pr_opener", "open_pull_request", "parse_remote"]


class PrError(RuntimeError):
    """Fallo al abrir el PR/MR (URL no parseable, error de la API, etc.)."""


_SSH_RE = re.compile(r"^[\w.+-]+@([^:/]+):(.+?)(?:\.git)?/?$")
_HTTPS_RE = re.compile(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$")


def parse_remote(remote_url: str) -> tuple[str, str, str]:
    """Devuelve ``(host, owner_path, repo)`` desde una URL https o ssh.

    ``owner_path`` puede llevar subgrupos (GitLab: ``grupo/sub``); ``repo`` va sin
    el sufijo ``.git``. Lanza :class:`PrError` si no parsea.
    """
    url = (remote_url or "").strip()
    m = _SSH_RE.match(url) or _HTTPS_RE.match(url)
    if not m:
        raise PrError(f"remote_url no parseable: {remote_url!r}")
    host, path = m.group(1), m.group(2).strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise PrError(f"remote_url sin owner/repo: {remote_url!r}")
    return host, "/".join(parts[:-1]), parts[-1]


def _github_pr(http: httpx.Client, host: str, owner: str, repo: str, token: str, **pr: str) -> str:
    api = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    resp = http.post(
        f"{api}/repos/{owner}/{repo}/pulls",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"title": pr["title"], "head": pr["head"], "base": pr["base"], "body": pr["body"]},
    )
    if resp.status_code >= 300:
        raise PrError(f"GitHub PR falló ({resp.status_code}): {resp.text[:300]}")
    return str(resp.json().get("html_url", ""))


def _gitlab_mr(http: httpx.Client, host: str, owner: str, repo: str, token: str, **pr: str) -> str:
    project = quote(f"{owner}/{repo}", safe="")  # URL-encoded path completo
    resp = http.post(
        f"https://{host}/api/v4/projects/{project}/merge_requests",
        headers={"PRIVATE-TOKEN": token},
        json={
            "source_branch": pr["head"],
            "target_branch": pr["base"],
            "title": pr["title"],
            "description": pr["body"],
        },
    )
    if resp.status_code >= 300:
        raise PrError(f"GitLab MR falló ({resp.status_code}): {resp.text[:300]}")
    return str(resp.json().get("web_url", ""))


def _azure_pr(http: httpx.Client, host: str, owner: str, repo: str, token: str, **pr: str) -> str:
    # owner = "{org}/{project}" (dev.azure.com/org/project/_git/repo). El PAT va en
    # Basic auth con usuario vacío.
    auth = base64.b64encode(f":{token}".encode()).decode()
    org_project = owner.replace("/_git", "")
    resp = http.post(
        f"https://{host}/{org_project}/_apis/git/repositories/{repo}/pullrequests",
        params={"api-version": "7.0"},
        headers={"Authorization": f"Basic {auth}"},
        json={
            "sourceRefName": f"refs/heads/{pr['head']}",
            "targetRefName": f"refs/heads/{pr['base']}",
            "title": pr["title"],
            "description": pr["body"],
        },
    )
    if resp.status_code >= 300:
        raise PrError(f"Azure DevOps PR falló ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    web = (data.get("_links", {}).get("web", {}) or {}).get("href")
    if web:
        return str(web)
    pr_id = data.get("pullRequestId", "")
    return f"https://{host}/{org_project}/_git/{repo}/pullrequest/{pr_id}"


def open_pull_request(
    *,
    provider: str,
    remote_url: str,
    token: str,
    head: str,
    base: str,
    title: str,
    body: str,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> str:
    """Abre el PR/MR y devuelve su URL. Despacha por ``provider``."""
    host, owner, repo = parse_remote(remote_url)
    http = client or httpx.Client(timeout=timeout)
    pr: dict[str, Any] = {"head": head, "base": base, "title": title, "body": body}
    try:
        if provider == "gitlab":
            return _gitlab_mr(http, host, owner, repo, token, **pr)
        if provider == "azure_devops":
            return _azure_pr(http, host, owner, repo, token, **pr)
        if provider in ("github", "generic"):
            return _github_pr(http, host, owner, repo, token, **pr)
        raise PrError(f"provider sin opener de PR: {provider!r}")
    finally:
        if client is None:
            http.close()


def build_pr_opener(*, provider: str, remote_url: str, token: str, head: str, base: str) -> Any:
    """Devuelve un ``pr_opener(title, body) -> url`` ligado al proyecto + rama.

    Encaja tal cual con el seam ``PlanGitWorkflow.open_plan_pr`` (``self._pr_opener
    (title, body)``): la rama del plan (``head``) y la rama destino (``base``) se
    ligan aquí, al construir el workflow."""

    def _opener(title: str, body: str) -> str:
        return open_pull_request(
            provider=provider,
            remote_url=remote_url,
            token=token,
            head=head,
            base=base,
            title=title,
            body=body,
        )

    return _opener
