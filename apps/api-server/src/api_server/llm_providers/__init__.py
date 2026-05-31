"""Platform-global LLM provider administration (Plan 11.2 Fase A).

The application-layer pieces backing the ``/admin/llm-providers`` System
Admin CRUD surface (ADR 0028):

  * :mod:`api_server.llm_providers.vault` — the write/read Vault store for
    a provider's credential (``platform/llm/<provider_id>``). Credentials
    NEVER touch a DB column; only the Vault pointer ``secret_vault_path``
    is persisted.

The ORM shape + repository helpers live in
:mod:`api_server.db.llm_providers` (task_11_2_01); the REST router lives in
:mod:`api_server.routers.llm_providers` (task_11_2_02).
"""

from __future__ import annotations
