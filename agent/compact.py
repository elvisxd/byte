"""Compactación del historial: resumir lo viejo en vez de tirarlo.

El MVP recortaba y listo: los mensajes que no entraban en el contexto
desaparecían, y el agente perdía el hilo de conversaciones largas. Acá se
resumen antes de descartarlos.

El resumen **no** viaja en el historial: se guarda en `CONVERSATIONS.summary` y
`agent_node` lo inyecta en cada turno. Como mensaje volvería a entrar en el
recorte, y cada compactación resumiría el resumen anterior hasta dejarlo en nada
(medido: 973 caracteres útiles degradados a 118).

Los mensajes originales no se borran de MESSAGES: la UI los sigue mostrando,
solo dejan de mandarse al modelo (docs/plan-asistente-ia-local.md).
"""

from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage

from agent.prompts import COMPACT_PROMPT
from api.logging import get_logger

logger = get_logger("agent.compact")

# Tope del resumen. Un resumen que crece sin límite termina comiéndose el
# contexto que venía a liberar.
MAX_SUMMARY_CHARS = 2000


def _texto(message: AnyMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    # Los mensajes multimodales traen listas de bloques: se queda con el texto.
    return " ".join(
        str(bloque.get("text", "")) if isinstance(bloque, dict) else str(bloque)
        for bloque in (content if isinstance(content, list) else [content])
    )


def _rol(message: AnyMessage) -> str:
    if isinstance(message, HumanMessage):
        return "Usuario"
    if isinstance(message, AIMessage):
        return "Asistente"
    return "Herramienta"


def transcribir(messages: list[AnyMessage]) -> str:
    """Los mensajes como texto plano, para pasárselos al modelo que resume."""
    lineas = []
    for message in messages:
        if isinstance(message, SystemMessage):
            # El system prompt no es parte de la conversación.
            continue
        texto = _texto(message).strip()
        if texto:
            lineas.append(f"{_rol(message)}: {texto}")
    return "\n\n".join(lineas)


async def resumir(llm: Any, messages: list[AnyMessage], previo: str | None = None) -> str | None:
    """Resume los mensajes dados. Devuelve None si no se pudo.

    Falla en silencio a propósito: si el modelo no responde, el llamador se
    queda con el recorte de siempre. Perder el resumen degrada la memoria del
    agente, pero tumbar el run sería peor.
    """
    transcripcion = transcribir(messages)
    if not transcripcion.strip():
        return previo

    nuevo = await _resumir_texto(llm, f"Mensajes a resumir:\n{transcripcion}")
    if nuevo is None:
        # Sin resumen nuevo se conserva el que había: es peor perderlo.
        return previo
    if not previo:
        return _acotar(nuevo)

    # Se concatena en vez de pedirle al modelo que integre los dos. Pidiéndoselo,
    # un 8B se queda con lo reciente y descarta lo viejo: medido, un resumen útil
    # de 973 caracteres terminó en 118 que solo describían los últimos mensajes.
    combinado = f"{previo}\n\n{nuevo}"
    if len(combinado) <= MAX_SUMMARY_CHARS:
        return combinado

    # Recién cuando no entra se recomprime todo junto, que es cuando perder
    # detalle es inevitable.
    recomprimido = await _resumir_texto(
        llm, f"Resumen largo de una conversación, condensalo:\n{combinado}"
    )
    return _acotar(recomprimido or combinado)


def _acotar(resumen: str) -> str:
    if len(resumen) <= MAX_SUMMARY_CHARS:
        return resumen
    return resumen[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"


async def _resumir_texto(llm: Any, pedido: str) -> str | None:
    try:
        respuesta = await llm.ainvoke(
            [SystemMessage(content=COMPACT_PROMPT), HumanMessage(content=pedido)]
        )
    except Exception as exc:  # noqa: BLE001 - sin resumen se sigue con el recorte
        logger.warning("compactacion_fallo", error_type=type(exc).__name__)
        return None
    return _texto(respuesta).strip() or None
