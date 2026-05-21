"""Built-in seeds for the agentic platform.

All seeds run under the BYPASSRLS admin engine and write to a single
designated "platform" organization (UUID 0000...0001). Tenant sessions
then see those rows through the per-table SELECT-only RLS policies
introduced in migrations 0004 / 0005.

Conventions:
  - Stable IDs via uuid5(NAMESPACE, slug) so re-running the seed is
    a true upsert, not a duplicate insert.
  - ON CONFLICT DO UPDATE on mutable fields (prompts, descriptions,
    config) -- shipping a refined built-in propagates to every tenant
    that uses it via the linked relationship. Forks are unaffected
    because they own a private copy by definition (spec §5.7).
"""

from __future__ import annotations

from uuid import UUID

PLATFORM_TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000001")
PLATFORM_TENANT_SLUG: str = "platform"
PLATFORM_TENANT_NAME: str = "Platform"

# Namespaces used by uuid5() to derive stable IDs for built-in rows.
AGENT_SEED_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000010")
SKILL_SEED_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000011")
TOOL_SEED_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000012")
TEAM_SEED_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000013")
