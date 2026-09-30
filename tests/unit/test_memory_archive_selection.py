"""task_mem_11 — qué archiva el job de caducidad y qué no (selección pura).

La regla del plan: se archivan (soft) las `episodic` sin `kind = summary`, con
`recall_count = 0` y de más de N días. Además, por CLAUDE.md, el job sólo toca la
memoria que escriben los AGENTES (`team_shared`/`project_shared`/`global`): una
`private` es de una persona (o del córtex, que tiene su propio olvido) y no la
archiva un barrido de plataforma.
"""

from __future__ import annotations

from api_server.memorizer.lifecycle import ArchiveCandidate, should_archive

N = 90


def _c(**kw: object) -> ArchiveCandidate:
    base: dict[str, object] = {
        "type": "episodic",
        "kind": None,
        "scope": "project_shared",
        "recall_count": 0,
        "age_days": 120.0,
        "archived": False,
        "deleted": False,
        "cortex": False,
    }
    base.update(kw)
    return ArchiveCandidate(**base)  # type: ignore[arg-type]


def test_an_old_never_recalled_episodic_memory_is_archived() -> None:
    assert should_archive(_c(), older_than_days=N)
    assert should_archive(_c(kind="fact"), older_than_days=N)
    assert should_archive(_c(kind=None, scope="global"), older_than_days=N)
    assert should_archive(_c(scope="team_shared"), older_than_days=N)


def test_a_summary_is_never_archived() -> None:
    assert not should_archive(_c(kind="summary"), older_than_days=N)


def test_a_memory_that_was_ever_recalled_stays() -> None:
    assert not should_archive(_c(recall_count=1), older_than_days=N)


def test_semantic_memories_do_not_expire() -> None:
    assert not should_archive(_c(type="semantic"), older_than_days=N)


def test_the_age_threshold_is_strict_and_configurable() -> None:
    assert not should_archive(_c(age_days=N), older_than_days=N)  # exactamente N: aún no
    assert should_archive(_c(age_days=N + 0.01), older_than_days=N)
    assert not should_archive(_c(age_days=10), older_than_days=N)
    assert should_archive(_c(age_days=10), older_than_days=7)


def test_private_and_cortex_memories_are_out_of_scope_for_the_platform_sweep() -> None:
    assert not should_archive(_c(scope="private"), older_than_days=N)
    assert not should_archive(_c(cortex=True), older_than_days=N)


def test_already_archived_or_deleted_rows_are_not_touched_twice() -> None:
    assert not should_archive(_c(archived=True), older_than_days=N)
    assert not should_archive(_c(deleted=True), older_than_days=N)
