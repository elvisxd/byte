"""El grafo entiende el contenido en bloques, que es como lo manda Gemini.

Ollama manda `content` como str y el pensamiento en `additional_kwargs`. Gemini
manda una lista de bloques —`text`, `thinking`— y el grafo, que solo miraba
cadenas, descartaba el texto y metía `str(lista)` en el historial.
"""

from typing import Any

from langchain_core.messages import AIMessageChunk, HumanMessage

from agent.graph import _extras_de, _pensamiento_de, _texto_de, build_graph
from tests.test_agente import Grabador
from tools.base import ToolRegistry


class _Bloques:
    """Un modelo que contesta como Gemini: bloques, y una firma en `additional_kwargs`."""

    def bind_tools(self, _esquemas: Any) -> "_Bloques":
        return self

    async def astream(self, _mensajes: Any, **_: Any) -> Any:
        yield AIMessageChunk(content=[{"type": "thinking", "thinking": "miro el rango… "}])
        yield AIMessageChunk(content=[{"type": "thinking", "thinking": "sin mecha."}])
        yield AIMessageChunk(
            content=[{"type": "text", "text": "No hay range-sweep."}],
            additional_kwargs={"__gemini_function_call_thought_signatures__": {"c1": "abc"}},
        )


def test_texto_y_pensamiento_de_los_bloques() -> None:
    bloques = [
        {"type": "thinking", "thinking": "pienso"},
        {"type": "text", "text": "hola "},
        "mundo",
    ]
    assert _texto_de(bloques) == "hola mundo"
    assert _pensamiento_de(bloques) == "pienso"
    assert _texto_de("plano") == "plano"
    assert _pensamiento_de("plano") == ""


def test_el_pensamiento_de_ollama_no_vuelve_al_historial() -> None:
    mensaje = AIMessageChunk(
        content="",
        additional_kwargs={"reasoning_content": "x" * 3000, "__gemini_x__": {"c1": "s"}},
    )
    assert _extras_de(mensaje) == {"__gemini_x__": {"c1": "s"}}


async def test_gemini_en_bloques_llega_a_la_traza_y_al_historial() -> None:
    grafo = build_graph(_Bloques(), ToolRegistry([]))
    grabador = Grabador()

    final = await grafo.ainvoke(
        {
            "messages": [HumanMessage(content="¿hay sweep?")],
            "iterations": 0,
            "sources": [],
            "tools_used": [],
        },
        config={"configurable": {"emitter": grabador}},
    )

    respuesta = final["messages"][-1]
    assert respuesta.content == "No hay range-sweep."
    assert respuesta.additional_kwargs == {
        "__gemini_function_call_thought_signatures__": {"c1": "abc"}
    }
    pensado = "".join(
        d["delta"] for t, d in grabador.eventos if t == "THINKING_TEXT_MESSAGE_CONTENT"
    )
    assert pensado == "miro el rango… sin mecha."
    texto = "".join(d["delta"] for t, d in grabador.eventos if t == "TEXT_MESSAGE_CONTENT")
    assert texto == "No hay range-sweep."
