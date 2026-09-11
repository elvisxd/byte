"""Estado del grafo del agente."""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def accumulate(left: list[Any], right: list[Any] | None) -> list[Any]:
    """Acumula listas entre nodos; `None` las resetea.

    Un run puede pasar varias veces por el nodo de herramientas, así que las
    fuentes se suman. Pero `sources` y `tools_used` son por run, y el hilo del
    checkpointer sobrevive entre runs: mandar `None` al arrancar los limpia.
    """
    if right is None:
        return []
    return left + right


class ByteState(TypedDict):
    """Estado compartido entre los nodos."""

    messages: Annotated[list[AnyMessage], add_messages]
    iterations: int
    sources: Annotated[list[dict[str, Any]], accumulate]
    tools_used: Annotated[list[str], accumulate]
