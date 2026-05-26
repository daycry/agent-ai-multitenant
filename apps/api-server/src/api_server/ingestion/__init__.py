"""Document ingestion pipeline (Plan 04 Fase C).

Glues the four external pieces together for every uploaded
`Document`:

  1. **Antivirus scan** (`AntivirusScanner`) — ClamAV INSTREAM
     check before we hand the bytes to Docling. A positive hit
     flips the doc to ``failed`` and never reaches the parser.
  2. **Parse + chunk** (`DoclingClient`) — docling-serve does the
     heavy lifting: PDF / Office / HTML / Markdown / audio (Whisper)
     all flow through the same convert endpoint.
  3. **Embed** (`Embedder`) — Ollama by default
     (`nomic-embed-text-v1.5`, 768 dims). Runs in batch for
     throughput; failed embeddings leave NULL so the row is still
     searchable via BM25.
  4. **Persist** — `chunks` rows + lifecycle status update on
     `documents`.

The orchestration lives in :func:`ingest_document`; each external
dependency is injected so tests use fakes and production wires the
real services. The Celery task in `apps/workers/src/workers/ingestion.py`
is a thin adapter.
"""

from api_server.ingestion.antivirus import (
    AntivirusScanner,
    AntivirusVerdict,
    NullAntivirus,
)
from api_server.ingestion.docling import (
    DoclingChunk,
    DoclingClient,
    DoclingParseError,
)
from api_server.ingestion.docling_mcp import (
    ConvertResult,
    DoclingMCPClient,
    document_convert,
)
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.ingestion.pipeline import IngestionResult, ingest_document
from api_server.ingestion.promote import (
    PromotionError,
    PromotionResult,
    promote_to_kb,
)

__all__ = [
    "AntivirusScanner",
    "AntivirusVerdict",
    "ConvertResult",
    "DoclingChunk",
    "DoclingClient",
    "DoclingMCPClient",
    "DoclingParseError",
    "Embedder",
    "EmbeddingError",
    "IngestionResult",
    "NullAntivirus",
    "PromotionError",
    "PromotionResult",
    "document_convert",
    "ingest_document",
    "promote_to_kb",
]
