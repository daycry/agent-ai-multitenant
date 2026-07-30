"""La self-review ve las tool calls EXTERNAS del run (prueba MCP 2026-07-18).

Caso vivo (run 019f7721, plan «Prueba MCP Atlassian v4»): el agente publicó
en Confluence y transicionó Jira vía tools MCP — el server externo recibió
ambos payloads — pero la self-review solo recibía tarea+criterios+ficheros
del worktree, así que un criterio del tipo «Se invocó
atlassian.confluence_create_page» era INVERIFICABLE y el run escalaba a
humano tras agotar reintentos («no evidence of calls ... in the
transcript»). El prompt del review lleva ahora un digest de las tool calls
del transcript (nombre + args + ok/error) como evidencia verificable.
"""

from __future__ import annotations

from agent_runtime.providers import _review_messages


def _state_with_mcp_calls() -> dict:
    return {
        "task": {
            "title": "Publicar en Confluence",
            "acceptance_criteria": [
                "Se invoco atlassian.confluence_create_page con space_key DEMO"
            ],
        },
        "steps": [
            {"kind": "node", "node": "perceive", "summary": "..."},
            {
                "kind": "tool_call",
                "node": "act",
                "tool": "atlassian.confluence_create_page",
                "args": {"space_key": "DEMO", "title": "Bienvenida E2E", "content": "# hola"},
                "status": "ok",
            },
            {
                "kind": "tool_call",
                "node": "act",
                "tool": "atlassian.jira_transition_issue",
                "args": {"issue_key": "DEMO-123", "status": "Done"},
                "status": "ok",
            },
            {
                "kind": "tool_call",
                "node": "act",
                "tool": "read_file",
                "args": {"path": "docs/BIENVENIDA.md"},
                "status": "ok",
            },
        ],
        "written_files": [{"path": "docs/BIENVENIDA.md", "content": "# hola"}],
        "output": "done",
    }


def test_review_prompt_carries_the_tool_call_digest() -> None:
    text = "\n".join(m.content for m in _review_messages(_state_with_mcp_calls()))
    # Las dos llamadas MCP aparecen como evidencia, con sus args clave.
    assert "atlassian.confluence_create_page" in text
    assert "space_key" in text and "DEMO" in text
    assert "atlassian.jira_transition_issue" in text
    # Marcadas como registro del transcript (evidencia), no como texto libre.
    assert "tool calls" in text.lower() or "acciones" in text.lower()


def test_digest_absent_when_the_run_made_no_tool_calls() -> None:
    state = _state_with_mcp_calls()
    state["steps"] = [{"kind": "node", "node": "perceive", "summary": "..."}]
    text = "\n".join(m.content for m in _review_messages(state))
    assert "Tool calls the agent made" not in text


def test_digest_marks_failed_calls() -> None:
    state = _state_with_mcp_calls()
    state["steps"] = [
        {
            "kind": "tool_call",
            "node": "act",
            "tool": "atlassian.confluence_create_page",
            "args": {"space_key": "DEMO"},
            "status": "error",
            "result": {"ok": False, "error": "boom"},
        }
    ]
    text = "\n".join(m.content for m in _review_messages(state))
    assert "error" in text.lower()
