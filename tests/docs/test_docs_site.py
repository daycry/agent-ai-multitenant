"""Static guards for the MkDocs documentation site (mkdocs.yml + docs.yml).

The site is built by `.github/workflows/docs.yml` with `mkdocs build --strict`,
which is the real gate: it fails on a broken link between documents, on a nav
entry pointing at a file that no longer exists, and on an auto-nav glob that
stopped matching anything. These tests are the part of that gate that must not
require a 3-minute build and a runner to notice — they parse the YAML and the
hook directly, so they run anywhere, offline, in milliseconds.

What each guard is actually protecting against, since a guard whose reason is
lost gets deleted by the next person who finds it inconvenient:

* **Pinned toolchain.** `docs/03-guides/gotchas/ci-tool-version-drift.md` is this
  repo's account of a `rev` that did not pin the tool: it passed locally and
  rewrote 16 files in CI with the same declared version on both sides. A docs
  build whose renderer depends on the day the runner cache was created has the
  same defect.
* **Actions pinned by SHA.** Every other workflow in this repo pins by commit,
  not by tag, because a tag is mutable and a docs job that can publish to Pages
  is a supply-chain target.
* **Least privilege.** Only the `deploy` job may hold `pages: write` /
  `id-token: write`. A pull-request build that could publish would make every
  docs dependency a deployment key.
* **`--strict` present.** Without it MkDocs downgrades every one of the checks
  above to a log line nobody reads, and the site ships with holes.
* **Nav targets exist.** A hand-written nav entry is the one part of the nav the
  hook cannot generate, so it is the one part that can rot.

Each guard carries a "found something" assertion. A discovery that quietly stops
finding anything passes green forever, which is the failure mode
`docs/03-guides/verificar-antes-de-implementar.md` §4 is about.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MKDOCS_YML = _REPO_ROOT / "mkdocs.yml"
_DOCS_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "docs.yml"
_REQUIREMENTS = _REPO_ROOT / "requirements-docs.txt"
_HOOKS = _REPO_ROOT / "scripts" / "docs" / "hooks.py"
_DOCS = _REPO_ROOT / "docs"

#: A 40-hex commit SHA, which is how every workflow in this repo pins an action.
_SHA_PIN_RE = re.compile(r"^[^@]+@[0-9a-f]{40}$")

#: `uses:` lines of a workflow.
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Loading mkdocs.yml: it carries MkDocs' `!!python/name:` tags, which
# yaml.safe_load refuses. Resolve them to their literal string instead of
# importing anything — this is a parser for assertions, not a plugin loader.
# ---------------------------------------------------------------------------
class _MkDocsTagLoader(yaml.SafeLoader):
    pass


def _python_name(loader: yaml.Loader, suffix: str, node: yaml.Node) -> str:
    return f"!!python/name:{suffix}"


_MkDocsTagLoader.add_multi_constructor("tag:yaml.org,2002:python/name:", _python_name)
_MkDocsTagLoader.add_multi_constructor("!!python/name:", _python_name)


@pytest.fixture(scope="module")
def mkdocs_config() -> dict[str, Any]:
    assert _MKDOCS_YML.is_file(), "mkdocs.yml is missing: the docs site has no configuration"
    data = yaml.load(_MKDOCS_YML.read_text(encoding="utf-8"), Loader=_MkDocsTagLoader)
    assert isinstance(data, dict), "mkdocs.yml top-level YAML is not a mapping"
    return data


@pytest.fixture(scope="module")
def docs_workflow() -> dict[str, Any]:
    assert _DOCS_WORKFLOW.is_file(), ".github/workflows/docs.yml is missing"
    data = yaml.safe_load(_DOCS_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "docs.yml top-level YAML is not a mapping"
    # PyYAML resolves the bare key `on:` to the boolean True (YAML 1.1).
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


@pytest.fixture(scope="module")
def hooks_module() -> Any:
    """Import `scripts/docs/hooks.py` by path — it is a script, not a package."""
    spec = importlib.util.spec_from_file_location("docs_site_hooks", _HOOKS)
    assert spec is not None and spec.loader is not None, f"cannot load {_HOOKS}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The build gate
# ---------------------------------------------------------------------------
def test_workflow_builds_the_site_strictly(docs_workflow: dict[str, Any]) -> None:
    """The build step must pass `--strict`, or none of the link checks fail a run."""
    runs = "\n".join(
        step.get("run", "")
        for job in docs_workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )
    assert "mkdocs build" in runs, "docs.yml never runs `mkdocs build`"
    assert "--strict" in runs, (
        "docs.yml must run `mkdocs build --strict`: without it a broken internal "
        "link, a missing nav target and an empty auto-nav section are all just "
        "log lines, and the site publishes with holes in it"
    )


def test_workflow_installs_the_pinned_requirements(docs_workflow: dict[str, Any]) -> None:
    runs = "\n".join(
        step.get("run", "")
        for job in docs_workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )
    assert "requirements-docs.txt" in runs, (
        "the docs job must install from requirements-docs.txt, not from loose "
        "`pip install mkdocs-material` — see that file's header"
    )


def test_docs_requirements_are_fully_pinned() -> None:
    """Every dependency of the site is pinned to an exact version."""
    lines = [
        line.strip()
        for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) >= 20, (
        f"requirements-docs.txt lists only {len(lines)} dependencies; the pinned "
        "set including transitive dependencies was 30 when written — did the "
        "file get reduced to direct dependencies only?"
    )
    loose = [line for line in lines if "==" not in line]
    assert not loose, (
        f"unpinned dependencies in requirements-docs.txt: {loose}. A docs build "
        "that renders differently depending on the runner cache date is the "
        "trap of docs/03-guides/gotchas/ci-tool-version-drift.md"
    )
    # The three direct dependencies must be there by name: dropping one silently
    # would change what the site is.
    joined = "\n".join(lines)
    for package in ("mkdocs==", "mkdocs-material==", "mkdocs-static-i18n=="):
        assert package in joined, f"requirements-docs.txt no longer pins {package!r}"


# ---------------------------------------------------------------------------
# Supply chain + least privilege
# ---------------------------------------------------------------------------
def test_docs_workflow_pins_every_action_by_sha() -> None:
    """Actions are pinned by commit SHA, like every other workflow here."""
    text = _DOCS_WORKFLOW.read_text(encoding="utf-8")
    uses = _USES_RE.findall(text)
    assert len(uses) >= 4, (
        f"only {len(uses)} `uses:` found in docs.yml — the parser stopped seeing "
        "them, so a green result here would mean nothing"
    )
    unpinned = [ref for ref in uses if not _SHA_PIN_RE.match(ref)]
    assert not unpinned, (
        f"actions not pinned to a 40-hex commit SHA in docs.yml: {unpinned}. "
        "A tag is mutable, and this workflow can publish to GitHub Pages"
    )


def test_only_the_deploy_job_can_write(docs_workflow: dict[str, Any]) -> None:
    """`pages: write` / `id-token: write` belong to the deploy job alone."""
    top = docs_workflow.get("permissions")
    assert top == {"contents": "read"}, (
        f"docs.yml top-level permissions must be exactly {{contents: read}}, got {top!r}: "
        "a workflow-wide write scope would give every pull-request build the "
        "ability to publish"
    )

    jobs = docs_workflow["jobs"]
    assert "deploy" in jobs, "docs.yml has no 'deploy' job"

    writers = {
        name: job.get("permissions", {})
        for name, job in jobs.items()
        if any(value == "write" for value in (job.get("permissions") or {}).values())
    }
    assert set(writers) == {"deploy"}, (
        f"only the deploy job may hold write permissions, but these do: {sorted(writers)}"
    )
    deploy_perms = jobs["deploy"]["permissions"]
    assert deploy_perms.get("pages") == "write", "the deploy job needs `pages: write`"
    assert deploy_perms.get("id-token") == "write", (
        "the deploy job needs `id-token: write` so deploy-pages can mint the OIDC "
        "token Pages verifies"
    )


def test_deploy_only_runs_from_the_default_branch(docs_workflow: dict[str, Any]) -> None:
    """A pull request must never publish the site."""
    condition = str(docs_workflow["jobs"]["deploy"].get("if", ""))
    assert "refs/heads/master" in condition, (
        "the deploy job must be gated on the default branch (master); without the "
        f"guard any branch could publish. Found: {condition!r}"
    )
    assert "pull_request" in condition, (
        "the deploy job must exclude pull_request events explicitly; a fork PR "
        f"otherwise reaches the publish step. Found: {condition!r}"
    )


# ---------------------------------------------------------------------------
# mkdocs.yml itself
# ---------------------------------------------------------------------------
def test_site_is_bilingual_with_english_canonical(mkdocs_config: dict[str, Any]) -> None:
    """English default + Spanish alternate, in `suffix` mode.

    Pins the operator decision of 2026-08-21 and the convention of
    docs/03-guides/bilingual-docs.md. `suffix` mode is load-bearing: the
    directory mode would require moving the whole Spanish corpus.
    """
    i18n = next(
        (
            plugin["i18n"]
            for plugin in mkdocs_config["plugins"]
            if isinstance(plugin, dict) and "i18n" in plugin
        ),
        None,
    )
    assert i18n is not None, (
        "mkdocs.yml no longer configures the i18n plugin: the site is not bilingual"
    )
    assert i18n["docs_structure"] == "suffix", (
        "i18n must stay in `suffix` mode — the `directory` mode requires relocating "
        "160 ADRs, 108 gotchas and the roadmap, which docs/03-guides/bilingual-docs.md "
        "rules out"
    )
    locales = {language["locale"]: language for language in i18n["languages"]}
    assert set(locales) == {"en", "es"}, f"expected exactly en + es, got {sorted(locales)}"
    assert locales["en"].get("default") is True, "English must be the canonical (default) language"
    assert not locales["es"].get("default"), "Spanish must not be the default language"


def test_strict_link_validation_is_not_downgraded(mkdocs_config: dict[str, Any]) -> None:
    """`links.not_found` must stay at `warn`, which with --strict means fail.

    This is the single setting that makes the site's link gate real. Lowering it
    to `info` would silence broken document links along with the source-code
    citations — which is exactly why those are handled in the build hook instead.
    """
    links = mkdocs_config["validation"]["links"]
    assert links["not_found"] == "warn", (
        f"validation.links.not_found is {links['not_found']!r}, not 'warn': broken "
        "links between documents would no longer fail `mkdocs build --strict`. "
        "Source-code citations are handled in scripts/docs/hooks.py, not here"
    )
    nav = mkdocs_config["validation"]["nav"]
    assert nav["omitted_files"] == "warn", (
        "validation.nav.omitted_files must stay at 'warn': it is what guarantees "
        "every built document is reachable from the navigation"
    )


def test_the_heavy_non_documentation_paths_are_excluded(mkdocs_config: dict[str, Any]) -> None:
    """`docs/manuals/` must not be published: it is 132 MB of generated assets."""
    excluded = mkdocs_config["exclude_docs"]
    for path in ("manuals/", "provider-example/", "/README.md"):
        assert path in excluded, (
            f"mkdocs.yml no longer excludes {path!r} from the site. `manuals/` alone "
            "is 132 MB of PDFs, Playwright fixtures and a vendored node_modules; "
            "`/README.md` would otherwise become the site's front page instead of "
            "index.md"
        )


def test_mermaid_is_wired(mkdocs_config: dict[str, Any]) -> None:
    """Nine documents ship ```mermaid blocks; the site has to render them."""
    superfences = next(
        (
            extension["pymdownx.superfences"]
            for extension in mkdocs_config["markdown_extensions"]
            if isinstance(extension, dict) and "pymdownx.superfences" in extension
        ),
        None,
    )
    assert superfences is not None, "pymdownx.superfences is not configured"
    fences = {fence["name"] for fence in superfences["custom_fences"]}
    assert "mermaid" in fences, (
        "no `mermaid` custom fence: the diagrams in the architecture overview, the "
        "human-agents guide and ADR 0046 would render as unreadable code blocks"
    )

    with_mermaid = [
        path
        for path in _DOCS.rglob("*.md")
        if "node_modules" not in path.parts
        and "```mermaid" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert len(with_mermaid) >= 9, (
        f"only {len(with_mermaid)} documents with mermaid blocks found (expected >= 9): "
        "the discovery broke, so this guard would pass vacuously"
    )


def test_every_hand_written_nav_target_exists(
    mkdocs_config: dict[str, Any], hooks_module: Any
) -> None:
    """The hand-written half of the nav is the half that can rot."""
    explicit = hooks_module.collect_explicit(mkdocs_config["nav"])
    assert len(explicit) >= 15, (
        f"only {len(explicit)} hand-written nav entries found (expected >= 15): the "
        "nav parser stopped seeing them"
    )
    missing = sorted(entry for entry in explicit if not (_DOCS / entry).is_file())
    assert not missing, (
        f"mkdocs.yml nav points at documents that do not exist: {missing}. "
        "`mkdocs build --strict` would fail on these"
    )


def test_every_auto_nav_glob_matches_documents(
    mkdocs_config: dict[str, Any], hooks_module: Any
) -> None:
    """An auto-nav glob that matches nothing would publish an empty section."""
    patterns = _auto_patterns(mkdocs_config["nav"], hooks_module)
    assert len(patterns) >= 8, (
        f"only {len(patterns)} auto-nav globs found (expected >= 8): the marker "
        "syntax changed and this guard stopped checking anything"
    )
    already: set[str] = set()
    empty = [
        pattern for pattern in patterns if not hooks_module.expand_glob(_DOCS, pattern, already)
    ]
    assert not empty, (
        f"these auto-nav globs match no document, so their site section would be empty: {empty}"
    )


def _auto_patterns(node: Any, hooks_module: Any) -> list[str]:
    pattern = hooks_module.auto_pattern(node)
    if pattern is not None:
        return [pattern]
    if isinstance(node, list):
        return [p for item in node for p in _auto_patterns(item, hooks_module)]
    if isinstance(node, dict):
        return [p for value in node.values() for p in _auto_patterns(value, hooks_module)]
    return []


# ---------------------------------------------------------------------------
# The hook's link rule
# ---------------------------------------------------------------------------
#: The documents MkDocs would report as being in an English build, for the pure
#: link-rule tests. Only membership matters, so a hand-written set is enough —
#: and it keeps these tests independent of a real 3-minute build.
_PAGES = {
    "index.md",
    "03-guides/bilingual-docs.md",
    "01-overview/03-diagrams.md",
}


def _rewrite(hooks_module: Any, markdown: str, folder: str) -> str:
    return hooks_module.rewrite_links(
        markdown, _DOCS, _DOCS / folder, "origin.md", "/agent-ai-multitenant/", _PAGES
    )


def test_a_real_source_citation_becomes_a_github_link(hooks_module: Any) -> None:
    """A citation of a file that exists is rewritten to a working GitHub URL.

    The corpus cites source by relative path, which is dead on a website. This
    is the half that makes those 210 links resolve.
    """
    out = _rewrite(
        hooks_module,
        "see [`pyproject`](../../pyproject.toml#L1-L3) for the shape",
        "05-architecture-decisions",
    )
    expected = "https://github.com/daycry/agent-ai-multitenant/blob/master/pyproject.toml#L1-L3"
    assert expected in out, f"a citation of an existing repository file was not rewritten: {out!r}"


def test_a_dead_source_citation_stops_being_a_link(hooks_module: Any) -> None:
    """A citation that resolves to nothing must not publish as a hyperlink.

    Three of these exist today, left behind by source files that moved into
    packages. Rendering them as links would publish a 404 — the
    measure-that-lies pattern this repository has been removing.
    """
    out = _rewrite(
        hooks_module,
        "see [`gone.py`](../../apps/nowhere/gone.py#L10) for the shape",
        "05-architecture-decisions",
    )
    assert "](" not in out, f"a dead citation was published as a link: {out!r}"
    assert "`gone.py`" in out, f"the dead citation lost its text: {out!r}"


def test_a_broken_document_link_is_left_for_strict_to_catch(hooks_module: Any) -> None:
    """The one thing the hook may never rescue.

    If a broken `.md` link were rewritten or degraded here, `--strict` would
    never see it and the site's link gate would be decorative.
    """
    broken = "see [the policy](./does-not-exist.md)"
    assert _rewrite(hooks_module, broken, "03-guides") == broken


def test_real_pages_and_urls_are_left_alone(hooks_module: Any) -> None:
    """The hook must not touch what MkDocs already resolves correctly."""
    for markdown in (
        "see [the policy](./bilingual-docs.md)",
        "see [upstream](https://example.invalid/a.py)",
        "see [reference](../04-reference/)",
        "see [an anchor](#a-section)",
        # A link to the repository root. MkDocs' own heuristic reads this as the
        # file `..` with extension `.`; treating it as a citation degraded three
        # real links in the roadmap to code spans.
        "see [the repo](../../)",
    ):
        assert _rewrite(hooks_module, markdown, "03-guides") == markdown, markdown


def test_directory_and_file_targets_are_told_apart(hooks_module: Any) -> None:
    """The classifier that decides "citation or directory link"."""
    for directory in ("../04-reference/", "../../", "..", ".", "gotchas/"):
        assert not hooks_module.names_a_file(directory), directory
    for file_target in ("foo.py", "../../apps/x/y.py", "a.b/c.yml", "notes.md"):
        assert hooks_module.names_a_file(file_target), file_target


def test_a_language_sidecar_link_points_at_the_other_site(hooks_module: Any) -> None:
    """`[Español](./foo.es.md)` must resolve, not warn.

    The i18n plugin removes `foo.es.md` from the English build, so the header
    cross-link the bilingual policy mandates would otherwise be a broken-document
    warning — making the policy and `--strict` mutually exclusive.
    """
    out = _rewrite(hooks_module, "**English** · [Español](./bilingual-docs.es.md)", "03-guides")
    assert "(/agent-ai-multitenant/es/03-guides/bilingual-docs/)" in out, (
        f"the Spanish cross-link did not resolve to the Spanish site: {out!r}"
    )


def test_an_excluded_document_links_to_the_repository(hooks_module: Any) -> None:
    """`docs/README.md` is excluded from the site but is a real, readable file."""
    out = _rewrite(hooks_module, "see [the structure](../README.md)", "03-guides")
    assert "blob/master/docs/README.md" in out, (
        f"a link to an excluded-but-real document did not fall back to GitHub: {out!r}"
    )


def test_translations_are_never_listed_in_the_nav(hooks_module: Any) -> None:
    """`foo.es.md` is served by the i18n plugin, not navigated to directly."""
    assert hooks_module.is_translation(Path("docs/index.es.md")) is True
    assert hooks_module.is_translation(Path("docs/index.md")) is False
    # The English half of an explicitly-suffixed pair belongs in the nav.
    assert hooks_module.is_translation(Path("docs/01-overview/03-diagrams.en.md")) is False

    already: set[str] = set()
    guides = hooks_module.expand_glob(_DOCS, "03-guides/*.md", already)
    assert guides, "the guides glob matched nothing: this guard would pass vacuously"
    assert not [entry for entry in guides if entry.endswith(".es.md")], (
        f"the nav generator emitted Spanish sidecars: {guides}"
    )
