"""Unit tests for the canonical ``/docs`` bootstrap (Plan 07 task_07_01).

These are pure-filesystem tests on a pytest ``tmp_path`` — no DB, no git,
no real bare repo. They pin the contract task_07_02's structural
validator depends on:

  * all seven canonical folders get created under ``<repo>/docs/``;
  * each folder carries the keep file + a stub README;
  * the function is idempotent (re-running creates nothing, raises
    nothing);
  * the return value reports exactly the folders newly created.

Negative / edge cases: a pre-existing partial structure, a clashing
regular file where a directory must go, a non-existent repo path, and a
folder whose README an author already started (must not be clobbered).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from api_server.docs_structure import bootstrap_docs_structure
from api_server.docs_structure.constants import (
    CANONICAL_DOC_FOLDER_NAMES,
    CANONICAL_DOC_FOLDERS,
    KEEP_FILENAME,
    README_FILENAME,
)

pytestmark = pytest.mark.unit


# --- the canonical constant ------------------------------------------------


def test_canonical_list_is_the_seven_claude_md_folders() -> None:
    expected = [
        "01-overview",
        "02-getting-started",
        "03-guides",
        "04-reference",
        "05-architecture-decisions",
        "06-runbooks",
        "07-changelog",
    ]
    # Order matters (numeric prefixes are part of the contract).
    assert [f.name for f in CANONICAL_DOC_FOLDERS] == expected
    assert len(CANONICAL_DOC_FOLDERS) == 7
    # The derived name set must agree with the ordered tuple.
    assert frozenset(expected) == CANONICAL_DOC_FOLDER_NAMES
    # Every folder names a non-empty Diátaxis purpose.
    assert all(f.diataxis_purpose.strip() for f in CANONICAL_DOC_FOLDERS)


# --- happy path ------------------------------------------------------------


def test_creates_all_seven_folders_with_keep_and_readme(tmp_path: Path) -> None:
    bootstrap_docs_structure(tmp_path)

    docs = tmp_path / "docs"
    assert docs.is_dir()
    for folder in CANONICAL_DOC_FOLDERS:
        target = docs / folder.name
        assert target.is_dir(), f"missing folder {folder.name}"
        assert (target / KEEP_FILENAME).is_file(), f"missing keep file in {folder.name}"
        readme = target / README_FILENAME
        assert readme.is_file(), f"missing README in {folder.name}"
        body = readme.read_text(encoding="utf-8")
        # Stub names the folder and its purpose.
        assert folder.name in body
        assert folder.diataxis_purpose in body

    # Exactly seven directories, nothing extra.
    subdirs = sorted(p.name for p in docs.iterdir() if p.is_dir())
    assert subdirs == sorted(f.name for f in CANONICAL_DOC_FOLDERS)


def test_returns_created_paths_in_canonical_order(tmp_path: Path) -> None:
    created = bootstrap_docs_structure(tmp_path)

    expected = [(tmp_path.resolve() / "docs" / f.name).as_posix() for f in CANONICAL_DOC_FOLDERS]
    assert created == expected
    assert len(created) == 7


def test_creates_repo_path_when_missing(tmp_path: Path) -> None:
    repo = tmp_path / "brand" / "new" / "repo"
    assert not repo.exists()

    created = bootstrap_docs_structure(repo)

    assert (repo / "docs" / "01-overview").is_dir()
    assert len(created) == 7


# --- idempotency -----------------------------------------------------------


def test_idempotent_second_run_creates_nothing(tmp_path: Path) -> None:
    first = bootstrap_docs_structure(tmp_path)
    assert len(first) == 7

    second = bootstrap_docs_structure(tmp_path)
    assert second == []


def test_idempotent_does_not_clobber_existing_readme(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    overview = docs / CANONICAL_DOC_FOLDERS[0].name
    overview.mkdir(parents=True)
    custom = "# Real overview\n\nAuthor wrote this.\n"
    (overview / README_FILENAME).write_text(custom, encoding="utf-8")

    created = bootstrap_docs_structure(tmp_path)

    # The pre-existing overview folder is not reported as newly created.
    assert (docs / CANONICAL_DOC_FOLDERS[0].name).as_posix() not in created
    # And its README is left untouched.
    assert (overview / README_FILENAME).read_text(encoding="utf-8") == custom
    # The other six folders were created.
    assert len(created) == 6


# --- partial / edge -------------------------------------------------------


def test_partial_structure_only_missing_folders_created(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    # Pre-create three of the seven.
    present = [f.name for f in CANONICAL_DOC_FOLDERS[:3]]
    for name in present:
        (docs / name).mkdir(parents=True)

    created = bootstrap_docs_structure(tmp_path)

    created_names = {Path(p).name for p in created}
    assert created_names == {f.name for f in CANONICAL_DOC_FOLDERS[3:]}
    assert len(created) == 4
    # All seven exist afterwards.
    assert sorted(p.name for p in docs.iterdir() if p.is_dir()) == sorted(
        f.name for f in CANONICAL_DOC_FOLDERS
    )


def test_keep_file_added_to_preexisting_folder_without_one(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    overview = docs / CANONICAL_DOC_FOLDERS[0].name
    overview.mkdir(parents=True)
    assert not (overview / KEEP_FILENAME).exists()

    bootstrap_docs_structure(tmp_path)

    assert (overview / KEEP_FILENAME).is_file()


def test_keep_files_are_empty(tmp_path: Path) -> None:
    bootstrap_docs_structure(tmp_path)
    docs = tmp_path / "docs"
    for folder in CANONICAL_DOC_FOLDERS:
        keep = docs / folder.name / KEEP_FILENAME
        assert keep.read_text(encoding="utf-8") == ""


# --- negative: clashing regular files -------------------------------------


def test_raises_when_repo_path_is_a_file(tmp_path: Path) -> None:
    clash = tmp_path / "repo_file"
    clash.write_text("not a dir", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        bootstrap_docs_structure(clash)


def test_raises_when_docs_path_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "docs").write_text("not a dir", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        bootstrap_docs_structure(tmp_path)


def test_raises_when_canonical_folder_path_is_a_file(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    # A regular file sitting where the first canonical folder must go.
    (docs / CANONICAL_DOC_FOLDERS[0].name).write_text("not a dir", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        bootstrap_docs_structure(tmp_path)


def test_readme_stub_has_frontmatter_and_h1(tmp_path: Path) -> None:
    bootstrap_docs_structure(tmp_path)
    overview = tmp_path / "docs" / CANONICAL_DOC_FOLDERS[0].name / README_FILENAME
    body = overview.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "lang: es" in body
    assert f"# {CANONICAL_DOC_FOLDERS[0].name}" in body
