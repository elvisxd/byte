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

from agent.compact import resumir
from agent.events import AGUI
from agent.prompts import compact_notice, iteration_limit_notice
from agent.state import ByteState
from api.logging import get_logger
from tools.base import ToolRegistry, wrap_untrusted

logger = get_logger("agent.graph")

# Aproximación de tokens por caracteres: sirve para recortar historial sin
# cargar un tokenizer. Lo que el recorte descarta pasa por `_compactar`, que lo
# resume antes de que se pierda.
CHARS_PER_TOKEN = 4
# El historial no puede comerse todo el contexto: hay que dejar lugar a los
# resultados de herramientas y a la respuesta.
HISTORY_CONTEXT_RATIO = 0.6


def _texto_de(contenido: Any) -> str:
    """El texto de un mensaje, venga como cadena o como bloques.

    Ollama manda `content` como str. Gemini lo manda como lista de bloques
    —`{"type": "text", …}`, `{"type": "thinking", …}`— y `str(lista)` metería
    en el historial el repr de un diccionario, que es lo que el modelo leería
    en el turno siguiente. Solo los bloques de texto son la respuesta.
    """
    if isinstance(contenido, str):
        return contenido
    if not isinstance(contenido, list):
        return ""
    partes: list[str] = []
    for bloque in contenido:
        if isinstance(bloque, str):
            partes.append(bloque)
        elif isinstance(bloque, dict) and bloque.get("type") == "text":
            partes.append(str(bloque.get("text", "")))
    return "".join(partes)


def _pensamiento_de(contenido: Any) -> str:
    """Los bloques `thinking` de un mensaje en bloques. Vacío si no hay."""
    if not isinstance(contenido, list):
        return ""
    return "".join(
        str(b.get("thinking", ""))
        for b in contenido
        if isinstance(b, dict) and b.get("type") in ("thinking", "reasoning")
    )


def _extras_de(mensaje: Any) -> dict[str, Any]:
    """Lo que el modelo necesita ver de vuelta en su propio mensaje.

    Gemini 3 firma sus llamadas a herramientas (`thought_signature`) y quiere
    la firma de vuelta en el turno siguiente; viaja en `additional_kwargs`.
    El `reasoning_content` de Ollama NO vuelve: son miles de caracteres por
    iteración que llenarían el contexto en dos vueltas (ver `agent_node`).
    """
    extras = getattr(mensaje, "additional_kwargs", None) or {}
    return {k: v for k, v in extras.items() if k not in ("reasoning_content", "reasoning")}


# Herramientas que pueden pedir confirmación humana antes de correr.
# Las que tienen efecto fuera del chat: ejecutar código, escribir en un archivo
# del proyecto, o cambiar algo visible en GitHub. Con el modo seguro puesto, el
# run se detiene y espera una decisión humana antes de cualquiera de ellas.
#
# Leer no está acá —ni `read_file` ni `listar_repos`—: equivocarse leyendo
# cuesta un turno, equivocarse escribiendo cuesta el archivo. Y `regenerar_cv`
# tampoco, porque solo rehace PDF a partir de HTML que ya se aprobaron.
HERRAMIENTAS_SENSIBLES = frozenset({"code_exec"})

# Las que **modifican algo fuera del chat**: un archivo del proyecto, un repo
# público, el CV que se manda a una empresa. Estas piden aprobación **siempre**,
# no solo con el modo seguro puesto.
#
# La diferencia con las sensibles: ejecutar código en un sandbox aislado se
# deshace solo —el intérprete muere y no queda nada—, pero un archivo escrito
# queda escrito y una descripción de repo la ve cualquiera que te busque. El
# modo seguro está apagado por defecto, así que dejar esto a su criterio
# significaría que Byte edita tu código sin preguntar.
HERRAMIENTAS_QUE_ESCRIBEN = frozenset(
    {
        "write_file",
        "describir_repo",
        "poner_topics",
        "agregar_certificacion",
        "agregar_experiencia",
        "agregar_proyecto",
        "reemplazar_en_cv",
        "git_commit",
        "git_push",
    }
)

# Herramientas que traen contenido de terceros al prompt. `doc_search` cuenta:
# un PDF o un README que alguien subió es tan ajeno como una página web
# (docs/seguridad-byte.md: "páginas web, documentos, resultados de herramientas"
# son todos entrada no confiable).
HERRAMIENTAS_CON_CONTENIDO_EXTERNO = frozenset({"web_search", "doc_search"})


def _que_va_a_hacer(llamada: dict[str, Any]) -> str:
    """Lo que se le muestra a la persona antes de que apruebe.

    Con `code_exec` es el código, que es lo obvio. Con las que escriben hay que
    armarlo: aprobar sin ver qué archivo se toca ni con qué se reemplaza es
    firmar en blanco, y era lo que pasaba —el campo venía vacío porque se leía
    un argumento `code` que estas herramientas no tienen.
    """
    args = llamada.get("args") or {}
    nombre = llamada.get("name", "")
    if "code" in args:
        return str(args["code"])
    if nombre == "write_file":
        viejo, nuevo = str(args.get("old_str", "")), str(args.get("new_str", ""))
        return f"{args.get('path', '?')}\n\n- {viejo[:300]}\n+ {nuevo[:300]}"
    if nombre == "describir_repo":
        return f"{args.get('repo', '?')}: «{args.get('descripcion', '')}»"
    if nombre == "poner_topics":
        return f"{args.get('repo', '?')}: {', '.join(args.get('topics') or [])}"
    if nombre == "git_commit":
        return f"git commit -m «{args.get('mensaje', '')}»\n\n(todos los cambios de la rama)"
    if nombre == "git_push":
        return "git push — esto sube los commits a GitHub y los hace públicos"
    if nombre == "reemplazar_en_cv":
        return (
            f"CV\n\n- {str(args.get('viejo', ''))[:300]}\n+ {str(args.get('nuevo_en', ''))[:300]}"
        )
    # Las de agregar al CV: se muestran sus argumentos, que son cortos y legibles.
    if args:
        return "\n".join(f"{k}: {v}" for k, v in list(args.items())[:6])
    return ""


def requiere_aprobacion(pedidas: list[str], ya_usadas: list[str], safe_mode: bool) -> str | None:
    """Devuelve el motivo por el que hace falta aprobación humana, o None.

    La regla del contrato: el modo seguro se activa **automáticamente** si en el
    mismo run entró contenido externo y el agente quiere ejecutar código, aunque
    el usuario no lo haya pedido. El razonamiento es de `docs/seguridad-byte.md`:
    contenido de terceros más ejecución de código es la combinación que permite
    que una inyección indirecta termine corriendo algo.
    """
    # Escribir se confirma siempre, aunque el modo seguro esté apagado: lo que
    # queda escrito no se deshace solo.
    if HERRAMIENTAS_QUE_ESCRIBEN & set(pedidas):
        return "modifica_algo"
    if not HERRAMIENTAS_SENSIBLES & set(pedidas):
        return None
    if safe_mode:
        return "modo_seguro_activado"
    # Se cuentan también las de este mismo turno: el modelo puede pedir buscar y
    # ejecutar en la misma tanda.
    # El motivo conserva el nombre histórico: lo usan la UI, los tests y los
    # evals, y renombrarlo no agrega nada.
    if HERRAMIENTAS_CON_CONTENIDO_EXTERNO & (set(ya_usadas) | set(pedidas)):
        return "web_y_codigo_en_el_mismo_run"
    return None


class Emitter(Protocol):
    def emit(self, event_type: str, data: dict[str, Any]) -> None: ...


class NullEmitter:
    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        return None


def _emitter(config: RunnableConfig) -> Emitter:
    return (config.get("configurable") or {}).get("emitter") or NullEmitter()


def _herramientas_en_el_hilo(messages: list[AnyMessage]) -> list[str]:
    """Qué herramientas se usaron en esta conversación, no solo en este run.

    El contexto del modelo no se resetea entre turnos: lo que trajo una búsqueda
    en el turno 1 sigue ahí en el turno 5. Si la aprobación mirara solo el run
    actual, bastaría con partir el ataque en dos mensajes —buscar en uno, pedir
    código en el siguiente— para que el modo seguro no se active nunca.

    Se lee de los ToolMessage del historial, que es lo que efectivamente sigue
    en el contexto: si la compactación se los llevó, ya no están influyendo.
    """
    from langchain_core.messages import ToolMessage

    return [m.name for m in messages if isinstance(m, ToolMessage) and m.name]


def _es_uuid(value: str | None) -> bool:
    """Si el id sirve para una columna uuid de Postgres.

    Los mensajes del grafo llevan ids de LangChain ("msg_..."), que no lo son.
    """
    if not value:
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


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

        Lo que no entra no se tira: pasa por `compact`, que lo resume para que
        el agente no pierda el hilo de las conversaciones largas.
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

        # El resumen NO se agrega al historial: se guarda en la conversación y
        # agent_node lo inyecta en cada turno. Como mensaje volvería a entrar en
        # el recorte, y cada compactación resumiría el resumen anterior hasta
        # dejarlo en nada (medido: 973 caracteres útiles degradados a 118).
        await _compactar(removed, config, emitter)
        return {"messages": [RemoveMessage(id=m.id) for m in removed if m.id]}

    async def _compactar(
        removed: list[AnyMessage], config: RunnableConfig, emitter: Emitter
    ) -> str | None:
        """Resume los mensajes que se van y persiste el resumen si se puede."""
        configurable = config.get("configurable") or {}
        repository = configurable.get("repository")
        conversation_id = configurable.get("thread_id")

        previo = None
        if repository is not None and conversation_id:
            conversacion = await repository.get_conversation(conversation_id)
            previo = conversacion.summary if conversacion else None

        emitter.emit(AGUI.STEP_STARTED, {"stepName": "compact"})
        # Resume el modelo del agente, sin bind_tools: acá no tiene que llamar a
        # ninguna herramienta, solo escribir texto.
        resumen = await resumir(llm, removed, previo)
        emitter.emit(AGUI.STEP_FINISHED, {"stepName": "compact", "ok": resumen is not None})
        if resumen is None:
            return None

        if repository is not None and conversation_id:
            # Los mensajes del grafo tienen ids propios de LangChain ("msg_..."),
            # que no son los uuid de MESSAGES: guardar uno ahí rompe la columna.
            # Se usa solo si resulta ser un uuid; el resumen vale igual sin
            # marcador, que solo sirve para el "compactada hasta acá" de la UI.
            ultimo = next((m.id for m in reversed(removed) if _es_uuid(m.id)), None)
            try:
                await repository.set_summary(conversation_id, resumen, ultimo)
            except Exception as exc:  # noqa: BLE001 - el resumen igual se usa en este run
                logger.warning(
                    "summary_no_persistido",
                    conversation_id=conversation_id,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )

        logger.info("historial_compactado", resumidos=len(removed), chars=len(resumen))
        return resumen

    async def _con_resumen(messages: list[AnyMessage], config: RunnableConfig) -> list[AnyMessage]:
        """Antepone el resumen de lo compactado, si la conversación tiene uno.

        Se lee de la conversación en cada turno en vez de vivir en el historial:
        así no lo alcanza el recorte y no se degrada al re-resumirse.
        """
        configurable = config.get("configurable") or {}
        repository = configurable.get("repository")
        conversation_id = configurable.get("thread_id")
        if repository is None or not conversation_id:
            return messages

        try:
            conversacion = await repository.get_conversation(conversation_id)
        except Exception as exc:  # noqa: BLE001 - sin resumen se sigue igual
            logger.warning("resumen_no_leido", error_type=type(exc).__name__)
            return messages

        resumen = getattr(conversacion, "summary", None) if conversacion else None
        if not resumen:
            return messages

        from langchain_core.messages import SystemMessage

        # Después del system prompt y antes del historial: es contexto de fondo,
        # no algo que alguien dijo en la conversación.
        corte = 1 if messages and isinstance(messages[0], SystemMessage) else 0
        aviso = SystemMessage(content=compact_notice(resumen))
        return [*messages[:corte], aviso, *messages[corte:]]

    async def agent_node(state: ByteState, config: RunnableConfig) -> dict[str, Any]:
        """Llama al modelo en streaming y emite el texto token a token."""
        emitter = _emitter(config)
        emitter.emit(AGUI.STEP_STARTED, {"stepName": "agent"})
        message_id = f"msg_{uuid.uuid4().hex[:12]}"

        accumulated: Any = None
        text_open = False
        async for chunk in model.astream(await _con_resumen(state["messages"], config)):
            accumulated = chunk if accumulated is None else accumulated + chunk
            # ⚠ EL PENSAMIENTO NO VIENE EN `content`. Con `reasoning=True`,
            # langchain-ollama lo separa en `additional_kwargs["reasoning_content"]`
            # y deja `content` vacío mientras el modelo piensa. Sin esto, esos
            # chunks caían en el `continue` de abajo y la traza enseñaba las
            # llamadas sin el razonamiento que las precedió —que es justo lo que
            # se quiere auditar: si recorre los ejes o repite una plantilla—.
            # No entra al historial del chat: `reply` se arma con `content` y
            # `tool_calls` solamente, y meter 3.000 caracteres de pensamiento por
            # iteración en el contexto lo llenaría en dos vueltas.
            # Y con Gemini viene como bloques `thinking` dentro de `content`.
            # Groq (gpt-oss, `reasoning_format="parsed"`) lo manda en `reasoning`.
            extras_chunk = getattr(chunk, "additional_kwargs", None) or {}
            pensamiento = (
                extras_chunk.get("reasoning_content")
                or extras_chunk.get("reasoning")
                or _pensamiento_de(chunk.content)
            )
            if pensamiento:
                emitter.emit(
                    AGUI.THINKING_TEXT_MESSAGE_CONTENT,
                    {"messageId": message_id, "delta": str(pensamiento)},
                )
            delta = _texto_de(chunk.content)
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

        llamadas = list(getattr(accumulated, "tool_calls", []) or []) if accumulated else []
        contenido = _texto_de(accumulated.content) if accumulated is not None else ""

        # Vacío **y sin herramientas que pedir**. Chequear solo `accumulated is
        # None` no alcanzaba: hay modelos que sí mandan chunks, pero todos con
        # contenido vacío —encontrado con granite4.1:8b, reproducible para el
        # mismo prompt—. `accumulated` existe, su `content` es "", y el turno
        # terminaba sin mensaje: la ruta `?wait=true` devolvía un 500 "El run no
        # produjo respuesta", que le echa la culpa al servidor por algo que hizo
        # el modelo y deja al usuario sin nada que leer.
        #
        # Un turno vacío que sí trae tool_calls es normal —el modelo pidió una
        # herramienta y hablará después—, así que ese no se toca.
        if not contenido.strip() and not llamadas:
            reply = AIMessage(content="El modelo no devolvió respuesta.", id=message_id)
        else:
            reply = AIMessage(
                content=contenido,
                tool_calls=llamadas,
                id=message_id,
                additional_kwargs=_extras_de(accumulated),
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
            # Del historial del hilo, no de state["tools_used"]: ese se resetea
            # en cada run, pero el contenido externo que entró en un turno sigue
            # en el contexto del modelo en los siguientes. Mirando solo el run
            # actual, partir el ataque en dos turnos evadía la aprobación.
            _herramientas_en_el_hilo(state["messages"]),
            bool(configurable.get("safe_mode")),
        )
        if motivo:
            # Aprobar alcanza a TODAS las llamadas de la tanda, así que hay que
            # mostrarlas todas: si el modelo pide dos ejecuciones y solo se
            # muestra la primera, la persona consiente sobre un código y corre
            # otro, que es lo peor que le puede pasar a una aprobación humana.
            a_confirmar = HERRAMIENTAS_SENSIBLES | HERRAMIENTAS_QUE_ESCRIBEN
            sensibles = [c for c in llamadas if c["name"] in a_confirmar] or llamadas[:1]
            codigos = [_que_va_a_hacer(c) for c in sensibles]
            # El run se detiene acá. El runner emite awaiting_approval con el
            # resume_token y cierra con RUN_FINISHED status "paused".
            aprobado = interrupt(
                {
                    "reason": motivo,
                    "tool": sensibles[0]["name"],
                    # `code` sigue siendo el primero por compatibilidad con la UI
                    # y el contrato; `codes` trae la tanda completa.
                    "code": codigos[0],
                    "codes": codigos,
                    "tool_calls": len(sensibles),
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
