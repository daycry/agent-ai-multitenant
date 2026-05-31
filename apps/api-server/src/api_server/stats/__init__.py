"""Tenant statistics analytics (Plan 14 Fase D).

Aggregation + detection over the :class:`~api_server.db.domain.Execution`
table that backs the tenant statistics dashboards. The read-side aggregation
endpoints live in :mod:`api_server.routers.tenant_stats` (task_14_12); this
package holds the analysis that runs ON those aggregates — currently outlier
detection + configurable alerts (task_14_13).
"""

from __future__ import annotations

__all__: list[str] = []
