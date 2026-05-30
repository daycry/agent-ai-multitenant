"""Allowed-domains guardrail (Plan 11, Phase B — task_11_09).

Registers the ``allowed_domains`` guardrail type. It extracts URLs from the
hook's text (model output) and from tool arguments, then triggers when any URL
points at a host that is not within a configured allowlist — so the host can
block calls to or generation of links to disallowed domains (data
exfiltration, untrusted fetches, ...).

Hooks
-----
Works at any hook. It is most useful at ``post_llm`` (the model produced a
link), ``pre_tool`` (a tool is about to fetch a URL passed in its args) and
``post_tool`` (a tool returned URLs).

Detection strategy (pure Python — ``urllib.parse``)
---------------------------------------------------
A high-signal URL regex finds candidate URLs in the primary text; at
``pre_tool`` / ``post_tool`` the string-coercible tool arguments are scanned
too (that is where a fetch target hides). Each URL's host is compared against
the allowlist with **suffix matching**: an allowlisted ``example.com`` permits
``example.com`` and ``api.example.com`` but not ``notexample.com`` or
``evil.com``. Comparison is case-insensitive and ignores a leading ``www.``.

No heavy dependency — ``urllib.parse`` from the stdlib does the parsing.

The detection is side-effect-free: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``block``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from shared_guardrails.checks._common import coerce_action, coerce_severity, coerce_str_list
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult

# Matches http(s) and bare ``scheme://host`` URLs. High-signal so prose is not
# misread as a URL; trailing punctuation is excluded from the capture.
_URL_RE = re.compile(
    r"\b(?:https?|ftp|ws|wss)://[^\s<>\"'\]\)]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DomainHit:
    """One URL found in the payload and whether its host is allowed."""

    url: str
    host: str
    allowed: bool


def _normalize_host(host: str) -> str:
    host = host.lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_allowed(host: str, allowlist: list[str]) -> bool:
    """Suffix match: ``api.example.com`` is allowed by ``example.com``.

    ``allowlist`` entries are already normalised (lower-cased, ``www.``
    stripped) at guardrail construction.
    """
    host = _normalize_host(host)
    if not host:
        return True  # no host (e.g. relative) — not a domain escape.
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowlist)


def _extract_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,);'\"") for m in _URL_RE.finditer(text)]


class AllowedDomainsGuardrail:
    """Triggers when a URL targets a host outside the configured allowlist.

    Config:
      - ``allowed_domains``  list[str] — required, non-empty. Bare domains
        (``example.com``); suffix-matched so subdomains are covered.
      - ``severity``         str       — default ``medium``.
      - ``suggested_action`` str       — override the default action. When
        unset the guardrail suggests ``block``.

    The result ``payload`` carries:
      - ``disallowed``  list of ``{url, host}`` that failed the allowlist,
      - ``allowed``     list of allowed hosts seen (for the audit log).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        raw = config.get("allowed_domains")
        if raw is None:
            raise GuardrailConfigError(
                "allowed_domains guardrail requires a non-empty 'allowed_domains' list."
            )
        domains = coerce_str_list(raw, field="allowed_domains", guardrail="allowed_domains")
        if not domains:
            raise GuardrailConfigError(
                "allowed_domains guardrail requires a non-empty 'allowed_domains' list."
            )
        self._allowlist = [_normalize_host(d) for d in domains]
        self._severity = coerce_severity(config.get("severity"))
        self._suggested_override = coerce_action(config.get("suggested_action"))

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.BLOCK

    def _gather_text(self, context: GuardrailContext) -> str:
        """Text to scan: the primary payload + (on tool hooks) the tool args."""
        parts = [context.primary_text()]
        if context.hook in ("pre_tool", "post_tool"):
            parts.extend(_stringify(v) for v in context.tool_args.values())
        return "\n".join(p for p in parts if p)

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = self._gather_text(context)
        if not text:
            return GuardrailResult.ok()

        hits = [
            DomainHit(url=url, host=urlsplit(url).hostname or "", allowed=False)
            for url in _extract_urls(text)
        ]
        hits = [
            DomainHit(h.url, _normalize_host(h.host), _host_allowed(h.host, self._allowlist))
            for h in hits
        ]
        disallowed = [h for h in hits if not h.allowed and h.host]
        if not disallowed:
            return GuardrailResult(triggered=False)

        hosts = sorted({h.host for h in disallowed})
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=f"URL(s) to disallowed domain(s): {', '.join(hosts)}.",
            suggested_action=self._suggested_action(),
            payload={
                "disallowed": [{"url": h.url, "host": h.host} for h in disallowed],
                "allowed": sorted({h.host for h in hits if h.allowed and h.host}),
            },
        )


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


@register_guardrail("allowed_domains")
def _build_allowed_domains(config: dict[str, Any]) -> AllowedDomainsGuardrail:
    return AllowedDomainsGuardrail(config)


__all__ = ["AllowedDomainsGuardrail", "DomainHit"]
