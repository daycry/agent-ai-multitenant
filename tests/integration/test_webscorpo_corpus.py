"""Corpus check for the WebScorpo KB seed (task_demo_ws_01).

The WebScorpo demo seed ships a markdown corpus under ``scripts/webscorpo/kb/``:
10 team-shared documents (``team/``) + one per-role document (``agents/<role>/``).
These tests assert every expected file exists, is non-empty, and carries a valid
YAML frontmatter block with the required keys. No DB/Redis is touched — this is a
pure on-disk corpus check, but it lives under ``tests/integration`` because it
backs the demo seed's integration suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Repo root: this file is tests/integration/test_webscorpo_corpus.py → up 2.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_KB_ROOT = _REPO_ROOT / "scripts" / "webscorpo" / "kb"

# The 10 team-shared documents (analysis §8), by stable filename.
TEAM_DOCS = (
    "01-project-overview.md",
    "02-architecture-map.md",
    "03-routing-and-filters.md",
    "04-data-model.md",
    "05-coding-standards-toolchain.md",
    "06-testing-strategy.md",
    "07-cicd-deploy-runbook.md",
    "08-i18n-policy.md",
    "09-security-baseline.md",
    "10-dependency-catalog.md",
)

# The 10 per-agent roles (analysis §7/§9); each has agents/<role>/role-knowledge.md.
AGENT_ROLES = (
    "pm",
    "architect",
    "backend",
    "dba",
    "frontend",
    "auth-security",
    "i18n",
    "qa",
    "reviewer",
    "devops",
)

_AGENT_DOC_NAME = "role-knowledge.md"

# Frontmatter keys that every corpus document must declare.
_REQUIRED_KEYS = ("title", "scope")


def _all_doc_paths() -> list[Path]:
    paths = [_KB_ROOT / "team" / name for name in TEAM_DOCS]
    paths += [_KB_ROOT / "agents" / role / _AGENT_DOC_NAME for role in AGENT_ROLES]
    return paths


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal ``key: value`` frontmatter parser for the leading ``---`` block.

    We avoid a YAML dependency on purpose: the corpus frontmatter is flat
    ``key: value`` pairs, so a line scanner is enough and keeps the test
    dependency-free.
    """
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "missing opening frontmatter fence"
    fields: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"').strip("'")
    assert closed, "missing closing frontmatter fence"
    return fields


def test_kb_root_exists() -> None:
    assert _KB_ROOT.is_dir(), f"missing corpus root: {_KB_ROOT}"


@pytest.mark.parametrize("name", TEAM_DOCS)
def test_team_doc_present(name: str) -> None:
    path = _KB_ROOT / "team" / name
    assert path.is_file(), f"missing team-shared doc: {path}"


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_agent_doc_present(role: str) -> None:
    path = _KB_ROOT / "agents" / role / _AGENT_DOC_NAME
    assert path.is_file(), f"missing per-agent doc: {path}"


def test_exactly_twenty_docs() -> None:
    md_files = sorted(_KB_ROOT.rglob("*.md"))
    assert len(md_files) == 20, f"expected 20 corpus docs, found {len(md_files)}: {md_files}"


@pytest.mark.parametrize("path", _all_doc_paths(), ids=lambda p: str(p.relative_to(_KB_ROOT)))
def test_doc_non_empty_with_valid_frontmatter(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    # Non-empty beyond just the frontmatter fences + a heading.
    assert len(text.strip()) > 200, f"corpus doc looks empty: {path}"
    fields = _parse_frontmatter(text)
    for key in _REQUIRED_KEYS:
        assert fields.get(key), f"{path} frontmatter missing '{key}'"
    # Body must contain a markdown heading after the frontmatter.
    assert "\n# " in text or text.lstrip().startswith("# "), f"{path} has no body heading"


def test_team_docs_scope_is_team_shared() -> None:
    for name in TEAM_DOCS:
        fields = _parse_frontmatter((_KB_ROOT / "team" / name).read_text(encoding="utf-8"))
        assert fields["scope"] == "team_shared", f"{name} scope must be team_shared"


def test_agent_docs_declare_their_role() -> None:
    for role in AGENT_ROLES:
        path = _KB_ROOT / "agents" / role / _AGENT_DOC_NAME
        fields = _parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fields["scope"] == "private", f"{role} agent doc scope must be private"
        assert fields.get("role") == role, f"{role} agent doc must declare role: {role}"
