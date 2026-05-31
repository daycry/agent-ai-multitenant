"""Output-structure guardrail (Plan 11, Phase B — task_11_09).

Registers the ``output_structure`` guardrail type. It validates a model's
``post_llm`` output against a configured JSON Schema and triggers when the
output is not valid JSON or does not conform to the schema, so the host can
block (or retry-with-feedback) malformed structured output.

Hooks
-----
Primary hook is ``post_llm`` (the model emitted structured output that must
match a schema). It also works at ``post_tool`` (a tool returned a JSON
document that must conform), since it only reads the hook's primary text and
the structured ``tool_result`` when present.

Detection strategy (pure Python — ``jsonschema``)
-------------------------------------------------
The configured ``schema`` is a JSON Schema (Draft 2020-12 by default). The
guardrail parses the output text as JSON (or uses the already-structured
``tool_result``) and validates it. Two failure modes both trigger:

  * **not_json** — the output is not parseable JSON at all,
  * **schema_violation** — it parses but violates the schema.

``jsonschema`` is a lightweight, pure-Python dependency (no model, no native
extension), so it is a *base* dependency of ``shared-guardrails`` — the engine
stays importable everywhere including CI.

The detection is side-effect-free: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``retry_with_feedback`` (a
malformed structured output is most usefully fixed by re-prompting the model
with the validation error, the plan's correction loop).
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from shared_guardrails.checks._common import coerce_action, coerce_severity
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult


class OutputStructureGuardrail:
    """Validates structured output against a configured JSON Schema.

    Config:
      - ``schema``           dict  — required JSON Schema the output must
        conform to. Validated at construction; an invalid schema raises
        :class:`GuardrailConfigError`.
      - ``severity``         str   — default ``medium``.
      - ``suggested_action`` str   — override the default action. When unset
        the guardrail suggests ``retry_with_feedback``.

    The result ``payload`` carries:
      - ``valid``    always ``False`` on a trigger,
      - ``reason``   ``"not_json"`` | ``"schema_violation"``,
      - ``errors``   list of human-readable validation messages,
      - ``error_paths`` list of ``"a.b[0]"``-style JSON paths that failed.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        schema = config.get("schema")
        if not isinstance(schema, dict):
            raise GuardrailConfigError(
                "output_structure guardrail requires a 'schema' dict (a JSON Schema)."
            )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise GuardrailConfigError(
                f"output_structure 'schema' is not a valid JSON Schema: {exc.message}"
            ) from exc
        self._validator = Draft202012Validator(schema)
        self._severity = coerce_severity(config.get("severity"))
        self._suggested_override = coerce_action(config.get("suggested_action"))

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.RETRY_WITH_FEEDBACK

    def _load_payload(self, context: GuardrailContext) -> tuple[Any, bool]:
        """Return ``(value, parsed_ok)`` for the structured payload to check.

        A ``post_tool`` already-structured ``tool_result`` (dict / list) is
        used as-is; otherwise the hook's primary text is parsed as JSON.
        """
        if context.hook == "post_tool" and isinstance(context.tool_result, dict | list):
            return context.tool_result, True
        text = context.primary_text()
        try:
            return json.loads(text), True
        except (ValueError, TypeError):
            return None, False

    def check(self, context: GuardrailContext) -> GuardrailResult:
        value, parsed_ok = self._load_payload(context)
        if not parsed_ok:
            return GuardrailResult(
                triggered=True,
                severity=self._severity,
                detail="Output is not valid JSON; cannot validate against schema.",
                suggested_action=self._suggested_action(),
                payload={"valid": False, "reason": "not_json", "errors": [], "error_paths": []},
            )

        errors = sorted(self._validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        if not errors:
            return GuardrailResult(triggered=False, payload={"valid": True})

        messages = [e.message for e in errors]
        paths = [_json_path(e) for e in errors]
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=f"Output violates JSON Schema: {len(errors)} error(s).",
            suggested_action=self._suggested_action(),
            payload={
                "valid": False,
                "reason": "schema_violation",
                "errors": messages,
                "error_paths": paths,
            },
        )


def _json_path(error: jsonschema.ValidationError) -> str:
    """Render a validation error's location as a dotted/indexed JSON path."""
    parts: list[str] = []
    for token in error.absolute_path:
        if isinstance(token, int):
            parts.append(f"[{token}]")
        elif parts:
            parts.append(f".{token}")
        else:
            parts.append(str(token))
    return "".join(parts) or "$"


@register_guardrail("output_structure")
def _build_output_structure(config: dict[str, Any]) -> OutputStructureGuardrail:
    return OutputStructureGuardrail(config)


__all__ = ["OutputStructureGuardrail"]
