"""Runtime template schema (Plan 06 task_06_01).

A :class:`RuntimeTemplate` is the immutable contract one of the
fourteen curated test-runtime images publishes to the platform. The
worker takes the template id from a task's acceptance criteria,
looks up the template in the catalog, and uses the fields here to
build the ``docker run`` envelope for ``test-runtime``.

The shape covers the seven knobs the spec calls out (section 12.4 of
the .docx, paraphrased):

  * ``docker_image``           — full registry ref (incl. tag) of the
                                  image the worker pulls.
  * ``workspace_mount_path``    — where the worktree gets mounted
                                  inside the container (default
                                  ``/workspace``, mirroring our
                                  agent-runtime convention).
  * ``dep_cache_mount``         — where the shared dep-cache gets
                                  mounted (e.g. ``/root/.cache/pip``).
                                  ``None`` opts the template out of
                                  the caching machinery (Plan 06
                                  Fase C).
  * ``default_pre_install``     — commands the worker runs once before
                                  tests (e.g. ``["pip install -r
                                  requirements.txt"]``). Skipped when
                                  the dep-cache hit was warm.
  * ``default_resources``       — cpu / memory limits applied to the
                                  container at run time. The project
                                  can override these per-task.
  * ``output_parsers``          — ordered list of parser ids the
                                  worker tries against the container's
                                  output to build the canonical
                                  TestReport (Plan 06 Fase D).
  * ``network_policy``          — ``none`` (default) | ``restricted``
                                  | ``open``. Most templates run with
                                  ``none``; ``restricted`` is for the
                                  generic-http runner that talks to
                                  the test compose; ``open`` is only
                                  for explicit integration scenarios.

The dataclass is ``frozen=True`` so templates are hashable and safe
to share across threads — the catalog hands them out by reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Output parser ids. Plan 06 Fase D (task_06_14) implements the
# concrete parsers; this literal is the closed set the schema accepts.
# Adding a new parser ⇒ add it here AND add the implementation in
# the agent-runtime (or wherever the worker hosts parsers).
OutputParser = Literal[
    "junit_xml",
    "jest_json",
    "playwright_json",
    "surefire_xml",
    "tap",
    "trx",
    "go_test_json",
    "rust_test_json",
    "raw_text",
]

# Network policy applied to the container by the worker. The worker
# enforces these via docker network attachments; the template only
# declares the default.
#
#   none        — no interface attached. The container talks to nobody.
#   restricted  — attached to an ephemeral docker network with only the
#                 task's compose services (postgres-test, redis-test).
#                 No egress to the host or the internet.
#   registries  — internal bridge + the worker transiently attaches the
#                 allowlisted ``registry-proxy`` so dependency installs
#                 (composer/pip/npm/go/nuget/…) resolve their registries.
#                 No raw NAT; egress is proxied + allowlisted (ADR 0094).
#   open        — historically: attached to a non-internal bridge with raw
#                 NAT. Redefined by ADR 0094 as an alias of ``registries``
#                 (proxied egress, never raw internet) for back-compat.
NetworkPolicy = Literal["none", "restricted", "registries", "open"]


@dataclass(frozen=True)
class Resources:
    """Container resource limits the worker applies at ``docker run``.

    ``cpu`` is in fractional cores (``1.0`` = one core). ``memory_mb``
    is the hard cap. Both translate to docker's ``--cpus`` and
    ``--memory`` flags. Projects can override these per-task in the
    task's acceptance criteria.
    """

    cpu: float = 1.0
    memory_mb: int = 1024

    def __post_init__(self) -> None:
        if self.cpu <= 0:
            raise ValueError(f"cpu must be > 0, got {self.cpu!r}")
        if self.memory_mb <= 0:
            raise ValueError(f"memory_mb must be > 0, got {self.memory_mb!r}")


@dataclass(frozen=True)
class RuntimeTemplate:
    """One entry in the catalog of curated test-runtime images.

    The catalog itself (task_06_02) is a ``dict[str, RuntimeTemplate]``
    keyed by ``id``. The worker resolves the template by id when it
    reads a task's acceptance criteria.

    Validation rules enforced in ``__post_init__``:

      * ``id`` must be a non-empty slug-shaped string (kebab-case).
      * ``docker_image`` must be a non-empty registry reference.
      * ``workspace_mount_path`` and ``dep_cache_mount`` (when set)
        must be absolute paths.
      * ``output_parsers`` must list at least one parser.
      * ``output_parsers`` entries must be unique (no dup attempts).
    """

    # Stable identifier referenced by tasks (e.g. ``"python-pytest"``).
    # Kebab-case, lowercase, no spaces.
    id: str

    # Full registry reference including tag. The worker passes this
    # to ``docker pull`` / ``docker run`` verbatim.
    docker_image: str

    # Filesystem location inside the container where the task's
    # worktree gets bind-mounted. Defaults to ``/workspace`` to mirror
    # the agent-runtime convention (Plan 02).
    workspace_mount_path: str = "/workspace"

    # Where the shared dep-cache mounts. ``None`` opts the template
    # out of caching entirely (e.g. generic-shell). Per template:
    # python-pytest → /root/.cache/pip ; node-* → /root/.npm ; etc.
    dep_cache_mount: str | None = None

    # Commands the worker shell-runs in order before kicking off the
    # tests, when the dep-cache is cold. Empty list = nothing to pre-
    # install (the image already ships everything).
    default_pre_install: tuple[str, ...] = ()

    # Default cpu/memory caps. Projects can override per-task.
    default_resources: Resources = field(default_factory=Resources)

    # Parsers tried in order against the container's output (stdout /
    # produced files) to build the TestReport. The first one that
    # produces a non-empty parse wins; the rest are skipped.
    output_parsers: tuple[OutputParser, ...] = ("raw_text",)

    # Default network policy. Most templates default to ``none`` so
    # the test container has no internet access.
    network_policy: NetworkPolicy = "none"

    # Per-tool cache env vars the worker injects so the tool's
    # ``$HOME``-relative default cache lands on the bind-mounted
    # ``dep_cache_mount`` (ADR 0094). A tuple of ``(key, value)`` pairs
    # to stay frozen/hashable; e.g.
    # ``(("COMPOSER_CACHE_DIR", "/root/.composer/cache"),)``. Read it as
    # a dict with ``dict(template.cache_env)``.
    cache_env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("id must be a non-empty slug")
        if any(c.isupper() or c.isspace() for c in self.id):
            raise ValueError(f"id must be kebab-case (lowercase, no whitespace), got {self.id!r}")
        if not self.docker_image or not self.docker_image.strip():
            raise ValueError("docker_image must be a non-empty registry reference")
        if not self.workspace_mount_path.startswith("/"):
            raise ValueError(
                f"workspace_mount_path must be absolute, got {self.workspace_mount_path!r}"
            )
        if self.dep_cache_mount is not None and not self.dep_cache_mount.startswith("/"):
            raise ValueError(
                f"dep_cache_mount must be absolute or None, got {self.dep_cache_mount!r}"
            )
        if not self.output_parsers:
            raise ValueError("output_parsers must list at least one parser")
        if len(set(self.output_parsers)) != len(self.output_parsers):
            raise ValueError(f"output_parsers has duplicates: {self.output_parsers!r}")


__all__ = [
    "NetworkPolicy",
    "OutputParser",
    "Resources",
    "RuntimeTemplate",
]
