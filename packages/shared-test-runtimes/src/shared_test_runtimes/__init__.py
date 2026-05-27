"""Runtime template schema + catalog (Plan 06 task_06_01).

A *runtime template* is the contract between the platform and one of
the curated test-runtime images (``python-pytest``, ``node-jest``,
``php-phpunit``, …). The schema declares everything the worker needs
to spin up the test container for a task:

  - which Docker image to run
  - where the worktree gets mounted
  - whether/where to mount the shared dep-cache
  - what command(s) to run before tests (e.g. ``pip install -r ...``)
  - default cpu/memory limits
  - which output parsers to try when collecting the TestReport
  - the default network policy for the container

Tasks (or their acceptance criteria) reference a template by ``id``
and the worker resolves the rest from this package's catalog.

Plan 06 task_06_01 only nails the *schema*. The catalog with the
fourteen initial templates is built in task_06_02 once the Dockerfiles
exist.
"""

from shared_test_runtimes.catalog import CATALOG, get, list_ids
from shared_test_runtimes.dep_cache import (
    DEFAULT_TTL_SECONDS,
    CacheEntry,
    DepCacheManager,
    LockHashResult,
    compute_lock_hash,
)
from shared_test_runtimes.types import (
    NetworkPolicy,
    OutputParser,
    Resources,
    RuntimeTemplate,
)

__all__ = [
    "CATALOG",
    "DEFAULT_TTL_SECONDS",
    "CacheEntry",
    "DepCacheManager",
    "LockHashResult",
    "NetworkPolicy",
    "OutputParser",
    "Resources",
    "RuntimeTemplate",
    "compute_lock_hash",
    "get",
    "list_ids",
]
