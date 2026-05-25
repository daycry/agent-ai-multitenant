"""RAG (Retrieval-Augmented Generation) — chunk search + reranking
(Plan 04 Fase D).

Three pieces:

  - :mod:`api_server.rag.search` — BM25 over chunks (`ts_rank_cd` on
    the GIN FTS index), vector over chunks (pgvector HNSW) and the
    hybrid `recall_chunks` that fuses both with Reciprocal Rank
    Fusion.
  - :mod:`api_server.rag.reranker` — `Reranker` Protocol +
    `NoopReranker` / `DeterministicReranker` (tests) and
    `BGEReranker` (real; lazy-imports `FlagEmbedding`).
  - :mod:`api_server.rag.tool` — `rag_search` high-level function the
    agent runtime exposes as a tool.

KB visibility is enforced via the `kb_projects` junction: a project
only sees chunks of KBs explicitly granted to it. Cross-tenant
isolation rides on RLS as usual.
"""

from api_server.rag.reranker import (
    BGEReranker,
    DeterministicReranker,
    NoopReranker,
    Reranker,
    RerankerError,
    RerankHit,
)
from api_server.rag.search import (
    ChunkHit,
    bm25_chunks,
    recall_chunks,
    vector_chunks,
)
from api_server.rag.tool import RAGSearchHit, rag_search

__all__ = [
    "BGEReranker",
    "ChunkHit",
    "DeterministicReranker",
    "NoopReranker",
    "RAGSearchHit",
    "RerankHit",
    "Reranker",
    "RerankerError",
    "bm25_chunks",
    "rag_search",
    "recall_chunks",
    "vector_chunks",
]
