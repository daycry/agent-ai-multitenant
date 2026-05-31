"""Price-catalog domain logic (Plan 11 Fase D).

The price *catalog* ORM + endpoints live in ``api_server.db.model_prices`` /
``api_server.routers.model_prices`` (Fase C). This package holds the
*synchronisation* logic that keeps the catalog fresh from an external data
feed — currently the community LiteLLM price JSON
(``model_prices_and_context_window.json``), consumed strictly as a **data
feed** (ADR 0021): the platform's closed runtime provider catalog (Claude
SDK + Copilot + Azure Foundry APIM + Ollama) is unaffected; LiteLLM is NOT
a provider runtime here.
"""
