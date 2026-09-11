"""Búsqueda web con Tavily — la única herramienta del MVP.

Seguridad:
- La query que manda el modelo tiene tope de longitud (200 chars por defecto).
- No existe "abrir URL arbitraria" en el MVP: el modelo no elige a qué host se llama.
- El resultado se envuelve en delimitadores y se acota antes de volver al prompt.
"""

from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.web_search")

MAX_SNIPPET_CHARS = 500


class WebSearchArgs(BaseModel):
    """Argumentos válidos de la herramienta. Lo que no encaje acá, no se ejecuta."""

    query: str = Field(description="Qué buscar en la web, en lenguaje natural")
    max_results: int = Field(default=5, ge=1, le=10, description="Cuántos resultados traer")


def build_web_search_tool(
    api_key: str,
    max_result_chars: int,
    max_query_chars: int,
    client: object | None = None,
) -> Tool:
    """Arma la herramienta. `client` se inyecta en los tests."""

    async def run(args: BaseModel) -> ToolResult:
        assert isinstance(args, WebSearchArgs)  # noqa: S101 - garantizado por el nodo de tools
        query = args.query.strip()[:max_query_chars]
        if not query:
            return ToolResult(
                content=wrap_untrusted("BUSQUEDA WEB", "Query vacía.", max_result_chars),
                summary={"ok": False, "error": "query_vacia"},
                ok=False,
            )

        search_client = client
        if search_client is None:
            from tavily import AsyncTavilyClient

            search_client = AsyncTavilyClient(api_key=api_key)

        try:
            raw = await search_client.search(
                query=query, max_results=args.max_results, search_depth="basic"
            )
        except Exception as exc:  # noqa: BLE001 - el error vuelve al modelo como dato
            logger.warning("web_search_fallo", error_type=type(exc).__name__)
            return ToolResult(
                content=wrap_untrusted(
                    "BUSQUEDA WEB",
                    "La búsqueda web falló. Probá responder con lo que ya sabés o reformular.",
                    max_result_chars,
                ),
                summary={"ok": False, "error": "busqueda_fallida"},
                ok=False,
            )

        results = (raw or {}).get("results", [])[: args.max_results]
        if not results:
            return ToolResult(
                content=wrap_untrusted("BUSQUEDA WEB", "Sin resultados.", max_result_chars),
                summary={"ok": True, "results": 0},
            )

        lines: list[str] = []
        sources: list[dict[str, str]] = []
        for index, item in enumerate(results, start=1):
            title = str(item.get("title", ""))[:200]
            url = str(item.get("url", ""))[:500]
            snippet = " ".join(str(item.get("content", "")).split())[:MAX_SNIPPET_CHARS]
            lines.append(f"[{index}] {title}\nURL: {url}\n{snippet}")
            sources.append({"filename": title or url, "url": url, "snippet": snippet[:200]})

        return ToolResult(
            content=wrap_untrusted("BUSQUEDA WEB", "\n\n".join(lines), max_result_chars),
            summary={"ok": True, "results": len(results), "query": query[:100]},
            sources=sources,
        )

    return Tool(
        name="web_search",
        description=(
            "Busca en la web y devuelve títulos, URLs y extractos. Usala para "
            "información actual o que puede haber cambiado."
        ),
        args_model=WebSearchArgs,
        run=run,
        source="builtin",
    )
