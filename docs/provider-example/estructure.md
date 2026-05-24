llm_layer/
├── **init**.py
├── types.py # Tipos comunes (mensajes, respuestas)
├── base.py # Protocol / clase base
├── exceptions.py
└── providers/
├── **init**.py
├── claude_agent.py # Wrapper de claude-agent-sdk
├── copilot.py # OAuth device flow + chat
└── azure_foundry.py # Azure AI Foundry vía APIM
