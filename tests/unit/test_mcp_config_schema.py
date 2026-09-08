"""Unit tests for `Project.mcp_servers` validation (Plan 05 task_05_04).

The validator lives in ``api_server.mcp.config`` and is wired into both
:class:`ProjectCreateRequest` and :class:`ProjectUpdateRequest`. These
tests pin the contract:

* The Pydantic model enforces transport-specific field rules (stdio
  needs `command`, http/sse need `url`, no crossover).
* `auth_ref` must be a Vault pointer (`vault:...`) or `None` — never a
  cleartext secret (CLAUDE.md hard rule).
* The list-level validator rejects duplicate `name` within one project.
* The validator is hooked on the *project* request schemas, not just
  the inner model — so an end-to-end POST/PATCH with a bad entry fails
  with a useful 422.

We deliberately stay in-process — no DB, no HTTP — so the suite runs
in <1s and pins the schema shape without dragging in the api-server's
session machinery.
"""

from __future__ import annotations

import pytest
from api_server.mcp.config import MCPServerConfigModel, validate_mcp_servers_payload
from api_server.schemas.projects import ProjectCreateRequest, ProjectUpdateRequest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# MCPServerConfigModel — transport-specific invariants
# ---------------------------------------------------------------------------
def test_stdio_minimal_valid_config() -> None:
    cfg = MCPServerConfigModel.model_validate(
        {"name": "docling", "transport": "stdio", "command": "docling-mcp"}
    )
    assert cfg.name == "docling"
    assert cfg.transport == "stdio"
    assert cfg.command == "docling-mcp"
    assert cfg.args == []
    assert cfg.env == {}
    assert cfg.url is None
    assert cfg.timeout_s == 30.0


def test_sse_minimal_valid_config() -> None:
    cfg = MCPServerConfigModel.model_validate(
        {"name": "slack", "transport": "sse", "url": "https://slack-mcp.example/mcp"}
    )
    assert cfg.transport == "sse"
    assert cfg.url == "https://slack-mcp.example/mcp"
    assert cfg.command is None


def test_streamable_http_minimal_valid_config() -> None:
    cfg = MCPServerConfigModel.model_validate(
        {
            "name": "github",
            "transport": "streamable_http",
            "url": "https://gh-mcp.example/mcp",
        }
    )
    assert cfg.transport == "streamable_http"
    assert cfg.url == "https://gh-mcp.example/mcp"


def test_stdio_without_command_raises() -> None:
    with pytest.raises(ValidationError, match="requires `command`"):
        MCPServerConfigModel.model_validate({"name": "docling", "transport": "stdio"})


def test_stdio_with_url_raises() -> None:
    with pytest.raises(ValidationError, match="must not set `url`"):
        MCPServerConfigModel.model_validate(
            {
                "name": "x",
                "transport": "stdio",
                "command": "x",
                "url": "https://nope.example/mcp",
            }
        )


def test_stdio_with_headers_raises() -> None:
    with pytest.raises(ValidationError, match="must not set `headers`"):
        MCPServerConfigModel.model_validate(
            {
                "name": "x",
                "transport": "stdio",
                "command": "x",
                "headers": {"Authorization": "Bearer x"},
            }
        )


def test_sse_without_url_raises() -> None:
    with pytest.raises(ValidationError, match="requires `url`"):
        MCPServerConfigModel.model_validate({"name": "slack", "transport": "sse"})


def test_sse_with_command_raises() -> None:
    with pytest.raises(ValidationError, match="must not set `command`"):
        MCPServerConfigModel.model_validate(
            {
                "name": "x",
                "transport": "sse",
                "url": "https://x.example",
                "command": "echo",
            }
        )


def test_sse_with_args_raises() -> None:
    with pytest.raises(ValidationError, match="must not set `args`"):
        MCPServerConfigModel.model_validate(
            {
                "name": "x",
                "transport": "sse",
                "url": "https://x.example",
                "args": ["--port", "0"],
            }
        )


def test_sse_with_env_raises() -> None:
    with pytest.raises(ValidationError, match="must not set `env`"):
        MCPServerConfigModel.model_validate(
            {
                "name": "x",
                "transport": "sse",
                "url": "https://x.example",
                "env": {"FOO": "bar"},
            }
        )


# ---------------------------------------------------------------------------
# Auth + Vault enforcement
# ---------------------------------------------------------------------------
def test_auth_ref_accepts_vault_pointer() -> None:
    cfg = MCPServerConfigModel.model_validate(
        {
            "name": "github",
            "transport": "streamable_http",
            "url": "https://gh-mcp.example/mcp",
            "auth_ref": "vault:secret/data/mcp/github/proj-42",
        }
    )
    assert cfg.auth_ref == "vault:secret/data/mcp/github/proj-42"


def test_auth_ref_none_is_allowed() -> None:
    cfg = MCPServerConfigModel.model_validate(
        {"name": "docling", "transport": "stdio", "command": "docling-mcp"}
    )
    assert cfg.auth_ref is None


def test_auth_ref_raw_token_rejected() -> None:
    """Cleartext tokens in config are a hard `no` — CLAUDE.md says
    Vault is the only credentials path."""
    with pytest.raises(ValidationError, match="Vault pointer"):
        MCPServerConfigModel.model_validate(
            {
                "name": "github",
                "transport": "streamable_http",
                "url": "https://gh-mcp.example/mcp",
                "auth_ref": "ghp_1234567890abcdef",
            }
        )


# ---------------------------------------------------------------------------
# Name pattern + length
# ---------------------------------------------------------------------------
def test_name_must_start_with_letter() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfigModel.model_validate({"name": "1bad", "transport": "stdio", "command": "x"})


def test_name_rejects_special_chars() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfigModel.model_validate(
            {"name": "bad name", "transport": "stdio", "command": "x"}
        )


def test_name_accepts_dot_dash_underscore() -> None:
    cfg = MCPServerConfigModel.model_validate(
        {"name": "github-mcp.v2_beta", "transport": "stdio", "command": "x"}
    )
    assert cfg.name == "github-mcp.v2_beta"


# ---------------------------------------------------------------------------
# timeout_s bounds
# ---------------------------------------------------------------------------
def test_timeout_s_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfigModel.model_validate(
            {"name": "x", "transport": "stdio", "command": "x", "timeout_s": 0}
        )


def test_timeout_s_capped_at_300() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfigModel.model_validate(
            {"name": "x", "transport": "stdio", "command": "x", "timeout_s": 301}
        )


# ---------------------------------------------------------------------------
# Extra fields are forbidden — typos surface instead of silently dropping
# ---------------------------------------------------------------------------
def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        MCPServerConfigModel.model_validate(
            {
                "name": "x",
                "transport": "stdio",
                "command": "x",
                "tranzport": "stdio",  # typo
            }
        )


# ---------------------------------------------------------------------------
# validate_mcp_servers_payload — list-level invariants
# ---------------------------------------------------------------------------
def test_empty_payload_returns_empty_list() -> None:
    assert validate_mcp_servers_payload(None) == []
    assert validate_mcp_servers_payload([]) == []


def test_duplicate_names_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate server name"):
        validate_mcp_servers_payload(
            [
                {"name": "github", "transport": "stdio", "command": "x"},
                {"name": "slack", "transport": "stdio", "command": "y"},
                {"name": "github", "transport": "stdio", "command": "z"},
            ]
        )


def test_non_dict_entry_rejected() -> None:
    with pytest.raises(ValueError, match=r"mcp_servers\[1\]: expected an object"):
        validate_mcp_servers_payload(
            [
                {"name": "ok", "transport": "stdio", "command": "x"},
                "not-a-dict",  # type: ignore[list-item]
            ]
        )


def test_index_appears_in_error_message() -> None:
    with pytest.raises(ValueError, match=r"mcp_servers\[1\]:"):
        validate_mcp_servers_payload(
            [
                {"name": "ok", "transport": "stdio", "command": "x"},
                {"name": "bad", "transport": "stdio"},  # missing command
            ]
        )


def test_canonical_dump_includes_all_fields() -> None:
    """The validator re-serialises to canonical dicts so JSONB diffs
    stay stable — even when the caller only provides the minimum."""
    out = validate_mcp_servers_payload(
        [{"name": "docling", "transport": "stdio", "command": "docling-mcp"}]
    )
    assert len(out) == 1
    entry = out[0]
    assert entry["name"] == "docling"
    assert entry["transport"] == "stdio"
    assert entry["command"] == "docling-mcp"
    assert entry["args"] == []
    assert entry["env"] == {}
    assert entry["url"] is None
    assert entry["headers"] == {}
    assert entry["auth_ref"] is None
    assert entry["timeout_s"] == 30.0


# ---------------------------------------------------------------------------
# Wire-in: validator is reachable from Project request schemas
# ---------------------------------------------------------------------------
def test_project_create_accepts_valid_mcp_servers() -> None:
    req = ProjectCreateRequest.model_validate(
        {
            "name": "proj-a",
            "mcp_servers": [
                {"name": "docling", "transport": "stdio", "command": "docling-mcp"},
                {
                    "name": "github",
                    "transport": "streamable_http",
                    "url": "https://gh-mcp.example/mcp",
                    "auth_ref": "vault:secret/data/mcp/github/proj-a",
                },
            ],
        }
    )
    assert len(req.mcp_servers) == 2
    assert req.mcp_servers[0]["name"] == "docling"
    assert req.mcp_servers[1]["auth_ref"] == "vault:secret/data/mcp/github/proj-a"


def test_project_create_rejects_invalid_mcp_servers() -> None:
    with pytest.raises(ValidationError, match=r"mcp_servers\[0\]"):
        ProjectCreateRequest.model_validate(
            {
                "name": "proj-a",
                "mcp_servers": [
                    {"name": "bad", "transport": "stdio"},  # missing command
                ],
            }
        )


def test_project_create_rejects_duplicate_server_names() -> None:
    with pytest.raises(ValidationError, match="duplicate server name"):
        ProjectCreateRequest.model_validate(
            {
                "name": "proj-a",
                "mcp_servers": [
                    {"name": "dup", "transport": "stdio", "command": "x"},
                    {"name": "dup", "transport": "stdio", "command": "y"},
                ],
            }
        )


def test_project_update_accepts_none_mcp_servers() -> None:
    """PATCH semantics: omitting `mcp_servers` (None) is `do not touch`,
    not `validate as empty list`."""
    req = ProjectUpdateRequest.model_validate({"name": "renamed"})
    assert req.mcp_servers is None


def test_project_update_validates_when_mcp_servers_present() -> None:
    with pytest.raises(ValidationError, match="Vault pointer"):
        ProjectUpdateRequest.model_validate(
            {
                "mcp_servers": [
                    {
                        "name": "leaky",
                        "transport": "stdio",
                        "command": "x",
                        "auth_ref": "raw-token-not-in-vault",
                    }
                ]
            }
        )


# ---------------------------------------------------------------------------
# `task_mk_02` (ADR 0165 D11 + addendum A2) — fail-CLOSED de FORMA de la `url`
# ---------------------------------------------------------------------------
# La allowlist NO se mira aquí (eso es fail-open con aviso, en el router): lo que
# se rechaza al guardar es una URL que la prueba de conexión marcaría después
# desde el api-server — IP literal, nombre de metadata, host mal formado— y el
# `http://` en claro contra un host externo, porque el token de un MCP remoto
# viaja en cabecera y `ConnectPort` sólo gobierna el túnel TLS.


def _http(url: str) -> dict[str, object]:
    return {"name": "remote", "transport": "streamable_http", "url": url}


def test_external_https_host_is_accepted() -> None:
    cfg = MCPServerConfigModel.model_validate(_http("https://mcp.atlassian.com/v1/mcp"))
    assert cfg.url == "https://mcp.atlassian.com/v1/mcp"


def test_external_host_on_8443_is_accepted() -> None:
    """`ConnectPort 443` y `ConnectPort 8443` son las dos directivas globales del
    proxy (ADR 0165 D3): un puerto explícito 8443 sale; 9443 no saldría nunca."""
    cfg = MCPServerConfigModel.model_validate(_http("https://mcp.example.com:8443/mcp"))
    assert cfg.url == "https://mcp.example.com:8443/mcp"


def test_internal_compose_host_is_accepted_over_plain_http() -> None:
    """Un host sin punto es un servicio del compose: se exime por NO_PROXY y no
    lleva TLS (`http://docling:5001/mcp`). La regla es la del worker."""
    cfg = MCPServerConfigModel.model_validate(_http("http://docling:5001/mcp"))
    assert cfg.url == "http://docling:5001/mcp"


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("https://10.0.0.5/mcp", "IP literal"),
        ("https://169.254.169.254/latest", "IP literal"),
        ("https://metadata.google.internal/mcp", "interno o de metadata"),
        ("https://foo.internal/mcp", "interno o de metadata"),
        ("https://mcp.atlassian.com:9443/mcp", "443 y 8443"),
    ],
)
def test_prohibited_external_host_shapes_are_rejected_with_reason(url: str, fragment: str) -> None:
    """422 con el MOTIVO, nunca un booleano: la lista de prohibidos es la misma que
    usa el córtex (`web_safety`), no una copia."""
    with pytest.raises(ValidationError) as exc_info:
        MCPServerConfigModel.model_validate(_http(url))
    assert fragment in str(exc_info.value)


def test_external_host_over_plain_http_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MCPServerConfigModel.model_validate(_http("http://mcp.atlassian.com/v1/mcp"))
    assert "https" in str(exc_info.value)


def test_url_without_host_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MCPServerConfigModel.model_validate(_http("https:///mcp"))
    assert "host" in str(exc_info.value)


def test_the_reason_reaches_the_project_request_with_the_index() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProjectUpdateRequest.model_validate(
            {"mcp_servers": [_http("https://mcp.atlassian.com/mcp"), _http("https://10.0.0.5/mcp")]}
        )
    text = str(exc_info.value)
    assert "mcp_servers[1]" in text
    assert "IP literal" in text
