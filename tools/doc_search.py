"""Búsqueda en los documentos que subió el usuario (RAG, Fase 2).

Seguridad:
- La query tiene tope de longitud, igual que en web_search.
- El contenido de los chunks viene de archivos de terceros: se envuelve en
  delimitadores de no confiable antes de volver al prompt (ASI01).
- El store filtra por user_id: el modelo no puede pedir documentos de otro.
"""

from pydantic import BaseModel, Field

from api.logging import get_logger
from rag.embeddings import Embedder, EmbeddingError
from rag.store import DocumentStore
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.doc_search")

MAX_SNIPPET_CHARS = 500


class DocSearchArgs(BaseModel):
    query: str = Field(description="Qué buscar en los documentos, en lenguaje natural")
    # Sin default: si el modelo no lo manda, vale el de la configuración
    # (BYTE_RAG_TOP_K). Con un default acá, ese ajuste no haría nada.
    top_k: int | None = Field(default=None, ge=1, le=10, description="Cuántos fragmentos traer")


def build_doc_search_tool(
    store: DocumentStore,
    embedder: Embedder,
    max_result_chars: int,
    max_query_chars: int,
    default_top_k: int = 5,
) -> Tool:
    async def run(args: BaseModel) -> ToolResult:
        assert isinstance(args, DocSearchArgs)  # noqa: S101 - garantizado por el nodo de tools
        query = args.query.strip()[:max_query_chars]
        if not query:
            return ToolResult(
                content=wrap_untrusted("DOCUMENTOS", "Query vacía.", max_result_chars),
                summary={"ok": False, "error": "query_vacia"},
                ok=False,
            )

        try:
            vector = await embedder.embed_one(query)
            hits = await store.search(
                query=query, embedding=vector, user_id=None, top_k=args.top_k or default_top_k
            )
        except EmbeddingError:
            return ToolResult(
                content=wrap_untrusted(
                    "DOCUMENTOS",
                    "No se pudo buscar en los documentos (el modelo de embeddings no responde).",
                    max_result_chars,
                ),
                summary={"ok": False, "error": "embeddings_caidos"},
                ok=False,
            )
        except Exception as exc:  # noqa: BLE001 - el error vuelve al modelo como dato
            logger.warning("doc_search_fallo", error_type=type(exc).__name__)
            return ToolResult(
                content=wrap_untrusted(
                    "DOCUMENTOS", "La búsqueda en documentos falló.", max_result_chars
                ),
                summary={"ok": False, "error": "busqueda_fallida"},
                ok=False,
            )

        if not hits:
            return ToolResult(
                content=wrap_untrusted(
                    "DOCUMENTOS",
                    "No hay nada sobre eso en los documentos cargados.",
                    max_result_chars,
                ),
                summary={"ok": True, "results": 0},
            )

        lines: list[str] = []
        sources: list[dict[str, str]] = []
        for index, hit in enumerate(hits, start=1):
            fragmento = " ".join(hit.content.split())[:MAX_SNIPPET_CHARS]
            lines.append(f"[{index}] {hit.filename}\n{fragmento}")
            # Forma que pide el contrato para MESSAGES.metadata.sources.
            sources.append(
                {
                    "document_id": hit.document_id,
                    "chunk_id": hit.chunk_id,
                    "filename": hit.filename,
                    "snippet": fragmento[:200],
                }
            )

        return ToolResult(
            content=wrap_untrusted("DOCUMENTOS", "\n\n".join(lines), max_result_chars),
            summary={"ok": True, "results": len(hits), "query": query[:100]},
            sources=sources,
        )

    return Tool(
        name="doc_search",
        description=(
            "Busca en los documentos que subió el usuario y devuelve los fragmentos "
            "más relevantes con su archivo de origen. Usala cuando la pregunta sea "
            "sobre material propio, interno o cargado por el usuario."
        ),
        args_model=DocSearchArgs,
        run=run,
        source="builtin",
    )
