"""La herramienta de búsqueda web por separado."""

from typing import Any

from tests.fakes import FakeTavily
from tools.web_search import WebSearchArgs, build_web_search_tool


class TavilyRoto:
    async def search(self, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("429 de Tavily")


async def test_la_query_se_acota() -> None:
    tavily = FakeTavily()
    tool = build_web_search_tool("falsa", 4000, 200, client=tavily)
    await tool.run(WebSearchArgs(query="a" * 500))
    # El modelo no puede mandar una query de longitud arbitraria.
    assert len(tavily.queries[0]) == 200


async def test_falla_de_la_api_vuelve_como_dato() -> None:
    tool = build_web_search_tool("falsa", 4000, 200, client=TavilyRoto())
    resultado = await tool.run(WebSearchArgs(query="q"))
    assert resultado.ok is False
    assert resultado.summary["error"] == "busqueda_fallida"
    # El modelo recibe una explicación, no una excepción.
    assert "falló" in resultado.content
    assert resultado.sources == []


async def test_sin_resultados() -> None:
    tool = build_web_search_tool("falsa", 4000, 200, client=FakeTavily([]))
    resultado = await tool.run(WebSearchArgs(query="q"))
    assert resultado.ok is True
    assert resultado.summary["results"] == 0
    assert "Sin resultados" in resultado.content


async def test_fuentes_citables() -> None:
    tavily = FakeTavily(
        [
            {"title": "Uno", "url": "https://uno.example", "content": "texto uno"},
            {"title": "Dos", "url": "https://dos.example", "content": "texto dos"},
        ]
    )
    tool = build_web_search_tool("falsa", 4000, 200, client=tavily)
    resultado = await tool.run(WebSearchArgs(query="q", max_results=2))
    assert [f["url"] for f in resultado.sources] == [
        "https://uno.example",
        "https://dos.example",
    ]
    assert resultado.summary == {"ok": True, "results": 2, "query": "q"}
