"""Unified async LLM provider layer (ADR 0021).

A single `LLMProvider` Protocol — `complete()`, `stream()`, `aclose()` —
implemented by a closed catalog of four providers:

  * `ClaudeAgentProvider`        — claude-agent-sdk
  * `CopilotProvider`            — GitHub Copilot via OAuth Device Flow
  * `AzureFoundryAPIMProvider`   — Azure AI Foundry behind APIM
  * `OllamaProvider`             — Ollama local + cloud

Anything else (LiteLLM gateway, raw Anthropic API, OpenAI, Bedrock,
Vertex, ...) is intentionally not supported. See ADR 0021 for the
rationale and the rule for adding a fifth provider.
"""

from shared_llm.base import LLMProvider
from shared_llm.exceptions import (
    AuthError,
    LLMError,
    ProviderError,
    RateLimitError,
)
from shared_llm.providers import (
    AzureFoundryAPIMProvider,
    ClaudeAgentProvider,
    CopilotProvider,
    OllamaProvider,
)
from shared_llm.types import (
    AgentRunEvent,
    CompletionResponse,
    Message,
    Role,
    StreamChunk,
    ToolCall,
    Usage,
)

__all__ = [
    "AgentRunEvent",
    "AuthError",
    "AzureFoundryAPIMProvider",
    "ClaudeAgentProvider",
    "CompletionResponse",
    "CopilotProvider",
    "LLMError",
    "LLMProvider",
    "Message",
    "OllamaProvider",
    "ProviderError",
    "RateLimitError",
    "Role",
    "StreamChunk",
    "ToolCall",
    "Usage",
]
