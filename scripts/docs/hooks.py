"""MkDocs build hooks for the platform documentation site.

Two jobs, both of them here so that ``mkdocs build --strict`` can stay strict
without anybody having to touch the existing Spanish corpus (160 ADR documents,
108 gotchas, 109 roadmap documents). Moving or rewriting those is explicitly out
of scope for the wave that introduced this site, and both hooks exist precisely
so that it does not have to happen.

``on_config`` -- **expand the auto-generated navigation sections.**
    ``mkdocs.yml`` cannot list the corpus by hand: a hand-written nav of ~690
    documents rots on the first commit that adds an ADR, and the indexes that
    would otherwise carry the job do not carry it -- ``05-architecture-
    decisions/README.md`` is a one-line stub that lists **zero** of its 160
    ADRs, and ``03-guides/gotchas/README.md`` is missing 22 of its 108 entries
    (both measured 2026-08-21). So a nav entry of the form ``auto: <glob>`` is
    replaced at build time by the sorted list of documents the glob matches.
    Generated from the filesystem, it cannot go stale.

    Expansion **de-duplicates against the hand-written nav**: a document placed
    explicitly (say ``02-getting-started/01-installation.md`` under "Start
    here") is skipped by any later glob that would also match it. That is what
    makes it possible to organise the front of the site by what a reader is
    looking for while still guaranteeing that every remaining document lands
    somewhere -- curated placement wins, the globs sweep up the rest, and no
    page is either orphaned or listed twice.

``on_page_markdown`` -- **make every link on the site resolve.**
    The corpus cites source code by relative path (for instance
    ``../../apps/api-server/src/api_server/db/domain.py`` with an ``#L120-L130``
    anchor): 210 such citations, which is the ADR convention here and is *not* a
    defect. But those paths point outside ``docs_dir``, so on a published
    website they are dead, and to MkDocs they are indistinguishable from a
    broken document link -- both land in ``validation.links.not_found`` (the
    discriminator in ``mkdocs/structure/pages.py`` is only whether the last path
    segment has an extension). Lowering that setting to silence them would also
    silence the broken-document-link check, which is the one thing ``--strict``
    is here to catch.

    So instead of silencing anything, this hook resolves each link against what
    the build actually contains. One rule, in four cases, applied in this order:

    1. a page of this build -> untouched, MkDocs owns it;
    2. the other language's half of a bilingual pair (``[Español](./foo.es.md)``,
       which the i18n plugin removed from this build) -> the sibling language's
       URL, so the cross-link the policy mandates actually resolves;
    3. a real file in the repository that is not a page of this build -- a source
       citation, a document excluded from the site, anything outside ``docs/`` --
       -> that file's canonical GitHub URL, line anchor included;
    4. resolves to nothing -> a **document** link is left exactly as written so
       ``--strict`` fails on it; a **source citation** stops being a link at all
       and degrades to a code span, counted and logged.

    Case 4 is the one that carries the honesty of the whole arrangement, in both
    halves. A broken link between documents must never be rescued here, or the
    gate stops being a gate. And a citation whose target no longer exists (three
    of them today, left by source files that moved into packages in
    ``d874b641``) must not render as a hyperlink that 404s: this is a repository
    that spent two days removing measures that lie. It renders as inline code,
    and the count is logged at the end of the build so it is visible rather than
    swallowed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("mkdocs.hooks.docs_site")

#: Repository root: ``scripts/docs/hooks.py`` -> up three.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Canonical blob base. The default branch of this repo is ``master``, not
#: ``main`` (see ``tests/docs/test_ci_workflows.py``).
BLOB_BASE = "https://github.com/daycry/agent-ai-multitenant/blob/master"

#: Nav entries of this shape are expanded from the filesystem.
AUTO_PREFIX = "auto:"

#: A Markdown inline link that is not an image: ``[text](target)``.
_LINK_RE = re.compile(r"(?<!!)\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)\)")

#: Counts for the end-of-build summary. A build is a single process, so module
#: state spans exactly one run.
_stats: dict[str, int] = {"rewritten": 0, "degraded": 0}

#: Citations that resolve to nothing, reported once per build with their origin.
_dead: list[str] = []

#: Single-slot memo of "which documents are in the build now under way", keyed by
#: the identity of MkDocs' own ``Files`` collection.
#:
#: The set is read from the ``files`` argument of ``on_page_markdown`` and NOT
#: from an ``on_files`` hook, which is where the first version of this got it
#: wrong: hook order is not guaranteed, so ``on_files`` ran before the i18n
#: plugin had removed the other language's sidecars, every cross-language link
#: looked like a page of this build, and all five of them stayed broken. By the
#: time pages render, the collection is final. Keeping a strong reference to the
#: collection is what makes identity a safe key — a dead object's ``id`` could
#: be reused by the second language's build.
_pages_memo: list[tuple[Any, set[str]]] = []

#: Language sidecar suffixes, per docs/03-guides/bilingual-docs.md.
_SIDECAR_RE = re.compile(r"^(?P<stem>.+)\.(?P<lang>es|en)\.md$")


# ---------------------------------------------------------------------------
# on_config: expand `auto:` nav sections
# ---------------------------------------------------------------------------
def is_translation(path: Path) -> bool:
    """True for the Spanish half of a bilingual pair, which the nav must skip.

    Listing ``foo.es.md`` would duplicate the entry: the i18n plugin already
    serves it as the Spanish rendition of ``foo``. The convention is the one
    written in ``docs/03-guides/bilingual-docs.md`` — the bare name is the
    English canonical document, ``.es.md`` its sidecar.

    ``foo.en.md`` is *not* skipped. It is the English half, so it is the one
    that belongs in the nav. Nothing in the corpus is named that way right now
    (``01-overview/03-diagrams`` briefly was, before being realigned to the bare
    name on 2026-08-21), but the i18n plugin accepts the spelling, so a nav
    generator that crashed or silently dropped a page on it would be a trap
    waiting for whoever writes the next pair.
    """
    return path.name.endswith(".es.md")


def expand_glob(docs_dir: Path, pattern: str, already_placed: set[str]) -> list[str]:
    """The documents matched by ``pattern``, as nav-ready relative POSIX paths.

    Skipped on purpose: ``README.md`` (the nav section already carries a
    heading, and for the two biggest corpora the README is a stub that would
    only add a useless first entry), the ``.es.md`` translations, and anything
    already placed by hand or by an earlier glob.
    """
    out: list[str] = []
    for path in sorted(docs_dir.glob(pattern)):
        if not path.is_file() or path.name.lower() == "readme.md" or is_translation(path):
            continue
        rel = path.relative_to(docs_dir).as_posix()
        if rel in already_placed:
            continue
        already_placed.add(rel)
        out.append(rel)
    return out


def auto_pattern(item: Any) -> str | None:
    """The glob of an auto-nav marker, or ``None`` if ``item`` is not one.

    Both YAML spellings are accepted, because both are what a human writes:

    .. code-block:: yaml

        - "auto: 05-architecture-decisions/*.md"   # a scalar
        - auto: 05-architecture-decisions/*.md     # a single-key mapping

    Unquoted, YAML parses the second as a mapping, and an earlier version of
    this hook only understood the first — so the marker was silently taken for
    a nav title and every generated section came out empty. Accepting both
    removes the trap instead of documenting it.
    """
    if isinstance(item, str) and item.startswith(AUTO_PREFIX):
        return item[len(AUTO_PREFIX) :].strip()
    if isinstance(item, dict) and len(item) == 1:
        key, value = next(iter(item.items()))
        marker = AUTO_PREFIX.rstrip(":")
        if isinstance(key, str) and key.strip() == marker and isinstance(value, str):
            return value.strip()
    return None


def collect_explicit(node: Any) -> set[str]:
    """Every document path written by hand in the nav (i.e. not an ``auto:``)."""
    found: set[str] = set()
    if auto_pattern(node) is not None:
        return found
    if isinstance(node, str):
        found.add(node)
    elif isinstance(node, list):
        for item in node:
            found |= collect_explicit(item)
    elif isinstance(node, dict):
        for value in node.values():
            found |= collect_explicit(value)
    return found


def _walk_nav(node: Any, docs_dir: Path, expanded: dict[str, int], already_placed: set[str]) -> Any:
    """Rewrite ``auto:`` markers anywhere in the nav tree, depth-first."""
    if isinstance(node, list):
        out: list[Any] = []
        for item in node:
            pattern = auto_pattern(item)
            if pattern is not None:
                pages = expand_glob(docs_dir, pattern, already_placed)
                expanded[pattern] = len(pages)
                out.extend(pages)
            else:
                out.append(_walk_nav(item, docs_dir, expanded, already_placed))
        return out
    if isinstance(node, dict):
        return {
            key: _walk_nav(value, docs_dir, expanded, already_placed) for key, value in node.items()
        }
    return node


def on_config(config: Any, **_kwargs: Any) -> Any:
    """Expand every ``auto: <glob>`` nav entry against ``docs_dir``."""
    if not config.get("nav"):
        return config
    docs_dir = Path(config["docs_dir"])
    expanded: dict[str, int] = {}
    already_placed = collect_explicit(config["nav"])
    config["nav"] = _walk_nav(config["nav"], docs_dir, expanded, already_placed)

    # A generator that silently stops finding documents would leave whole
    # sections of the site empty and the build still green -- the failure mode
    # docs/03-guides/verificar-antes-de-implementar.md section 4 is about.
    # Refuse to build instead.
    empty = sorted(pattern for pattern, count in expanded.items() if count == 0)
    if empty:
        raise SystemExit(
            "docs site: these auto-nav globs matched no document, so their nav "
            f"section would be empty: {empty}. Either the corpus moved or the "
            "glob is wrong; an empty section is not an acceptable build."
        )
    total = sum(expanded.values())
    log.info(
        "docs site: auto-nav expanded %d documents across %d sections",
        total,
        len(expanded),
    )
    return config


# ---------------------------------------------------------------------------
# on_page_markdown: source citations -> GitHub URLs
# ---------------------------------------------------------------------------
def resolve_in_repo(page_dir: Path, target_path: str) -> Path | None:
    """Resolve a citation to a real repository file, or ``None``.

    Two spellings are honoured because the corpus uses both: relative to the
    citing document (``../../apps/...``, 163 citations) and relative to the
    repository root (``apps/...``, 47 citations).
    """
    for candidate in (page_dir / target_path, REPO_ROOT / target_path):
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - defensive on exotic paths
            continue
        if resolved.is_file() and REPO_ROOT in resolved.parents:
            return resolved
    return None


def names_a_file(target_path: str) -> bool:
    """Whether the target names a file rather than a directory.

    The same question MkDocs asks to choose between ``not_found`` and
    ``unrecognized_links``, but asked correctly. MkDocs looks for a dot in the
    last segment, which reads ``../../`` — a link to the repository root, three
    of them in one roadmap document — as the file ``..`` with extension ``.``.
    An earlier version of this hook copied that shortcut and degraded those three
    links to code spans, which is not wrong enough to notice and not right.
    """
    if target_path.endswith("/"):
        return False
    last = target_path.rsplit("/", 1)[-1]
    if set(last) <= {"."}:  # "." and ".." name directories, not files
        return False
    return "." in last


def docs_relative(docs_dir: Path, page_dir: Path, target_path: str) -> str | None:
    """The target as a ``docs/``-relative POSIX path, or ``None`` if it escapes."""
    candidate = (page_dir / target_path).resolve()
    try:
        return candidate.relative_to(docs_dir).as_posix()
    except ValueError:
        return None


def page_url(src_uri: str) -> str:
    """The site-relative URL MkDocs gives a document, with directory URLs on.

    ``index.md`` -> ``""``; ``01-overview/README.md`` -> ``"01-overview/"``;
    ``03-guides/bilingual-docs.md`` -> ``"03-guides/bilingual-docs/"``.
    """
    stem = src_uri[: -len(".md")]
    parts = stem.split("/")
    if parts[-1] in ("index", "README"):
        parts.pop()
    return "".join(f"{part}/" for part in parts)


def cross_language_url(base_path: str, docs_rel: str, pages: set[str]) -> str | None:
    """Where the *other* language's rendition of ``docs_rel`` lives, or ``None``.

    The header cross-link the bilingual policy requires (``[Español](./foo.es.md)``)
    points at a file that, by construction, is **not** in the build that needs the
    link: the i18n plugin removes ``foo.es.md`` from the English site and the bare
    ``foo.md`` from the Spanish one. Left alone the link is a broken-document
    warning, so `--strict` would make the policy and the site mutually exclusive.

    It resolves instead to the sibling site: the Spanish rendition is always at
    ``<base>/es/<url>`` and the English canonical always at ``<base>/<url>``,
    which is true regardless of which language is currently building — so this
    needs no knowledge of the active locale.
    """
    match = _SIDECAR_RE.match(docs_rel)
    if match:
        url = page_url(f"{match.group('stem')}.md")
        # English is the default build, so it has no locale prefix.
        prefix = "" if match.group("lang") == "en" else "es/"
        return f"{base_path}{prefix}{url}"
    # A bare name missing from *this* build, whose Spanish sidecar exists: this is
    # the Spanish build linking back to the English canonical.
    if f"{docs_rel[: -len('.md')]}.es.md" in pages:
        return f"{base_path}{page_url(docs_rel)}"
    return None


def rewrite_links(
    markdown: str,
    docs_dir: Path,
    page_dir: Path,
    origin: str,
    base_path: str = "/",
    build_files: set[str] | None = None,
) -> str:
    """Apply the site's one link rule to a page's Markdown.

    The order of the branches is the whole design, so it is worth stating: a
    broken link **between documents** is always left exactly as written, because
    that is the defect ``--strict`` exists to report. Nothing in here may rescue
    it, or the gate stops being a gate.
    """
    pages = set() if build_files is None else build_files

    def github(text: str, resolved: Path, suffix: str) -> str:
        rel = resolved.relative_to(REPO_ROOT).as_posix()
        _stats["rewritten"] += 1
        return f"[{text}]({BLOB_BASE}/{rel}{suffix})"

    def document(text: str, path_part: str, suffix: str, original: str) -> str:
        """Case 1, 2 and the document half of case 4."""
        docs_rel = docs_relative(docs_dir, page_dir, path_part)
        # A page of this build: MkDocs resolves it, broken or not.
        if docs_rel is not None and docs_rel in pages:
            return original
        if docs_rel is not None:
            # The other language's half of a bilingual pair.
            alternate = cross_language_url(base_path, docs_rel, pages)
            if alternate is not None:
                return f"[{text}]({alternate}{suffix})"
        # A real document this build excludes (docs/README.md), or one outside
        # docs/ altogether (CLAUDE.md): both are readable in the repository, so
        # that is where the link goes.
        resolved = resolve_in_repo(page_dir, path_part)
        if resolved is not None:
            return github(text, resolved, suffix)
        # Resolves to nothing: a broken document link. Left untouched on purpose,
        # so `mkdocs build --strict` fails on it.
        return original

    def citation(text: str, path_part: str, suffix: str, target: str) -> str:
        """Case 3 and the citation half of case 4."""
        resolved = resolve_in_repo(page_dir, path_part)
        if resolved is not None:
            return github(text, resolved, suffix)
        # Refuse to publish it as a link. It becomes visible text, and
        # on_post_build reports the count.
        _stats["degraded"] += 1
        _dead.append(f"{origin} -> {target}")
        label = text.strip("`") or target
        return f"`{label}`"

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        text, target = match.group("text"), match.group("target")
        if target.startswith(("http://", "https://", "mailto:", "#", "<", "/")):
            return original

        path_part, _, anchor = target.partition("#")
        if not path_part:
            return original
        suffix = f"#{anchor}" if anchor else ""

        if path_part.endswith(".md"):
            return document(text, path_part, suffix, original)

        # Directory links (`../04-reference/`, `../../`) are not citations; MkDocs
        # classes them as `unrecognized_links` (info) and they resolve on the
        # built site. Leave them alone.
        if not names_a_file(path_part):
            return original

        return citation(text, path_part, suffix, target)

    return _LINK_RE.sub(replace, markdown)


def pages_in_build(files: Any) -> set[str]:
    """The ``src_uri`` of every document in the build now under way.

    Asking MkDocs beats re-deriving it from the filesystem: the answer already
    accounts for ``exclude_docs`` and for the i18n plugin having removed the
    other language's sidecars, and neither is worth reimplementing.
    """
    if _pages_memo and _pages_memo[0][0] is files:
        return _pages_memo[0][1]
    pages = {file.src_uri.replace("\\", "/") for file in files if file.src_uri.endswith(".md")}
    _pages_memo.clear()
    _pages_memo.append((files, pages))
    return pages


def _base_path(config: Any) -> str:
    """The site's root path (``/agent-ai-multitenant/``) from ``site_url``."""
    site_url = config.get("site_url") or "/"
    path = urlsplit(site_url).path or "/"
    return path if path.endswith("/") else f"{path}/"


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any, **_kwargs: Any) -> str:
    docs_dir = Path(config["docs_dir"]).resolve()
    src_uri = page.file.src_uri.replace("\\", "/")
    page_dir = (docs_dir / src_uri).parent
    return rewrite_links(
        markdown,
        docs_dir,
        page_dir,
        src_uri,
        _base_path(config),
        pages_in_build(files),
    )


def on_post_build(config: Any, **_kwargs: Any) -> None:
    """Report what the link rule did, so neither half is invisible."""
    log.info("docs site: built into %s", config.get("site_dir"))
    log.info(
        "docs site: %d source citations rewritten to GitHub, %d degraded to code spans",
        _stats["rewritten"],
        _stats["degraded"],
    )
    for entry in sorted(set(_dead)):
        log.info("docs site: dead source citation (rendered as code, not a link): %s", entry)
