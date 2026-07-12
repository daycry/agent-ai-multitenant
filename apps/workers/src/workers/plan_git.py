"""Git integration tied to the Plan (Plan 06 Fase F).

Plan 06 ties every plan to one git branch per repo, every commit to
the plan + task + execution it came from, and every plan closure to
an automatic PR. Five tasks of Fase F live here:

  * :func:`make_plan_branch_name` (06_21) — stable branch naming.
  * :func:`commit_task` (06_22) — commits with the four mandatory
    trailers: ``Plan-Id``, ``Task-Id``, ``Execution-Id``,
    ``Generated-By``.
  * :class:`PlanGitWorkflow.push_review_to_bare` (06_23) — the
    worktree → bare-repo step that fires after a successful auto-
    review.
  * :class:`PlanGitWorkflow.push_branch_to_remote` (06_23) — the
    bare → remote step gated by ``branch_push_mode`` (``incremental``
    pushes per task, ``final_only`` pushes once at plan close).
  * :class:`PlanGitWorkflow.open_plan_pr` (06_24) — opens a PR per
    affected repo at plan completion.
  * :class:`PlanGitWorkflow.apply_push_policy` (06_25) — the merge-
    time policy: ``forbidden`` rejects, ``branch_only_pr_required``
    keeps the PR open, ``direct_to_default_allowed`` fast-forwards
    the default branch on the bare.

The three policies (``branch_push_mode`` x ``plan_validation_mode`` x
``push_policy``) are orthogonal — every combination yields a
well-defined behaviour. Section 12.6 of the .docx is the source of
truth; the matrix tests pin the combinations.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog

from workers.git_repos import BareRepoLayout, GitCommandError, _run_git

# Optimistic-concurrency retries for the worktree→bare push: sibling tasks of the
# same plan push to the SAME plan branch, so a losing task must rebase onto the
# branch tip and retry. A handful of retries covers any realistic contention.
_PUSH_RECONCILE_RETRIES = 5


def _is_non_fast_forward(exc: GitCommandError) -> bool:
    """Whether a push failed because the branch advanced (a sibling pushed first)."""
    msg = str(exc).lower()
    return (
        "non-fast-forward" in msg
        or "fast-forwards" in msg
        or "failed to push some refs" in msg
        or "[rejected]" in msg
    )


_log = structlog.get_logger("workers.plan_git")

# Policy axes (Plan 06 section 12.6 of the .docx).
BranchPushMode = Literal["incremental", "final_only"]
PlanValidationMode = Literal["human_required", "auto_approve"]
PushPolicy = Literal["forbidden", "branch_only_pr_required", "direct_to_default_allowed"]


# ---------------------------------------------------------------------------
# task_06_21 — Plan branch naming
# ---------------------------------------------------------------------------

# Short id width — 8 hex chars matches the convention we use across
# the codebase (project.id.hex[:8] is the most common pattern).
_PLAN_ID_SHORT_LEN = 8

# Slug normaliser: lowercase, kebab-case, alnum + dashes only.
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def make_plan_branch_name(plan_id: str, slug: str) -> str:
    """Return ``plan/{id_short}-{slug}`` for a plan id + human slug.

    ``plan_id`` may be a UUID-string (with or without dashes) or any
    other string; we take the first 8 hex characters of its
    non-dashed lowercase form. ``slug`` is normalised to kebab-case.

    Examples::

        >>> make_plan_branch_name("11111111-2222-3333-4444-555555555555", "Fix Auth")
        'plan/11111111-fix-auth'
        >>> make_plan_branch_name("abc123", "")
        'plan/abc123'
    """
    short = plan_id.replace("-", "").lower()[:_PLAN_ID_SHORT_LEN] or plan_id
    norm = _SLUG_RE.sub("-", (slug or "").lower()).strip("-")
    if not norm:
        return f"plan/{short}"
    return f"plan/{short}-{norm}"


@dataclass(frozen=True)
class PlanGitIdentity:
    """A plan's git coordinates: which bare repo holds its commits + its branch.

    ``project_slug`` is BOTH the ``BareRepoLayout`` directory and the bare repo
    name (one bare per project, ADR 0085 decision 2), so the on-disk bare is
    ``.../{tenant_slug}/{project_slug}/repos/{project_slug}.git``.
    """

    project_slug: str
    plan_branch: str


def plan_git_identity(plan_id: str, plan_slug: str, project_slug: str) -> PlanGitIdentity:
    """Single source of truth for a plan's git identity (bare repo + branch).

    Execution, clone and the auto-PR MUST resolve IDENTICAL coordinates, so this
    is the only place that derives them. Callers pass the PERSISTED slugs
    (``projects.slug`` / ``plans.slug``, generated once at creation, ADR 0085) —
    never re-slugify a name/title. Re-slugifying the (prefixed) title in the
    auto-PR while execution used ``plan.slug`` is exactly what made the PR branch
    diverge from the branch that held the commits (audit 2026-07-03, P1/P2).
    """
    return PlanGitIdentity(
        project_slug=project_slug,
        plan_branch=make_plan_branch_name(plan_id, plan_slug),
    )


def worktree_layout(
    *,
    data_root: str | Path,
    tenant_slug: str,
    project_slug: str,
) -> BareRepoLayout:
    """La primitiva de LAYOUT de :func:`worktree_coordinates` (remate I-2, auditoría
    2026-07-10): el sitio que solo necesita el layout — la resolución read-only del
    worktree del review, que no tiene plan a mano — la llama directamente en vez de
    reconstruir ``BareRepoLayout`` a mano, y no puede divergir de los demás.

    IDENTIDAD DooD (CRÍTICO): ``settings.data_root`` es un path DAEMON-SIDE que el worker
    monta en la MISMA ruta y entrega VERBATIM al daemon como bind source. NO normaliza
    (``Path(data_root)`` sin ``resolve()``/realpath) para preservar la identidad
    container-side == daemon-side de los binds ``/workspace``."""
    return BareRepoLayout(
        data_root=Path(data_root), tenant_slug=tenant_slug, project_slug=project_slug
    )


def worktree_coordinates(
    *,
    data_root: str | Path,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
) -> tuple[BareRepoLayout, str]:
    """``(BareRepoLayout, plan_branch)`` — las coordenadas de worktree que TODOS los
    sitios (provisión, resolución read-only, commit/push, review, back-fill) deben
    derivar IDÉNTICAS (hallazgo #10a). Antes cada uno reconstruía el ``BareRepoLayout``
    + ``make_plan_branch_name`` a mano — 5+ puntos propensos a divergir del contrato de
    :func:`plan_git_identity` («Execution, clone and the auto-PR MUST resolve IDENTICAL
    coordinates»). El layout sale de :func:`worktree_layout` (misma primitiva que usa
    la resolución read-only del review); ver allí el invariante DooD de no-normalización.

    El ``repo_name`` (nombre del bare, ADR 0085 = ``project_slug`` salvo override legacy
    por-request) lo resuelve cada caller y lo pasa a ``layout.bare_repo_path`` /
    ``WorktreeManager``."""
    return (
        worktree_layout(data_root=data_root, tenant_slug=tenant_slug, project_slug=project_slug),
        make_plan_branch_name(plan_id, plan_slug),
    )


# ---------------------------------------------------------------------------
# task_06_22 — Commit with trailers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitTrailers:
    """The four mandatory trailers every plan-generated commit carries.

    Mirrors the format ``git commit --trailer`` emits — one trailer
    per output line, ``Key: Value``. Section 12.6 of the .docx
    pins these names + their order.
    """

    plan_id: str
    task_id: str
    execution_id: str
    generated_by: str = "agentic-platform"

    def as_args(self) -> list[str]:
        """Return ``--trailer Key=Value`` args for ``git commit``."""
        return [
            f"--trailer=Plan-Id={self.plan_id}",
            f"--trailer=Task-Id={self.task_id}",
            f"--trailer=Execution-Id={self.execution_id}",
            f"--trailer=Generated-By={self.generated_by}",
        ]


def commit_task(
    worktree_path: Path,
    *,
    message: str,
    trailers: CommitTrailers,
    author_name: str = "Agentic Platform",
    author_email: str = "noreply@agentic.local",
) -> str:
    """Stage everything, commit with trailers, return the new sha.

    Pre-conditions: the agent has already written its changes into
    the worktree. We always ``git add -A`` first because the agent's
    file-tools don't guarantee a clean staging area.

    Returns the new commit sha. Raises :class:`GitCommandError` if
    the working tree was clean (nothing to commit) — the caller
    treats that as "the task produced no code change" and skips the
    push.
    """
    env_extra = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    _run_git("add", "-A", cwd=worktree_path, env_extra=env_extra)
    try:
        _run_git(
            "commit",
            "-m",
            message,
            *trailers.as_args(),
            cwd=worktree_path,
            env_extra=env_extra,
        )
    except GitCommandError as exc:
        if "nothing to commit" in str(exc).lower():
            raise GitCommandError("commit_task: worktree is clean") from exc
        raise
    return _run_git("rev-parse", "HEAD", cwd=worktree_path).strip()


# ---------------------------------------------------------------------------
# task_06_23..06_25 — Workflow class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanGitPolicies:
    """The three orthogonal policies that drive the Git side of a plan.

    Defaults are the Plan 06 section 12.6.8 "razonables" combination:
    incremental + human_required + branch_only_pr_required — the rama
    is visible on the remote from the first task, the human validates
    at end-of-plan, and the system opens a PR for the human to merge.
    """

    branch_push_mode: BranchPushMode = "incremental"
    plan_validation_mode: PlanValidationMode = "human_required"
    push_policy: PushPolicy = "branch_only_pr_required"


# Type alias for the PR-opener seam. The worker injects a real
# ``gh pr create`` runner or a GitHub/AzureDevOps API client; tests
# inject a fake that records calls.
PrOpener = Any  # Callable[[str, str], str] returning the PR URL


@dataclass(frozen=True)
class PrInfo:
    """Result of :meth:`PlanGitWorkflow.open_plan_pr` per repo."""

    repo_name: str
    branch: str
    url: str | None
    skipped_reason: str | None = None


class PlanGitWorkflow:
    """The git side of a plan's life cycle.

    The orchestrator instantiates one per plan and calls these
    methods at the right moments. The class doesn't own state across
    method calls — it's a thin coordinator around
    :func:`workers.git_repos._run_git` + the injected PR opener.
    """

    def __init__(
        self,
        *,
        bare_repo_path: Path,
        plan_branch: str,
        policies: PlanGitPolicies,
        pr_opener: PrOpener | None = None,
        auth_env: dict[str, str] | None = None,
        base_branch: str | None = None,
    ) -> None:
        self._bare_path = bare_repo_path
        self._plan_branch = plan_branch
        self._policies = policies
        self._pr_opener = pr_opener
        # ADR 0072: env de auth git (GIT_ASKPASS/GIT_SSH_COMMAND) para el push al
        # remoto. None = remoto local o ya autenticable por el host.
        self._auth_env = auth_env
        # Rama base del PR (default_branch del git config). Cuando está
        # presente, open_plan_pr verifica ANTES de llamar a la API que la base
        # remota comparte historia con la rama del plan — el 422 «no history
        # in common» del proveedor deja de ser el primer aviso.
        self._base_branch = base_branch

    @property
    def plan_branch(self) -> str:
        return self._plan_branch

    def _base_ancestry_guard(self) -> str | None:
        """Motivo accionable para NO llamar a la API del proveedor, o ``None``.

        Re-fetch de la rama base + ``merge-base`` contra la rama del plan.
        Best-effort: un fallo transitorio del fetch (red/credenciales) NO
        bloquea el intento de PR (contrato anterior); solo bloquean los dos
        casos deterministas — base ausente en el remoto e historias sin
        ancestro común (el caso api-ci: base local sembrada sintética).
        """
        base = self._base_branch
        if not base:
            return None
        try:
            _run_git("fetch", "origin", base, cwd=self._bare_path, env_extra=self._auth_env)
        except GitCommandError as exc:
            if "couldn't find remote ref" in str(exc).lower():
                return (
                    f"el remoto no tiene la rama base '{base}': haz un push inicial de esa "
                    "rama o corrige default_branch en el git del proyecto"
                )
            _log.warning("plan_pr.base_fetch_failed", base=base, error=str(exc))
            return None  # transitorio → no bloquear el intento
        try:
            _run_git("merge-base", "FETCH_HEAD", self._plan_branch, cwd=self._bare_path)
        except GitCommandError:
            return (
                f"la rama base '{base}' del remoto no comparte historia con la rama del plan "
                "(la base local se sembró sin el contenido del remoto): re-sincroniza el git "
                "del proyecto y rebasa la rama del plan, o ajusta default_branch"
            )
        return None

    # ----- task_06_23 — transitions ------------------------------------

    def push_review_to_bare(self, worktree_path: Path) -> str:
        """worktree → bare, reconciling concurrent sibling commits.

        Pushes the worktree's HEAD to the plan branch on the bare repo. Several
        sibling tasks of the same plan share ONE plan branch, so a plain push
        fails *non-fast-forward* whenever another task pushed first — which used
        to surface as ``commit_failed`` and blocked the task. We rebase this
        task's commit onto the branch's current tip and retry (optimistic
        concurrency). A genuine rebase CONFLICT (two tasks changed the same
        lines) is NOT a transient race — it is re-raised as a
        :class:`GitCommandError` so the caller escalates it for resolution.

        Returns the sha now on the bare's branch tip.
        """
        # The rebase replays our commit; give git an identity for the new
        # committer (the worktree carries no user.name/email config).
        committer_env = {
            "GIT_AUTHOR_NAME": "Agentic Platform",
            "GIT_AUTHOR_EMAIL": "noreply@agentic.local",
            "GIT_COMMITTER_NAME": "Agentic Platform",
            "GIT_COMMITTER_EMAIL": "noreply@agentic.local",
        }
        last_exc: GitCommandError | None = None
        for _ in range(_PUSH_RECONCILE_RETRIES):
            try:
                _run_git(
                    "push",
                    str(self._bare_path),
                    f"HEAD:refs/heads/{self._plan_branch}",
                    cwd=worktree_path,
                )
                return _run_git(
                    "rev-parse", f"refs/heads/{self._plan_branch}", cwd=self._bare_path
                ).strip()
            except GitCommandError as exc:
                if not _is_non_fast_forward(exc):
                    raise
                last_exc = exc
                # Reconcile: replay our commit on top of the branch's current tip.
                _run_git("fetch", str(self._bare_path), self._plan_branch, cwd=worktree_path)
                try:
                    _run_git("rebase", "FETCH_HEAD", cwd=worktree_path, env_extra=committer_env)
                except GitCommandError as rebase_exc:
                    # Anticipo ADR 0099: capturar el contexto ESTRUCTURADO del
                    # conflicto (ficheros en disputa + shas de ambos lados)
                    # ANTES del abort — despues ya no existe. Best-effort: si
                    # git falla aqui, el contexto queda parcial pero el error
                    # original se propaga igual.
                    context: dict[str, Any] = {"plan_branch": self._plan_branch}
                    with contextlib.suppress(GitCommandError):
                        context["files"] = [
                            line.strip()
                            for line in _run_git(
                                "diff", "--name-only", "--diff-filter=U", cwd=worktree_path
                            ).splitlines()
                            if line.strip()
                        ][:50]
                    with contextlib.suppress(GitCommandError):
                        context["worktree_sha"] = _run_git(
                            "rev-parse", "HEAD", cwd=worktree_path
                        ).strip()
                    with contextlib.suppress(GitCommandError):
                        context["branch_sha"] = _run_git(
                            "rev-parse", "FETCH_HEAD", cwd=worktree_path
                        ).strip()
                    with contextlib.suppress(GitCommandError):
                        _run_git("rebase", "--abort", cwd=worktree_path)
                    conflict_error = GitCommandError(
                        f"push_review_to_bare: rebase onto {self._plan_branch} conflicted "
                        f"(another task changed the same lines): {rebase_exc}"
                    )
                    conflict_error.conflict_context = context  # type: ignore[attr-defined]
                    raise conflict_error from rebase_exc
        # Exhausted retries — persistent contention; surface the last push error.
        assert last_exc is not None  # the loop only exits here via a non-ff push
        raise last_exc

    def push_branch_to_remote(self, *, force: bool = False) -> bool:
        """bare → remote. Gated by ``branch_push_mode``.

        ``incremental`` pushes every time the bare's branch advances
        (one push per accepted task). ``final_only`` skips here and
        is invoked once at plan close. Returns True iff a push
        actually happened.
        """
        if self._policies.branch_push_mode == "final_only" and not force:
            return False
        # If the bare has no ``origin``, treat that as "local-only
        # project" — the user gets the rama in the bare and no
        # remote step.
        if not self._has_origin():
            return False
        _run_git(
            "push",
            "origin",
            f"refs/heads/{self._plan_branch}:refs/heads/{self._plan_branch}",
            cwd=self._bare_path,
            env_extra=self._auth_env,  # ADR 0072: auth (PAT/SSH) si la hay
        )
        return True

    def _has_origin(self) -> bool:
        try:
            _run_git("remote", "get-url", "origin", cwd=self._bare_path)
        except GitCommandError:
            return False
        return True

    # ----- task_06_24 — PR creation ------------------------------------

    def open_plan_pr(self, *, title: str, body: str) -> PrInfo:
        """Open one PR for this repo at plan close.

        Skipped (with a reason) when:
          * ``push_policy='forbidden'`` — the project never pushes.
          * No ``origin`` remote — local-only project.
          * No ``pr_opener`` injected — dev/test without GitHub creds.

        Otherwise calls the injected opener and returns the URL it
        produced. The actual ``gh pr create`` machinery lives in the
        platform's wiring; here we just dispatch.
        """
        # Force the final push when in final_only mode.
        if self._policies.branch_push_mode == "final_only":
            self.push_branch_to_remote(force=True)

        if self._policies.push_policy == "forbidden":
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason="push_policy=forbidden",
            )
        if not self._has_origin():
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason="no remote origin configured",
            )
        if self._pr_opener is None:
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason="no pr_opener wired",
            )

        guard = self._base_ancestry_guard()
        if guard is not None:
            _log.warning(
                "plan_pr.base_guard_skip",
                repo=self._bare_path.stem,
                branch=self._plan_branch,
                reason=guard,
            )
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason=guard,
            )

        url = self._pr_opener(title, body)
        _log.info(
            "plan_pr.opened",
            repo=self._bare_path.stem,
            branch=self._plan_branch,
            url=url,
        )
        return PrInfo(
            repo_name=self._bare_path.stem,
            branch=self._plan_branch,
            url=url,
        )

    # ----- task_06_25 — push policy at merge time ----------------------

    def apply_push_policy(self, *, default_branch: str = "main") -> str:
        """Apply ``push_policy`` to the merge step. Returns the action
        actually taken: ``"forbidden"``, ``"pr_required"``, or
        ``"merged_to_default"``.

        * ``forbidden`` — does nothing, returns ``"forbidden"``. The
          plan branch lives on the bare (and possibly the remote)
          forever.
        * ``branch_only_pr_required`` — leaves the PR open for the
          human; returns ``"pr_required"``.
        * ``direct_to_default_allowed`` — fast-forwards the bare's
          default branch to the plan branch's tip. Returns
          ``"merged_to_default"``. The remote push of the new default
          tip is a separate concern (whoever owns CI handles it).
        """
        if self._policies.push_policy == "forbidden":
            return "forbidden"
        if self._policies.push_policy == "branch_only_pr_required":
            return "pr_required"
        # direct_to_default_allowed → fast-forward the bare's default.
        _run_git(
            "update-ref",
            f"refs/heads/{default_branch}",
            f"refs/heads/{self._plan_branch}",
            cwd=self._bare_path,
        )
        _log.info(
            "plan_git.merged_to_default",
            default=default_branch,
            plan_branch=self._plan_branch,
        )
        return "merged_to_default"


__all__ = [
    "BranchPushMode",
    "CommitTrailers",
    "PlanGitPolicies",
    "PlanGitWorkflow",
    "PlanValidationMode",
    "PrInfo",
    "PrOpener",
    "PushPolicy",
    "commit_task",
    "make_plan_branch_name",
]
