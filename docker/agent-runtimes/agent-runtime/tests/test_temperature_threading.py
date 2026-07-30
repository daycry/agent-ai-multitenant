"""P1-8 (investigación 2026-07-11): temperature por fin llega al modelo.

`model_config.temperature` se validaba (0-2) en el API y viajaba en el spec del
run, pero el runtime la DESCARTABA al construir los clients — una palanca
declarada en la UI que no operaba. Ahora los kinds HTTP (ollama / azure /
copilot) la pliegan en los call kwargs; claude_sdk la ignora a propósito (el
SDK no expone temperature — documentado, no silencioso).
"""

from __future__ import annotations

from agent_runtime.providers import build_provider_client


def test_ollama_threads_temperature() -> None:
    client = build_provider_client({"kind": "ollama", "model": "gpt-oss:20b", "temperature": 0.2})
    assert client._extra_call_kwargs.get("temperature") == 0.2


def test_azure_and_copilot_thread_temperature() -> None:
    azure = build_provider_client(
        {
            "kind": "azure_foundry",
            "model": "gpt-4o",
            "apim_base_url": "https://apim.example",
            "subscription_key": "k",
            "temperature": 0.7,
        }
    )
    copilot = build_provider_client(
        {"kind": "copilot", "model": "gpt-4o", "github_token": "t", "temperature": 1.0}
    )
    assert azure._extra_call_kwargs.get("temperature") == 0.7
    assert copilot._extra_call_kwargs.get("temperature") == 1.0


def test_absent_temperature_adds_no_kwarg() -> None:
    client = build_provider_client({"kind": "ollama", "model": "m"})
    assert "temperature" not in client._extra_call_kwargs


def test_claude_sdk_ignores_temperature_without_crashing() -> None:
    client = build_provider_client(
        {"kind": "claude_sdk", "model": "claude-sonnet-4-5", "temperature": 0.3}
    )
    assert "temperature" not in getattr(client, "_extra_call_kwargs", {})
