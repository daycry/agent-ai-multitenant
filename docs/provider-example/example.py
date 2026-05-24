"""Ejemplo de uso de la capa unificada."""

import asyncio
import os

from llm_layer import (
    AzureFoundryAPIMProvider,
    ClaudeAgentProvider,
    CopilotProvider,
    LLMProvider,
    Message,
)


async def demo(provider: LLMProvider):
    messages = [
        Message(role="system", content="Eres un asistente conciso. Responde en español."),
        Message(role="user", content="Resume en una frase qué es eIDAS."),
    ]
    resp = await provider.complete(messages, max_tokens=200)
    print(f"[{provider.name}] {resp.content}")
    print(f"  tokens: in={resp.usage.input_tokens} out={resp.usage.output_tokens}")


async def main():
    providers: list[LLMProvider] = []

    # 1. Claude Agent SDK
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(ClaudeAgentProvider(default_model="claude-sonnet-4-5"))

    # 2. GitHub Copilot
    gh_token = os.environ.get("GITHUB_COPILOT_TOKEN")
    copilot = CopilotProvider(
        github_token=gh_token,
        token_store_path=os.path.expanduser("~/.config/llm_layer/copilot.json"),
    )
    if not gh_token:
        # Primera vez: device flow
        await copilot.authenticate_interactive()
    providers.append(copilot)

    # 3. Azure Foundry vía APIM
    if os.environ.get("APIM_BASE_URL"):
        providers.append(
            AzureFoundryAPIMProvider(
                apim_base_url=os.environ["APIM_BASE_URL"],
                deployment=os.environ.get("APIM_DEPLOYMENT", "gpt-4o"),
                subscription_key=os.environ.get("APIM_SUBSCRIPTION_KEY"),
            )
        )

    try:
        for p in providers:
            await demo(p)
    finally:
        for p in providers:
            await p.aclose()


# --- Uso AGENTE de Claude (vía de escape) ---
async def agent_demo():
    provider = ClaudeAgentProvider()
    async for msg in provider.run_agent(
        "Lista los archivos .py de este directorio y dime cuántas líneas tiene cada uno",
        allowed_tools=["Read", "Glob", "Bash"],
        max_turns=5,
    ):
        print(msg)


if __name__ == "__main__":
    asyncio.run(main())
