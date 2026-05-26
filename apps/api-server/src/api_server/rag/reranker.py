"""Cross-encoder reranker (Plan 04 task_04_19).

After the hybrid recall pulls ~20 candidates the reranker picks the
top N by computing a per-(query, chunk) relevance score with a
cross-encoder model. Default: `BAAI/bge-reranker-v2-m3` — small,
multilingual, runs on CPU via PyTorch / FlagEmbedding.

Architecture mirrors the rest of Plan 04: a `Reranker` Protocol +
fakes for tests (`NoopReranker`, `DeterministicReranker`) + a real
implementation that lazy-imports the heavy lib so a deployment that
doesn't need it doesn't pay the import cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class RerankerError(RuntimeError):
    """Raised by the real reranker when the backend fails to load or
    score. The caller should fall back to the un-reranked order."""


@dataclass(frozen=True)
class RerankHit:
    """One reranker output, sorted by descending `score`."""

    chunk_id: Any  # opaque to this module — caller passes whatever id type
    score: float


class Reranker(Protocol):
    @property
    def model_id(self) -> str: ...

    async def rerank(
        self,
        *,
        query: str,
        items: Sequence[tuple[Any, str]],
    ) -> list[RerankHit]: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------
class NoopReranker:
    """Identity reranker — preserves the input order with a synthetic
    decaying score. Used by tests + as the default when the operator
    hasn't configured a real model."""

    @property
    def model_id(self) -> str:
        return "noop"

    async def rerank(
        self,
        *,
        query: str,  # noqa: ARG002
        items: Sequence[tuple[Any, str]],
    ) -> list[RerankHit]:
        # Score = 1 / (rank+1). Strictly decreasing so the caller can
        # sort and the relative order matches the input.
        return [
            RerankHit(chunk_id=cid, score=1.0 / (i + 1)) for i, (cid, _content) in enumerate(items)
        ]

    async def aclose(self) -> None:  # pragma: no cover
        pass


class DeterministicReranker:
    """Scores items by lexical overlap (number of query tokens
    appearing in the item text, normalised). Stable + reproducible —
    a real reranker would do better but this is enough to assert the
    pipeline reorders things sensibly.

    NOT a real reranker; only used by tests."""

    @property
    def model_id(self) -> str:
        return "deterministic-overlap"

    async def rerank(
        self,
        *,
        query: str,
        items: Sequence[tuple[Any, str]],
    ) -> list[RerankHit]:
        q_tokens = {t.lower() for t in query.split() if t}
        scored: list[RerankHit] = []
        for cid, content in items:
            c_tokens = {t.lower() for t in content.split() if t}
            overlap = len(q_tokens & c_tokens)
            denom = max(len(q_tokens), 1)
            scored.append(RerankHit(chunk_id=cid, score=overlap / denom))
        # Stable secondary sort by original order so ties resolve
        # deterministically.
        return sorted(scored, key=lambda h: -h.score)

    async def aclose(self) -> None:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Real implementation — bge-reranker-v2-m3
# ---------------------------------------------------------------------------
class BGEReranker:
    """Cross-encoder reranker via FlagEmbedding's `FlagReranker`.

    Heavy: pulls in torch + transformers. The import is **lazy** so a
    deployment that doesn't need this never pays the cost. Plan 04
    Fase D's automated tests use the deterministic fakes; this
    implementation is exercised by hand against the dev stack.
    """

    def __init__(
        self,
        *,
        model_id: str = "BAAI/bge-reranker-v2-m3",
        use_fp16: bool = True,
    ) -> None:
        self._model_id = model_id
        self._use_fp16 = use_fp16
        self._model: Any = None  # lazy

    @property
    def model_id(self) -> str:
        return self._model_id

    async def rerank(
        self,
        *,
        query: str,
        items: Sequence[tuple[Any, str]],
    ) -> list[RerankHit]:
        if not items:
            return []
        try:
            model = self._get_model()
            # FlagEmbedding's API is sync; the caller should be fine
            # with a small blocking section here (rerank batches are
            # ~20 items and the model runs in milliseconds on CPU).
            pairs = [[query, content] for _cid, content in items]
            scores = model.compute_score(pairs, normalize=True)
        except Exception as exc:
            raise RerankerError(f"bge-reranker failed: {exc}") from exc
        # FlagReranker may return a single float when len(pairs)==1.
        if isinstance(scores, int | float):
            scores = [float(scores)]
        out: list[RerankHit] = [
            RerankHit(chunk_id=cid, score=float(s))
            for (cid, _content), s in zip(items, scores, strict=True)
        ]
        return sorted(out, key=lambda h: -h.score)

    async def aclose(self) -> None:  # pragma: no cover — model lives for the process
        self._model = None

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from FlagEmbedding import FlagReranker
        except ImportError as exc:
            raise RerankerError(
                "FlagEmbedding is not installed."
                " `pip install 'FlagEmbedding>=1.2'` enables BGEReranker."
            ) from exc
        self._model = FlagReranker(self._model_id, use_fp16=self._use_fp16)
        return self._model


__all__ = [
    "BGEReranker",
    "DeterministicReranker",
    "NoopReranker",
    "RerankHit",
    "Reranker",
    "RerankerError",
]
