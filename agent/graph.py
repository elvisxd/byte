"""Grafo del agente (LangGraph).

Patrón ReAct con `StateGraph` propio en vez del prebuilt, para tener control y
transparencia sobre cada paso (decisión documentada en el plan).

    retrieve_context -> agent -> should_continue -> tools -> agent -> ... -> finalize

Los nodos emiten eventos AG-UI a través del emisor que viaja en
`config["configurable"]["emitter"]`, así el SSE muestra el progreso real del grafo.
"""

import json
import uuid
from typing import Any, Protocol

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from agent.events import AGUI
from agent.prompts import iteration_limit_notice
from agent.state import ByteState
from api.logging import get_logger
from tools.base import ToolRegistry, wrap_untrusted

logger = get_logger("agent.graph")

# Aproximación de tokens por caracteres: sirve para recortar historial sin
# cargar un tokenizer. La compactación real llega en la Fase 2.
CHARS_PER_TOKEN = 4
# El historial no puede comerse todo el contexto: hay que dejar lugar a los
# resultados de herramientas y a la respuesta.
HISTORY_CONTEXT_RATIO = 0.6

# Herramientas que pueden pedir confirmación humana antes de correr.
HERRAMIENTAS_SENSIBLES = frozenset({"code_exec"})


def requiere_aprobacion(pedidas: list[str], ya_usadas: list[str], safe_mode: bool) -> str | None:
    """Devuelve el motivo por el que hace falta aprobación humana, o None.

    La regla del contrato: el modo seguro se activa **automáticamente** si en el
    mismo run hubo búsqueda web y el agente quiere ejecutar código, aunque el
    usuario no lo haya pedido. El razonamiento es de `docs/seguridad-byte.md`:
    contenido de terceros (la web) más ejecución de código es la combinación que
    permite que una inyección indirecta termine corriendo algo.
    """
    if not HERRAMIENTAS_SENSIBLES & set(pedidas):
        return None
    if safe_mode:
        return "modo_seguro_activado"
    # Se cuentan también las de este mismo turno: el modelo puede pedir buscar y
    # ejecutar en la misma tanda.
    if "web_search" in set(ya_usadas) | set(pedidas):
        return "web_y_codigo_en_el_mismo_run"
    return None


class Emitter(Protocol):
    def emit(self, event_type: str, data: dict[str, Any]) -> None: ...


class NullEmitter:
    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        return None


def _emitter(config: RunnableConfig) -> Emitter:
    return (config.get("configurable") or {}).get("emitter") or NullEmitter()


def _approx_token_count(messages: list[AnyMessage]) -> int:
    total = 0
    for message in messages:
        content = (
            message.content
            if isinstance(message.content, str)
            else json.dumps(message.content, ensure_ascii=False)
        )
        total += len(content) // CHARS_PER_TOKEN + 4  # 4 tokens de overhead por mensaje
    return total


def build_graph(
    llm: Any,
    registry: ToolRegistry,
    *,
    max_iterations: int = 6,
    max_tool_result_chars: int = 4000,
    num_ctx: int = 16384,
    checkpointer: Any = None,
) -> Any:
    """Compila el grafo. `llm` es cualquier chat model de LangChain (o un doble en tests)."""

    model = llm.bind_tools(registry.schemas()) if len(registry) else llm
    history_budget = max(int(num_ctx * HISTORY_CONTEXT_RATIO), 512)

    async def retrieve_context(state: ByteState, config: RunnableConfig) -> dict[str, Any]:
        """Recorta el historial para que entre en el contexto.

        En el MVP solo recorta. En la Fase 2 acá entran el RAG y el resumen
        compactado de la conversación.
        """
        emitter = _emitter(config)
        emitter.emit(AGUI.STEP_STARTED, {"stepName": "retrieve_context"})
        messages = state["messages"]
        trimmed = trim_messages(
            messages,
            max_tokens=history_budget,
            token_counter=_approx_token_count,
            strategy="last",
            include_system=True,
            start_on="human",
            allow_partial=False,
        )
        emitter.emit(
            AGUI.STEP_FINISHED,
            {"stepName": "retrieve_context", "kept": len(trimmed), "total": len(messages)},
        )
        if len(trimmed) == len(messages):
            return {}
        # add_messages reemplaza por id: se marcan para borrar los que sobran.
        keep_ids = {id(m) for m in trimmed}
        removed = [m for m in messages if id(m) not in keep_ids]
        logger.info("historial_recortado", descartados=len(removed), quedaron=len(trimmed))
        from langchain_core.messages import RemoveMessage

        return {"messages": [RemoveMessage(id=m.id) for m in removed if m.id]}

    async def agent_node(state: ByteState, config: RunnableConfig) -> dict[str, Any]:
        """Llama al modelo en streaming y emite el texto token a token."""
        emitter = _emitter(config)
        emitter.emit(AGUI.STEP_STARTED, {"stepName": "agent"})
        message_id = f"msg_{uuid.uuid4().hex[:12]}"

        accumulated: Any = None
        text_open = False
        async for chunk in model.astream(state["messages"]):
            accumulated = chunk if accumulated is None else accumulated + chunk
            delta = chunk.content if isinstance(chunk.content, str) else ""
            if not delta:
                continue
            if not text_open:
                emitter.emit(
                    AGUI.TEXT_MESSAGE_START, {"messageId": message_id, "role": "assistant"}
                )
                text_open = True
            emitter.emit(AGUI.TEXT_MESSAGE_CONTENT, {"messageId": message_id, "delta": delta})
        if text_open:
            emitter.emit(AGUI.TEXT_MESSAGE_END, {"messageId": message_id})

        if accumulated is None:
            # El modelo no devolvió nada: se cierra el run con un mensaje honesto.
            reply = AIMessage(content="El modelo no devolvió respuesta.", id=message_id)
        else:
            reply = AIMessage(
                content=accumulated.content,
                tool_calls=list(getattr(accumulated, "tool_calls", []) or []),
                id=message_id,
            )

        for call in reply.tool_calls:
            emitter.emit(
                AGUI.TOOL_CALL_START,
                {
                    "toolCallId": call["id"],
                    "toolCallName": call["name"],
                    "parentMessageId": message_id,
                },
            )
            emitter.emit(
                AGUI.TOOL_CALL_ARGS,
                {"toolCallId": call["id"], "delta": json.dumps(call["args"], ensure_ascii=False)},
            )
            emitter.emit(AGUI.TOOL_CALL_END, {"toolCallId": call["id"]})

        emitter.emit(AGUI.STEP_FINISHED, {"stepName": "agent", "toolCalls": len(reply.tool_calls)})
        return {"messages": [reply], "iterations": state["iterations"] + 1}

    async def tools_node(state: ByteState, config: RunnableConfig) -> dict[str, Any]:
        """Ejecuta las herramientas pedidas.

        Los errores vuelven como ToolMessage (no revientan el grafo): el modelo
        los lee y puede probar otra estrategia.
        """
        emitter = _emitter(config)
        last = state["messages"][-1]
        llamadas = list(getattr(last, "tool_calls", []) or [])

        # La decisión de aprobación va ANTES de ejecutar cualquier herramienta:
        # al reanudar, LangGraph vuelve a correr el nodo entero desde arriba, así
        # que nada que tenga efecto puede quedar antes del interrupt (ni una
        # búsqueda, ni un evento, que saldría duplicado).
        configurable = config.get("configurable") or {}
        motivo = requiere_aprobacion(
            [c["name"] for c in llamadas],
            state["tools_used"],
            bool(configurable.get("safe_mode")),
        )
        if motivo:
            sensible = next(
                (c for c in llamadas if c["name"] in HERRAMIENTAS_SENSIBLES), llamadas[0]
            )
            # El run se detiene acá. El runner emite awaiting_approval con el
            # resume_token y cierra con RUN_FINISHED status "paused".
            aprobado = interrupt(
                {
                    "reason": motivo,
                    "tool": sensible["name"],
                    "code": str(sensible.get("args", {}).get("code", "")),
                }
            )
            if not aprobado:
                emitter.emit(AGUI.STEP_STARTED, {"stepName": "tools"})
                rechazos: list[AnyMessage] = [
                    ToolMessage(
                        content=(
                            "El usuario no aprobó esta ejecución. No la reintentes: "
                            "explicá qué querías hacer y por qué, o resolvelo sin ejecutar código."
                        ),
                        tool_call_id=c["id"],
                        name=c["name"],
                    )
                    for c in llamadas
                ]
                for c in llamadas:
                    emitter.emit(
                        AGUI.TOOL_CALL_RESULT,
                        {"toolCallId": c["id"], "ok": False, "error": "rechazado_por_el_usuario"},
                    )
                emitter.emit(AGUI.STEP_FINISHED, {"stepName": "tools", "ejecutadas": 0})
                return {"messages": rechazos}

        emitter.emit(AGUI.STEP_STARTED, {"stepName": "tools"})
        out: list[AnyMessage] = []
        sources: list[dict[str, Any]] = []
        used: list[str] = []

        for call in llamadas:
            name = call["name"]
            tool = registry.get(name)
            if tool is None:
                out.append(
                    ToolMessage(
                        content=f"ERROR: la herramienta '{name}' no existe.",
                        tool_call_id=call["id"],
                        name=name,
                    )
                )
                emitter.emit(
                    AGUI.TOOL_CALL_RESULT,
                    {"toolCallId": call["id"], "ok": False, "error": "herramienta_desconocida"},
                )
                continue

            # Los argumentos que manda el modelo se validan antes de ejecutar.
            try:
                args = tool.args_model.model_validate(call["args"])
            except ValidationError as exc:
                detail = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
                )
                out.append(
                    ToolMessage(
                        content=wrap_untrusted(
                            f"ERROR DE ARGUMENTOS EN {name}", detail, max_tool_result_chars
                        ),
                        tool_call_id=call["id"],
                        name=name,
                    )
                )
                emitter.emit(
                    AGUI.TOOL_CALL_RESULT,
                    {"toolCallId": call["id"], "ok": False, "error": "argumentos_invalidos"},
                )
                continue

            try:
                result = await tool.run(args)
            except Exception as exc:  # noqa: BLE001 - el error vuelve al modelo como dato
                logger.warning("tool_error", tool=name, error_type=type(exc).__name__)
                out.append(
                    ToolMessage(
                        content=f"ERROR: la herramienta '{name}' falló.",
                        tool_call_id=call["id"],
                        name=name,
                    )
                )
                emitter.emit(
                    AGUI.TOOL_CALL_RESULT,
                    {"toolCallId": call["id"], "ok": False, "error": "ejecucion_fallida"},
                )
                continue

            out.append(ToolMessage(content=result.content, tool_call_id=call["id"], name=name))
            used.append(name)
            sources.extend(result.sources)
            emitter.emit(
                AGUI.TOOL_CALL_RESULT,
                {"toolCallId": call["id"], "ok": result.ok, **result.summary},
            )

        emitter.emit(AGUI.STEP_FINISHED, {"stepName": "tools", "ejecutadas": len(out)})
        # El estado compartido que la UI usa para mostrar fuentes y progreso.
        emitter.emit(
            AGUI.STATE_DELTA,
            {
                "iteration_count": state["iterations"],
                "sources": state["sources"] + sources,
                "awaiting_approval": None,
            },
        )
        return {"messages": out, "sources": sources, "tools_used": used}

    async def finalize(state: ByteState, config: RunnableConfig) -> dict[str, Any]:
        """Último nodo: deja el estado listo y avisa a la UI."""
        emitter = _emitter(config)
        emitter.emit(AGUI.STEP_STARTED, {"stepName": "finalize"})
        patch: dict[str, Any] = {}
        last = state["messages"][-1] if state["messages"] else None
        hit_limit = (
            isinstance(last, AIMessage)
            and bool(getattr(last, "tool_calls", []))
            and state["iterations"] >= max_iterations
        )
        if hit_limit:
            # Se cortó por el tope de iteraciones: se cierra con un mensaje claro
            # en vez de dejar el run sin respuesta.
            notice = iteration_limit_notice(max_iterations)
            message_id = f"msg_{uuid.uuid4().hex[:12]}"
            emitter.emit(AGUI.TEXT_MESSAGE_START, {"messageId": message_id, "role": "assistant"})
            emitter.emit(AGUI.TEXT_MESSAGE_CONTENT, {"messageId": message_id, "delta": notice})
            emitter.emit(AGUI.TEXT_MESSAGE_END, {"messageId": message_id})
            patch["messages"] = [AIMessage(content=notice, id=message_id)]

        emitter.emit(
            AGUI.STATE_SNAPSHOT,
            {
                "iteration_count": state["iterations"],
                "sources": state["sources"],
                "tools_used": state["tools_used"],
                "awaiting_approval": None,
            },
        )
        emitter.emit(AGUI.STEP_FINISHED, {"stepName": "finalize"})
        return patch

    def should_continue(state: ByteState) -> str:
        last = state["messages"][-1] if state["messages"] else None
        wants_tools = bool(getattr(last, "tool_calls", []) or [])
        if wants_tools and state["iterations"] < max_iterations:
            return "tools"
        if wants_tools:
            logger.info("tope_iteraciones", iterations=state["iterations"])
        return "finalize"

    builder = StateGraph(ByteState)
    builder.add_node("retrieve_context", retrieve_context)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "retrieve_context")
    builder.add_edge("retrieve_context", "agent")
    builder.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "finalize": "finalize"}
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
