"""Documentos del RAG y búsqueda (Fase 2).

El upload responde 202: indexar implica parsear y vectorizar, que en CPU tarda
demasiado para hacerlo dentro del request. El estado se consulta con
GET /documents/{id} (processing → indexed | error).
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Request, UploadFile, status

from api.deps import Context, OwnerId, limiter, runs_limit
from api.errors import ByteError
from api.logging import get_logger
from models.schemas import (
    DocumentInfo,
    DocumentList,
    SearchHit,
    SearchRequest,
    SearchResults,
)
from rag.embeddings import EmbeddingError

router = APIRouter(tags=["documentos"])
logger = get_logger("api.documents")

MAX_SNIPPET_CHARS = 300

# Las tareas de indexado se guardan acá para que el recolector de basura no se
# las lleve a mitad de camino (asyncio solo guarda referencias débiles).
_tareas: set[asyncio.Task[None]] = set()


def _limpiar_nombre(filename: str | None) -> str:
    """Se queda con el nombre, sin ruta ni caracteres de control.

    Byte nunca escribe el archivo en disco, así que un "../../etc/passwd" no
    sería un traversal. Pero el nombre se muestra en la UI y viaja al prompt
    del modelo como parte de las citas: mejor que sea un nombre y nada más.
    """
    nombre = (filename or "").replace("\\", "/").split("/")[-1]
    nombre = "".join(c for c in nombre if c.isprintable()).strip(". ")
    return nombre[:255] or "documento"


def _rag(ctx: Context) -> Any:
    """El servicio de RAG, o 503 si Byte arrancó sin Postgres."""
    rag = getattr(ctx, "rag", None)
    if rag is None:
        raise ByteError(
            "rag_not_configured",
            "Los documentos necesitan Postgres con pgvector (DATABASE_URL)",
            status_code=503,
        )
    return rag


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentInfo)
@limiter.limit(runs_limit)
async def subir_documento(
    request: Request,
    ctx: Context,
    owner: OwnerId,
    file: UploadFile = File(...),  # noqa: B008 - así se declara un upload en FastAPI
) -> DocumentInfo:
    rag = _rag(ctx)
    data = await file.read()

    if not data:
        raise ByteError("empty_file", "El archivo está vacío", status_code=422)
    # El middleware ya corta por content-length, pero eso es lo que el cliente
    # *dice* que manda: acá se mide lo que realmente llegó.
    if len(data) > ctx.settings.max_document_bytes:
        raise ByteError(
            "payload_too_large",
            f"El archivo supera los {ctx.settings.max_document_bytes // (1024 * 1024)} MB",
            status_code=413,
        )

    filename = _limpiar_nombre(file.filename)
    mime_type = file.content_type or "application/octet-stream"
    document_id = await rag.store.create(
        filename=filename,
        size_bytes=len(data),
        mime_type=mime_type,
        user_id=owner,
    )

    # La respuesta se arma con lo que ya se sabe, sin releer la base: entre el
    # create y un get habría un await, y la tarea de indexado podría adelantarse
    # y devolver "indexed" en el 202 que promete "processing".
    respuesta = DocumentInfo(
        id=document_id,
        filename=filename,
        status="processing",
        size_bytes=len(data),
        mime_type=mime_type,
        chunks=0,
        uploaded_at=datetime.now(UTC),
    )

    tarea = asyncio.create_task(rag.ingestor.index(document_id, data, filename))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return respuesta


@router.get("/documents", response_model=DocumentList)
async def listar_documentos(ctx: Context, owner: OwnerId) -> DocumentList:
    rag = _rag(ctx)
    documentos = await rag.store.list_documents(owner)
    return DocumentList(items=[DocumentInfo.model_validate(doc) for doc in documentos])


@router.get("/documents/{document_id}", response_model=DocumentInfo)
async def ver_documento(document_id: str, ctx: Context, owner: OwnerId) -> DocumentInfo:
    rag = _rag(ctx)
    documento = await rag.store.get(document_id, owner)
    if documento is None:
        raise ByteError("not_found", "No existe ese documento", status_code=404)
    return DocumentInfo.model_validate(documento)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_documento(document_id: str, ctx: Context, owner: OwnerId) -> None:
    rag = _rag(ctx)
    # Los chunks se van con el documento por el ON DELETE CASCADE: el borrado es
    # real, no lógico (docs/seguridad-byte.md).
    if not await rag.store.delete(document_id, owner):
        raise ByteError("not_found", "No existe ese documento", status_code=404)


@router.post("/search", response_model=SearchResults)
@limiter.limit(runs_limit)
async def buscar(
    request: Request, payload: SearchRequest, ctx: Context, owner: OwnerId
) -> SearchResults:
    """La misma búsqueda híbrida que usa el agente, expuesta para `byte search`."""
    rag = _rag(ctx)
    # Solo EmbeddingError es "Ollama no responde". Capturar todo acá haría que un
    # bug del código se reporte como servicio caído, y el operador reiniciaría
    # Ollama en loop buscando un problema que está en otro lado.
    try:
        vector = await rag.embedder.embed_one(payload.query)
    except EmbeddingError as exc:
        logger.warning("search_embedding_fallo", error_type=type(exc).__name__)
        raise ByteError(
            "embeddings_unavailable",
            "El modelo de embeddings no responde",
            status_code=503,
        ) from exc

    hits = await rag.store.search(
        query=payload.query, embedding=vector, user_id=owner, top_k=payload.top_k
    )
    return SearchResults(
        results=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                filename=hit.filename,
                snippet=" ".join(hit.content.split())[:MAX_SNIPPET_CHARS],
                score=hit.score,
            )
            for hit in hits
        ]
    )
