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

    # El CV: leerlo y mantenerlo. Opt-in por la misma razón que los archivos —
    # que el agente pueda reescribir tu CV tiene que ser una decisión.
    if settings.cv_dir:
        from pathlib import Path

        from tools.cv import build_cv_tools

        carpeta = Path(settings.cv_dir).expanduser()
        if carpeta.is_dir():
            portfolio = (
                Path(settings.portfolio_dir).expanduser() if settings.portfolio_dir else None
            )
            drive = Path(settings.drive_cv_dir).expanduser() if settings.drive_cv_dir else None
            for herramienta in build_cv_tools(
                carpeta, settings.max_tool_result_chars, portfolio, drive
            ):
                registry.add(herramienta)
            logger.info(
                "cv_activo",
                carpeta=str(carpeta),
                detail="el agente puede leer y mantener el CV",
            )
        else:
            logger.warning(
                "cv_dir_no_existe", carpeta=str(carpeta), detail="el agente arranca sin el CV"
            )

    # GitHub por `gh`, si está instalado y con sesión. No hace falta configurar
    # nada: la sesión ya vive en el llavero del sistema.
    if settings.github_tools:
        from tools.github import GhNoDisponible, build_github_tools

        try:
            for herramienta in build_github_tools():
                registry.add(herramienta)
            logger.info("github_activo", detail="el agente puede leer y describir tus repos")
        except GhNoDisponible as exc:
            logger.warning("github_sin_sesion", detail=str(exc))

    # Git sobre el proyecto. Necesita la misma raíz que las de archivos: sin
    # saber sobre qué repo se commitea, no hay nada que hacer.
    if settings.git_tools and settings.project_root:
        from pathlib import Path

        from tools.git import build_git_tools

        raiz = Path(settings.project_root).expanduser()
        if (raiz / ".git").exists():
            for herramienta in build_git_tools(raiz):
                registry.add(herramienta)
            logger.info("git_activo", raiz=str(raiz), detail="el agente puede commitear y subir")
        else:
            logger.warning("git_sin_repo", raiz=str(raiz), detail="no es un repositorio")

    # Leer páginas y explorar APIs. Van con la búsqueda web porque es su
    # continuación natural: buscar dice que algo existe, leer dice qué dice.
    if settings.web_fetch:
        from tools.web_fetch import build_web_fetch_tools

        for herramienta in build_web_fetch_tools(settings.max_tool_result_chars):
            registry.add(herramienta)
        logger.info("web_fetch_activo", detail="el agente puede abrir páginas y llamar APIs")

    # Las skills: el índice va al prompt, el contenido se lee con la herramienta.
    if settings.skills_dir:
        from agent.skills import cargar
        from tools.skills import build_skill_tool

        herramienta = build_skill_tool(cargar(settings.skills_dir))
        if herramienta is not None:
            registry.add(herramienta)

    return registry
