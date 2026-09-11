"""Armado del registro de herramientas según la configuración.

Cada herramienta se registra solo si está configurada: sin `TAVILY_API_KEY` no
hay búsqueda web, sin `SANDBOX_URL` no hay ejecución de código, y el agente
arranca igual con las que haya.
"""

from api.config import Settings
from api.logging import get_logger
from tools.base import ToolRegistry
from tools.code_exec import build_code_exec_tool
from tools.web_search import build_web_search_tool

logger = get_logger("tools.registry")


def build_registry(settings: Settings) -> ToolRegistry:
    registry = ToolRegistry()

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

    return registry
