"""Minimal provider liveness probes for ``POST /admin/llm-providers/{id}/test``.

ADR 0028: the "probar conexión" button does a *minimal* live call to the
provider with the credential read from Vault, and returns a typed
ok/classified-error result. It NEVER leaks the secret — not in the
response, not in the error message, not in logs.

Per kind (the four ADR-0021 paths):

  * ``ollama``        — GET ``{base_url}/models`` (the OpenAI-compatible
                        list-models endpoint; ``base_url`` already includes
                        ``/v1`` — local ``http://localhost:11434/v1`` or cloud
                        ``https://ollama.com/v1`` — matching how the shared-llm
                        OllamaProvider talks to it), optional bearer. A 2xx is
                        OK; a 401/403 is AUTH_ERROR; a connect failure is
                        CONNECTION_ERROR.
  * ``azure_foundry`` — GET the APIM gateway with EITHER auth form the
                        factory accepts: ``Ocp-Apim-Subscription-Key`` when the
                        row carries a subscription key, or ``Authorization:
                        Bearer`` when it is bearer-only (APIM validating an AAD
                        JWT). A reachable gateway that does not reject the
                        credential is OK; a 401/403 is AUTH_ERROR.
  * ``copilot``       — mints the short-lived JWT against
                        ``api.github.com/copilot_internal/v2/token`` with the
                        stored OAuth token, exactly as
                        ``CopilotProvider._ensure_jwt`` does at run time. This
                        is the check that notices a REVOKED token.
  * ``claude_sdk``    — subscription path with no cheap public liveness
                        endpoint. The probe verifies the credential is PRESENT
                        in Vault and SAYS SO in ``detail`` ("no live call for
                        this kind"), so the UI can render a weaker green
                        instead of implying a verified connection.

Both of the first two used to lie (prod-07 task_prod07_11, llm-12): copilot only
checked that the token EXISTED in Vault — a revoked token stayed there, so the
probe went green and the run died with a 401 later — and azure rejected
bearer-only configs with CONFIG_ERROR even though the factory builds them and
the assistant uses them happily.

The classified status is a small closed enum the UI renders; the
``detail`` is a human string that, by construction, contains no secret.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import httpx

from api_server.db.llm_providers import LLMProviderKind
from api_server.llm_providers.vault import (
    SECRET_FIELD_API_KEY,
    SECRET_FIELD_BEARER_TOKEN,
    SECRET_FIELD_OAUTH_TOKEN,
)


class LivenessStatus(enum.StrEnum):
    """Classified outcome of a provider liveness probe (UI-renderable)."""

    OK = "ok"
    # The provider rejected the credential (401/403).
    AUTH_ERROR = "auth_error"
    # The endpoint was unreachable (DNS / connect / timeout).
    CONNECTION_ERROR = "connection_error"
    # A required field is missing (no base_url / no credential in Vault).
    CONFIG_ERROR = "config_error"
    # The endpoint answered, but with an unexpected status.
    UPSTREAM_ERROR = "upstream_error"


@dataclass(frozen=True)
class LivenessResult:
    """Typed result of a probe. ``ok`` is the boolean the UI toggles on; the
    ``status`` classifies the failure; ``detail`` is a secret-free message."""

    ok: bool
    status: LivenessStatus
    detail: str


# A short timeout — the probe is interactive (the operator is waiting).
_PROBE_TIMEOUT_S = 10.0


async def probe_provider(
    *,
    kind: str,
    base_url: str | None,
    secret: dict[str, str],
    http_client: httpx.AsyncClient | None = None,
) -> LivenessResult:
    """Run a minimal liveness probe for *kind* with the *secret* from Vault.

    ``secret`` is the ``{field: value}`` dict read from Vault for this
    provider; it is consumed locally and never echoed. ``http_client`` is
    injectable so tests drive the probe with a mock transport (no network).
    """
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S)
    try:
        if kind == LLMProviderKind.OLLAMA.value:
            return await _probe_ollama(client, base_url=base_url, secret=secret)
        if kind == LLMProviderKind.AZURE_FOUNDRY.value:
            return await _probe_azure_foundry(client, base_url=base_url, secret=secret)
        if kind == LLMProviderKind.COPILOT.value:
            return await _probe_copilot(client, secret=secret)
        if kind == LLMProviderKind.CLAUDE_SDK.value:
            return _probe_credential_present(kind=kind, secret=secret)
        # Unknown kind should never reach here (the DB CHECK + schema gate
        # it), but classify rather than crash.
        return LivenessResult(
            ok=False,
            status=LivenessStatus.CONFIG_ERROR,
            detail=f"unknown provider kind {kind!r}",
        )
    finally:
        if owns_client:
            await client.aclose()


async def _probe_ollama(
    client: httpx.AsyncClient, *, base_url: str | None, secret: dict[str, str]
) -> LivenessResult:
    if not base_url:
        return LivenessResult(
            ok=False,
            status=LivenessStatus.CONFIG_ERROR,
            detail="Ollama provider has no base_url configured",
        )
    headers: dict[str, str] = {}
    bearer = secret.get(SECRET_FIELD_BEARER_TOKEN)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    # OpenAI-compatible list-models endpoint (base_url already ends in /v1),
    # consistent with shared_llm OllamaProvider.list_models — NOT the native
    # /api/tags (which 404s when base_url is the /v1 OpenAI-compat base, e.g.
    # cloud https://ollama.com/v1).
    url = f"{base_url.rstrip('/')}/models"
    return await _probe_get(client, url, headers=headers, provider="Ollama")


async def _probe_azure_foundry(
    client: httpx.AsyncClient, *, base_url: str | None, secret: dict[str, str]
) -> LivenessResult:
    if not base_url:
        return LivenessResult(
            ok=False,
            status=LivenessStatus.CONFIG_ERROR,
            detail="Azure Foundry provider has no APIM base_url configured",
        )
    # APIM accepts EITHER auth form and so does the factory (`_build_azure_*`):
    # the subscription key (quotas/billing) or a Bearer JWT that APIM validates.
    # Requiring the subscription key here rejected a config that works — the
    # operator could not validate a bearer-only provider at all (task_prod07_11).
    api_key = secret.get(SECRET_FIELD_API_KEY)
    bearer = secret.get(SECRET_FIELD_BEARER_TOKEN)
    if api_key:
        headers = {"Ocp-Apim-Subscription-Key": api_key}
    elif bearer:
        headers = {"Authorization": f"Bearer {bearer}"}
    else:
        return LivenessResult(
            ok=False,
            status=LivenessStatus.CONFIG_ERROR,
            detail=(
                "Azure Foundry provider has neither an API key nor a bearer "
                "token configured in Vault"
            ),
        )
    # List deployments/models is the cheapest authenticated GET the APIM
    # gateway forwards; a reachable gateway that accepts the credential is OK.
    url = f"{base_url.rstrip('/')}/openai/models?api-version=2024-10-21"
    return await _probe_get(client, url, headers=headers, provider="Azure Foundry")


# GitHub's Copilot token endpoint — the same URL `CopilotProvider._ensure_jwt`
# calls on every run. Kept in sync deliberately: probing anything else would
# check something the run does not do.
_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"


async def _probe_copilot(client: httpx.AsyncClient, *, secret: dict[str, str]) -> LivenessResult:
    """Mint the Copilot JWT: the only check that sees a REVOKED token.

    The previous probe asserted the OAuth token was PRESENT in Vault. Revoking a
    token in GitHub does not remove it from Vault, so a dead provider showed
    green and the failure surfaced later as a 401 inside a run — misattributing
    the cause. The mint is a single cheap GET and is exactly what the provider
    does at run time, so a green here means the run can authenticate.
    """
    oauth_token = secret.get(SECRET_FIELD_OAUTH_TOKEN)
    if not oauth_token:
        # No credential at all is CONFIGURATION, not authentication — and there
        # is nothing to call with.
        return LivenessResult(
            ok=False,
            status=LivenessStatus.CONFIG_ERROR,
            detail="copilot provider has no OAuth token configured in Vault",
        )
    headers = {
        "Authorization": f"token {oauth_token}",
        "Accept": "application/json",
    }
    return await _probe_get(client, _COPILOT_TOKEN_URL, headers=headers, provider="Copilot")


def _probe_credential_present(*, kind: str, secret: dict[str, str]) -> LivenessResult:
    """For the subscription/OAuth paths: verify the credential is in Vault.

    There is no cheap public liveness endpoint (a real check mints a token /
    runs a completion — out of scope). The presence of the OAuth token in
    Vault is the actionable signal the UI surfaces.
    """
    if secret.get(SECRET_FIELD_OAUTH_TOKEN):
        return LivenessResult(
            ok=True,
            status=LivenessStatus.OK,
            detail="credential configured in Vault (no live call for this kind)",
        )
    return LivenessResult(
        ok=False,
        status=LivenessStatus.CONFIG_ERROR,
        detail=f"{kind} provider has no OAuth token configured in Vault",
    )


async def _probe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    provider: str,
) -> LivenessResult:
    """GET *url*, classify the outcome. Never includes the secret in detail."""
    try:
        resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        # Connect / DNS / timeout — the message carries the URL/cause, not
        # the secret (which only ever lived in a header we built locally).
        return LivenessResult(
            ok=False,
            status=LivenessStatus.CONNECTION_ERROR,
            detail=f"{provider} endpoint unreachable: {type(exc).__name__}",
        )
    if resp.status_code in (401, 403):
        return LivenessResult(
            ok=False,
            status=LivenessStatus.AUTH_ERROR,
            detail=f"{provider} rejected the credential (HTTP {resp.status_code})",
        )
    if resp.is_success:
        return LivenessResult(
            ok=True,
            status=LivenessStatus.OK,
            detail=f"{provider} responded OK (HTTP {resp.status_code})",
        )
    return LivenessResult(
        ok=False,
        status=LivenessStatus.UPSTREAM_ERROR,
        detail=f"{provider} returned an unexpected status (HTTP {resp.status_code})",
    )


__all__ = [
    "LivenessResult",
    "LivenessStatus",
    "probe_provider",
]
