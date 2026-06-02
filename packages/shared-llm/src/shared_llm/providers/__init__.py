"""Concrete provider implementations (ADR 0021)."""

from shared_llm.providers.azure_foundry import AzureFoundryAPIMProvider
from shared_llm.providers.claude_agent import ClaudeAgentProvider
from shared_llm.providers.copilot import (
    CopilotProvider,
    DeviceCodeInfo,
    DevicePollResult,
)
from shared_llm.providers.ollama import OllamaProvider

__all__ = [
    "AzureFoundryAPIMProvider",
    "ClaudeAgentProvider",
    "CopilotProvider",
    "DeviceCodeInfo",
    "DevicePollResult",
    "OllamaProvider",
]
