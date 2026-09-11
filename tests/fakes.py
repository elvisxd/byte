"""Dobles de prueba: un LLM que se puede guionar y un cliente Tavily falso.

Permiten testear el grafo y el streaming SSE sin Ollama ni Tavily reales.
"""

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessageChunk


def text_turn(text: str, *, chunk_size: int = 4) -> list[AIMessageChunk]:
    """Un turno del modelo que solo escribe texto, partido en varios chunks."""
    return [
        AIMessageChunk(content=text[i : i + chunk_size]) for i in range(0, len(text), chunk_size)
    ]


def tool_turn(name: str, args_json: str, call_id: str = "call_1") -> list[AIMessageChunk]:
    """Un turno del modelo que pide una herramienta."""
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"name": name, "args": args_json, "id": call_id, "index": 0},
            ],
        )
    ]


class FakeLLM:
    """Modelo guionado: cada llamada consume el turno siguiente."""

    def __init__(self, turns: list[list[AIMessageChunk]]) -> None:
        self._turns = list(turns)
        self.calls = 0
        self.bound_tools: list[dict[str, Any]] = []

    def bind_tools(self, tools: list[dict[str, Any]]) -> "FakeLLM":
        self.bound_tools = tools
        return self

    async def astream(self, _messages: list[Any], **_kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for chunk in turn:
            yield chunk


class FakeTavily:
    """Cliente de búsqueda falso. `results` se devuelve tal cual."""

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.results = (
            results
            if results is not None
            else [
                {
                    "title": "FastAPI",
                    "url": "https://fastapi.tiangolo.com",
                    "content": "FastAPI es un framework web para Python.",
                }
            ]
        )
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int = 5, **_kwargs: Any) -> dict[str, Any]:
        self.queries.append(query)
        return {"results": self.results[:max_results]}


class SlowLLM:
    """Modelo que tarda: sirve para probar cancelación, timeout y concurrencia."""

    def __init__(self, delay_s: float = 5.0, text: str = "respuesta lenta") -> None:
        self.delay_s = delay_s
        self.text = text
        self.started = 0

    def bind_tools(self, _tools: list[dict[str, Any]]) -> "SlowLLM":
        return self

    async def astream(self, _messages: list[Any], **_kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        import asyncio

        self.started += 1
        yield AIMessageChunk(content="")
        await asyncio.sleep(self.delay_s)
        yield AIMessageChunk(content=self.text)


class BrokenLLM:
    """Modelo que explota: sirve para probar RUN_ERROR."""

    def bind_tools(self, _tools: list[dict[str, Any]]) -> "BrokenLLM":
        return self

    async def astream(self, _messages: list[Any], **_kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        raise RuntimeError("ollama caido")
        yield AIMessageChunk(content="")  # pragma: no cover


class OllamaCaidoLLM:
    """Modelo que no se puede alcanzar: prueba el código model_unavailable."""

    def bind_tools(self, _tools: list[dict[str, Any]]) -> "OllamaCaidoLLM":
        return self

    async def astream(self, _messages: list[Any], **_kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        import httpx

        raise httpx.ConnectError("conexión rechazada")
        yield AIMessageChunk(content="")  # pragma: no cover


class FakeSandbox:
    """Doble del servicio sandbox: devuelve `resultado` sin ejecutar nada."""

    def __init__(self, resultado: dict[str, Any] | None = None) -> None:
        self.resultado = resultado or {
            "stdout": "42\n",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 7,
            "truncated": False,
        }
        self.pedidos: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
        self.pedidos.append({"url": url, "json": json, "headers": headers})

        class _Respuesta:
            def __init__(self, datos: dict[str, Any]) -> None:
                self._datos = datos

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return self._datos

        return _Respuesta(self.resultado)
