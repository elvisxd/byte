"""Armado del registro de herramientas según la configuración.

Cada herramienta se registra solo si está configurada: sin `TAVILY_API_KEY` no
hay búsqueda web, sin `SANDBOX_URL` no hay ejecución de código, y el agente
arranca igual con las que haya.
"""

from typing import TYPE_CHECKING

from api.config import Settings
from api.logging import get_logger
from tools.base import ToolRegistry
from tools.code_exec import build_code_exec_tool
from tools.web_search import build_web_search_tool

if TYPE_CHECKING:
    from rag.store import DocumentStore

logger = get_logger("tools.registry")


def build_registry(settings: Settings, doc_store: "DocumentStore | None" = None) -> ToolRegistry:
    registry = ToolRegistry()

    # El RAG necesita pgvector: sin Postgres no hay store y el agente arranca
    # sin buscar en documentos, igual que arranca sin web_search sin Tavily.
    if doc_store is not None:
        from rag.embeddings import build_embedder
        from tools.doc_search import build_doc_search_tool

        registry.add(
            build_doc_search_tool(
                doc_store,
                build_embedder(settings),
                settings.max_tool_result_chars,
                settings.max_search_query_chars,
                settings.rag_top_k,
            )
        )
    else:
        logger.info("sin_documentos", detail="el agente arranca sin búsqueda en documentos")

    if settings.tavily_api_key:
        registry.add(
            build_web_search_tool(
                settings.tavily_api_key,
                settings.max_tool_result_chars,
                settings.max_search_query_chars,
            )
        )
    else:
        logger.warning("sin_tavily_api_key", detail="el agente arranca sin búsqueda web")

    if settings.sandbox_url:
        if not settings.sandbox_token:
            # El sandbox rechaza todo sin token: registrar la herramienta solo
            # haría que el modelo falle en cada intento.
            logger.warning(
                "sandbox_sin_token",
                detail="definí SANDBOX_TOKEN: el agente arranca sin ejecución de código",
            )
        else:
            registry.add(
                build_code_exec_tool(
                    settings.sandbox_url,
                    settings.sandbox_token,
                    settings.max_tool_result_chars,
                )
            )
    else:
        logger.warning("sin_sandbox_url", detail="el agente arranca sin ejecución de código")

    # Navegar archivos es opt-in: sin BYTE_PROJECT_ROOT el agente no ve el disco.
    if settings.project_root:
        from pathlib import Path

        from tools.archivos import build_file_tools

        raiz = Path(settings.project_root).expanduser()
        if raiz.is_dir():
            for herramienta in build_file_tools(raiz, settings.max_tool_result_chars):
                registry.add(herramienta)
            logger.info(
                "archivos_activos",
                raiz=str(raiz.resolve()),
                detail="el agente puede listar, leer y buscar dentro de esa carpeta",
            )
        else:
            logger.warning(
                "project_root_no_existe",
                raiz=str(raiz),
                detail="el agente arranca sin acceso a archivos",
            )

    return registry
