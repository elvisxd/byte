"""Las piezas del RAG agrupadas, para pasarlas juntas al contexto de la app."""

from dataclasses import dataclass
from typing import Any

from api.config import Settings
from rag.embeddings import Embedder, build_embedder
from rag.ingest import Ingestor
from rag.store import DocumentStore


@dataclass(frozen=True, slots=True)
class RagService:
    store: DocumentStore
    embedder: Embedder
    ingestor: Ingestor


def build_rag_service(settings: Settings, pool: Any) -> RagService:
    store = DocumentStore(pool)
    embedder = build_embedder(settings)
    return RagService(
        store=store,
        embedder=embedder,
        ingestor=Ingestor(
            store=store,
            embedder=embedder,
            parse_timeout_s=settings.document_parse_timeout_s,
            batch_size=settings.embed_batch_size,
        ),
    )
