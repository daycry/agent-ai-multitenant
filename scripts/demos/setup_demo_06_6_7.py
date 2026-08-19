r"""Seed datos demo para los tests humanos de Plan 06.6 y 06.7.

Sobre el proyecto "Demo poema del mar (eb8faa10)" (id
326cb49f-76d9-40b0-b826-939a1cdb4b9f, tenant
019e4a6a-d6bc-7b30-aef2-41fc4d48e59f) añade:

  - 1 plan con título + descripción markdown + 5 tareas con status
    variados (incluyendo una en awaiting_human_approval para
    disparar el badge "tareas escaladas" del Plan 06.6).
  - 4 memory_entries con embeddings sintéticos:
      A y B → vectores muy cercanos (cos > 0.85, dispara el badge
              "N similares" del detector del Plan 06.7).
      C → vector aleatorio (no dispara nada).
      D → embedding=NULL (verifica que el detector las excluye).

Uso (desde el repo, con el venv):
    .\.venv\Scripts\python.exe scripts\setup_demo_06_6_7.py

Re-ejecutable: borra el plan demo + memorias demo antes de
insertar de nuevo, identificándolos por un tag en metadata.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from uuid import UUID, uuid4

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Forzar UTF-8 en la consola de Windows.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

DB_URL = (
    "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
    "@localhost:15432/agentic_platform"
)

PROJECT_ID = UUID("326cb49f-76d9-40b0-b826-939a1cdb4b9f")
TENANT_ID = UUID("019e4a6a-d6bc-7b30-aef2-41fc4d48e59f")
DEMO_TAG = "seed_06_6_7"  # marca rows insertadas por este script

EMBED_DIM = 768


def _vec_literal(arr: np.ndarray) -> str:
    """Format a numpy vector as pgvector literal: '[0.123, 0.456, ...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in arr) + "]"


def _gen_embeddings(seed: int = 42) -> tuple[str, str, str]:
    """Return (A, B, C) pgvector literals.

    A and B share the same base direction + tiny Gaussian noise so
    their cosine similarity lands ~0.92. C is independent (cosine
    against A and B near 0 — gaussian unit vectors in 768 dims are
    practically orthogonal).
    """
    rng = np.random.default_rng(seed)
    base = rng.standard_normal(EMBED_DIM)
    base /= np.linalg.norm(base)

    # Noise σ ≈ 0.012 → |noise| ≈ 0.012 * sqrt(768) ≈ 0.33, so
    # cos(A, B) ends near 0.90 (well above the default 0.85 threshold
    # of the dedup detector). Keep small or A and B drift to orthogonal.
    noise_a = rng.standard_normal(EMBED_DIM) * 0.012
    noise_b = rng.standard_normal(EMBED_DIM) * 0.012
    a = base + noise_a
    a /= np.linalg.norm(a)
    b = base + noise_b
    b /= np.linalg.norm(b)

    c = rng.standard_normal(EMBED_DIM)
    c /= np.linalg.norm(c)

    cos_ab = float(np.dot(a, b))
    cos_ac = float(np.dot(a, c))
    print(f"  cos(A,B) = {cos_ab:.3f}  cos(A,C) = {cos_ac:.3f}")
    return _vec_literal(a), _vec_literal(b), _vec_literal(c)


PLAN_DESCRIPTION = """\
## Objetivo del plan

Generar un **poema sobre el mar** con tres revisiones por agentes
distintos: implementador, *revisor lingüístico* y revisor de estilo.

## Tareas previstas

- Borrador inicial (writer)
- Revisión gramática (lingüista)
- Revisión de métrica (revisor poético)
- Aprobación humana final

> Plan generado por el seed `setup_demo_06_6_7.py` para los tests
> humanos de Plan 06.6 (admin UI gaps) y Plan 06.7 (memoria dedup).
"""


TASKS = [
    ("Borrador inicial del poema", "done", "medium"),
    ("Revisión gramática y ortografía", "in_progress", "high"),
    ("Revisión de métrica y rima", "in_review", "medium"),
    (
        "Bloqueada — falta acceso a la web del cliente",
        "blocked",
        "low",
    ),
    (
        "Aprobación humana final del poema (escalada)",
        "awaiting_human_approval",
        "high",
    ),
]


MEMORIES = [
    {
        "label": "A",
        "content": "El equipo decidió escribir el poema con métrica clásica de endecasílabos.",
        "embedding_var": "vec_a",
    },
    {
        "label": "B",
        "content": "Para el poema se acordó usar versos endecasílabos siguiendo la tradición clásica.",
        "embedding_var": "vec_b",
    },
    {
        "label": "C",
        "content": "El presupuesto trimestral del proyecto se aprobó en 12.500 EUR.",
        "embedding_var": "vec_c",
    },
    {
        "label": "D (sin embedding)",
        "content": "Anotación sin embedding generado todavía — no debe aparecer en candidatos.",
        "embedding_var": None,
    },
]


async def _cleanup_previous(conn: object) -> None:
    """Drop plan + memories from a previous seed run."""
    # Tasks linked to a previous demo plan get plan_id reset to NULL.
    res = await conn.execute(
        text(
            "SELECT id FROM plans "
            "WHERE project_id = cast(:pid AS uuid) AND specification->>'demo_tag' = :tag"
        ),
        {"pid": str(PROJECT_ID), "tag": DEMO_TAG},
    )
    prev_plan_ids = [str(row[0]) for row in res]
    if prev_plan_ids:
        await conn.execute(
            text("UPDATE tasks SET plan_id = NULL WHERE cast(plan_id AS text) = ANY(:ids)"),
            {"ids": prev_plan_ids},
        )
        # Drop tasks created by previous seed (matched by title within
        # this project). Don't touch tasks from other demos that may
        # share status values.
        await conn.execute(
            text(
                "DELETE FROM tasks WHERE plan_id IS NULL "
                "AND project_id = cast(:pid AS uuid) AND title = ANY(:titles)"
            ),
            {"pid": str(PROJECT_ID), "titles": [t[0] for t in TASKS]},
        )
        await conn.execute(
            text("DELETE FROM plans WHERE cast(id AS text) = ANY(:ids)"),
            {"ids": prev_plan_ids},
        )
        print(f"  cleanup: dropped {len(prev_plan_ids)} previous demo plan(s) + their tasks")

    await conn.execute(
        text("DELETE FROM memory_entries WHERE metadata->>'demo_tag' = :tag"),
        {"tag": DEMO_TAG},
    )
    print("  cleanup: dropped any previous demo memory entries")


async def _insert_plan(conn: object) -> UUID:
    plan_id = uuid4()
    await conn.execute(
        text("""
            INSERT INTO plans (id, project_id, tenant_id, title, description,
                               status, specification)
            VALUES (cast(:id AS uuid), cast(:pid AS uuid), cast(:tid AS uuid), :title, :desc, 'in_progress',
                    jsonb_build_object('demo_tag', cast(:tag AS text)))
            """),
        {
            "id": str(plan_id),
            "pid": str(PROJECT_ID),
            "tid": str(TENANT_ID),
            "title": "Demo Plan 06.6/06.7 — poema del mar",
            "desc": PLAN_DESCRIPTION,
            "tag": DEMO_TAG,
        },
    )
    print(f"  plan creado: {plan_id} — 'Demo Plan 06.6/06.7'")
    return plan_id


async def _insert_tasks(conn: object, plan_id: UUID) -> None:
    for title, status, priority in TASKS:
        await conn.execute(
            text("""
                INSERT INTO tasks (id, tenant_id, project_id, plan_id,
                                   title, description, status, priority)
                VALUES (cast(:id AS uuid), cast(:tid AS uuid), cast(:pid AS uuid), cast(:plan_id AS uuid),
                        :title, :desc, :status, :priority)
                """),
            {
                "id": str(uuid4()),
                "tid": str(TENANT_ID),
                "pid": str(PROJECT_ID),
                "plan_id": str(plan_id),
                "title": title,
                "desc": f"Tarea seedeada por scripts/setup_demo_06_6_7.py. Status inicial: {status}.",
                "status": status,
                "priority": priority,
            },
        )
        print(f"    task: [{status:>24}] {title}")


async def _insert_memories(conn: object) -> None:
    vec_a, vec_b, vec_c = _gen_embeddings()
    vec_map = {"vec_a": vec_a, "vec_b": vec_b, "vec_c": vec_c}
    for mem in MEMORIES:
        emb_clause = "NULL"
        if mem["embedding_var"]:
            emb_clause = f"'{vec_map[mem['embedding_var']]}'::vector(768)"
        sql = f"""
            INSERT INTO memory_entries (id, tenant_id, scope, type, content,
                                        embedding, project_id, tags, metadata)
            VALUES (cast(:id AS uuid), cast(:tid AS uuid), 'project_shared', 'episodic', :content,
                    {emb_clause}, cast(:pid AS uuid),
                    '["seed", "06.6_06.7"]'::jsonb,
                    jsonb_build_object('demo_tag', cast(:tag AS text), 'label', cast(:label AS text)))
        """
        await conn.execute(
            text(sql),
            {
                "id": str(uuid4()),
                "tid": str(TENANT_ID),
                "content": mem["content"],
                "pid": str(PROJECT_ID),
                "tag": DEMO_TAG,
                "label": mem["label"],
            },
        )
        print(f"    memory [{mem['label']}]: {mem['content'][:60]}…")


async def main() -> int:
    print("=" * 72)
    print("  seed demo 06.6 + 06.7 — proyecto 'Demo poema del mar (eb8faa10)'")
    print("=" * 72)
    print(f"  project: {PROJECT_ID}")
    print(f"  tenant:  {TENANT_ID}")
    print()

    engine = create_async_engine(DB_URL)
    try:
        async with engine.begin() as conn:
            await _cleanup_previous(conn)
            print()
            plan_id = await _insert_plan(conn)
            await _insert_tasks(conn, plan_id)
            print()
            print("  memorias con embeddings sintéticos:")
            await _insert_memories(conn)
    finally:
        await engine.dispose()

    print()
    print("=" * 72)
    print("  OK")
    print("=" * 72)
    print(f"  abre el browser en /admin/projects/{PROJECT_ID}")
    print("  → tab Planes — debe aparecer 'Demo Plan 06.6/06.7'")
    print("  → tab Tasks — debe haber 5 tasks nuevas con plan_id set")
    print("  → /admin/memories — A y B deben mostrar badge 'N similares'")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
