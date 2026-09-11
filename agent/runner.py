"""Ciclo de vida de los runs: ejecución, streaming SSE, cancelación y límites.

Un *run* es una ejecución del agente. Se crea con un mensaje (POST) y se observa
por SSE (GET), como pide el contrato. Acá viven:
- el registro de runs en memoria y su estado,
- el fan-out de eventos AG-UI a los clientes suscritos, con replay por
  `Last-Event-ID`,
- el tope de runs concurrentes por credencial y el timeout por run,
- la persistencia de los mensajes que produjo el run.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.events import AGUI, Event, keepalive
from agent.prompts import SYSTEM_PROMPT
from api.errors import ByteError
from api.logging import get_logger
from db.repository import Repository
from models.schemas import RunStatusValue

logger = get_logger("agent.runner")

# Cada cuánto se manda un comentario SSE para que no corte el proxy.
KEEPALIVE_S = 15.0
# Tope de eventos guardados por run para el replay de reconexión.
# El texto se emite token a token: una sola respuesta de 1.024 tokens son ~1.030
# eventos, y un run puede dar hasta `max_iterations` vueltas. El tope es alto
# porque hay pocos runs vivos a la vez (`max_concurrent_runs`), y al terminar el
# run el buffer se compacta (ver `compactar`).
MAX_EVENTS_PER_RUN = 10_000
# Cuántos runs terminados se recuerdan (para GET /runs/{id} y reconexiones).
MAX_RUNS_RETAINED = 200

# Un run en estos estados ya no espera nada de nadie: se puede olvidar.
# "paused" NO está: ahí hay una persona que todavía puede aprobar.
ESTADOS_TERMINALES = frozenset({"finished", "cancelled", "error"})

# Cuántos runs terminados conservan sus eventos de texto completos. Son a los
# que alguien se puede estar reconectando; los más viejos se compactan para que
# 200 runs recordados no se lleven cientos de MB en tokens.
RUNS_CON_TEXTO_COMPLETO = 5


def _now() -> datetime:
    return datetime.now(UTC)


def _elegibles_para_compactar(mensajes: list[Any], dejar: int) -> list[Any]:
    """Qué mensajes se pueden sacar del hilo al compactar.

    Dos invariantes que un `mensajes[:-N]` a secas rompe:

    - **El system prompt se queda.** Vive al principio del hilo y no se
      reinyecta: `_seed_history` solo corre cuando el hilo está vacío, así que
      sacarlo una vez lo pierde para siempre, con las reglas de seguridad
      adentro. `trim_messages(include_system=True)` ya lo protege en el recorte
      automático; acá hay que hacerlo a mano.
    - **Un ToolMessage no se separa de su AIMessage.** Si el corte cae entre
      medio, el hilo arranca con un `tool` sin el `assistant` que lo pidió y
      Ollama rechaza la conversación entera. Es el mismo pareo que cuida
      `_seed_history` al sembrar.
    """
    from langchain_core.messages import SystemMessage, ToolMessage

    if len(mensajes) <= dejar:
        return []

    corte = len(mensajes) - dejar
    # Un ToolMessage justo después del corte quedaría huérfano: se retrocede
    # hasta dejar su AIMessage del lado que se conserva.
    while corte > 0 and isinstance(mensajes[corte], ToolMessage):
        corte -= 1

    return [m for m in mensajes[:corte] if not isinstance(m, SystemMessage)]


@dataclass
class Run:
    """Estado de un run y su bus de eventos."""

    id: str
    conversation_id: str
    credential_id: str
    safe_mode: bool = False
    status: RunStatusValue = "running"
    iterations: int = 0
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    message_id: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    # Lo que el run dejó esperando confirmación humana (modo seguro).
    awaiting: dict[str, Any] | None = None
    events: list[Event] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    # True cuando ya se tiraron los deltas de texto: quien se suscriba después
    # no puede reconstruir la respuesta desde el stream y hay que avisarle.
    compactado: bool = False
    _seq: int = 0
    _subscribers: set[asyncio.Queue[Event]] = field(default_factory=set)
    _dropped_events: int = 0

    # --- Emisor de eventos (lo usan los nodos del grafo) ---
    def emit(self, event_type: str, data: dict[str, Any]) -> Event:
        self._seq += 1
        event = Event(seq=self._seq, type=event_type, data={"runId": self.id, **data})
        self.events.append(event)
        if len(self.events) > MAX_EVENTS_PER_RUN:
            # Se descartan los más viejos: quien se reconecte pidiendo desde
            # antes de esto se entera por el aviso de replay incompleto.
            self.events.pop(0)
            self._dropped_events += 1
            self.compactado = True
        for queue in self._subscribers:
            queue.put_nowait(event)
        if event_type == AGUI.STATE_DELTA and "iteration_count" in data:
            self.iterations = data["iteration_count"]
        return event

    @property
    def finished(self) -> bool:
        return self.status in ("finished", "cancelled", "error", "paused")

    def events_after(self, last_seq: int) -> list[Event]:
        return [event for event in self.events if event.seq > last_seq]

    @property
    def primer_evento_disponible(self) -> int:
        """Seq del evento más viejo que queda. Si el cliente pide algo anterior,
        se le perdió contenido y hay que avisarle."""
        return self.events[0].seq if self.events else 0

    def compactar(self) -> None:
        """Al terminar el run se tiran los deltas de texto y queda la estructura.

        El texto completo ya está en MESSAGES, así que un cliente que se
        reconecta tarde lo pide por la API. Esto acota la memoria: se recuerdan
        hasta MAX_RUNS_RETAINED runs y no pueden quedarse con miles de eventos
        de tokens cada uno.
        """
        self.events = [e for e in self.events if e.type != AGUI.TEXT_MESSAGE_CONTENT]
        self.compactado = True

    def add_subscriber(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def remove_subscriber(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)


class RunManager:
    """Crea, ejecuta, observa y cancela runs."""

    def __init__(
        self,
        graph: Any,
        repository: Repository,
        tokens: Any,
        *,
        max_concurrent_runs: int = 2,
        run_timeout_s: int = 180,
        max_iterations: int = 6,
        resume_ttl_s: int = 3600,
    ) -> None:
        self._graph = graph
        self._repo = repository
        # TokenService: firma los resume_token del modo seguro.
        self._tokens = tokens
        self._max_concurrent = max_concurrent_runs
        self._timeout_s = run_timeout_s
        self._max_iterations = max_iterations
        # Cuánto vive un resume_token: hasta entonces el run pausado no se purga.
        self._resume_ttl_s = resume_ttl_s
        self._runs: dict[str, Run] = {}

    # --- Consulta ---
    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def require(self, run_id: str, credential_id: str) -> Run:
        run = self._runs.get(run_id)
        # Un run de otra credencial se responde como inexistente (evita enumerarlos).
        if run is None or run.credential_id != credential_id:
            raise ByteError("not_found", "El run no existe", status_code=404)
        return run

    def active_for(self, credential_id: str) -> int:
        return sum(
            1
            for run in self._runs.values()
            if run.credential_id == credential_id and not run.finished
        )

    def active_in(self, conversation_id: str, credential_id: str) -> bool:
        return any(
            run.conversation_id == conversation_id
            and run.credential_id == credential_id
            and not run.finished
            for run in self._runs.values()
        )

    def busy(self, conversation_id: str) -> bool:
        """Si la conversación tiene algún run vivo, sea de quien sea.

        `active_in` filtra por credencial porque responde "¿este cliente ya
        tiene un run acá?". Para evitar dos escrituras simultáneas del mismo
        `summary` hay que mirar la conversación entera.
        """
        return any(
            run.conversation_id == conversation_id and not run.finished
            for run in self._runs.values()
        )

    # --- Creación ---
    async def start(
        self,
        conversation_id: str,
        credential_id: str,
        user_content: str,
        safe_mode: bool = False,
    ) -> Run:
        await self.preparar(conversation_id, credential_id)

        run = Run(
            id=f"run_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            credential_id=credential_id,
            safe_mode=safe_mode,
        )
        self._runs[run.id] = run
        self._prune()
        run.task = asyncio.create_task(self._execute(run, user_content=user_content))
        return run

    async def preparar(self, conversation_id: str, credential_id: str) -> None:
        """Todo lo que puede rechazar un pedido, antes de que se guarde nada.

        La ruta lo llama antes de persistir el mensaje del usuario: si esto
        falla después, queda un mensaje sin ningún run que lo responda.
        """
        # Un run por conversación: dos a la vez comparten el hilo del
        # checkpointer (thread_id = conversation_id), se pisan el estado y cada
        # uno responde sin ver la pregunta del otro.
        if self.active_in(conversation_id, credential_id):
            raise ByteError(
                "conversation_busy",
                "Ya hay un run en curso en esta conversación",
                status_code=409,
            )
        if self.active_for(credential_id) >= self._max_concurrent:
            raise ByteError(
                "too_many_runs",
                f"Ya hay {self._max_concurrent} runs en curso con esta credencial",
                status_code=429,
                headers={"Retry-After": "5"},
            )
        await self._resolver_aprobacion_pendiente(conversation_id, credential_id)

    def pausado_en(self, conversation_id: str, credential_id: str) -> Run | None:
        """El run de esta conversación que está esperando una decisión humana."""
        return next(
            (
                run
                for run in self._runs.values()
                if run.conversation_id == conversation_id
                and run.credential_id == credential_id
                and run.status == "paused"
            ),
            None,
        )

    async def _resolver_aprobacion_pendiente(
        self, conversation_id: str, credential_id: str
    ) -> None:
        """Un mensaje nuevo no puede pisar una aprobación pendiente.

        Sin esto, el pedido de ejecutar código quedaba abandonado: el hilo se
        quedaba con un tool_call sin su ToolMessage (historial inválido para el
        modelo) y la conversación mostraba dos mensajes del usuario seguidos sin
        explicación.
        """
        pendiente = self.pausado_en(conversation_id, credential_id)
        if pendiente is not None:
            # La persona todavía puede decidir: que decida.
            raise ByteError(
                "approval_pending",
                f"El run {pendiente.id} espera tu aprobación. Resolvelo con "
                f"POST /runs/{pendiente.id}/resume antes de seguir.",
                status_code=409,
            )

        # Sin run en memoria pero con el hilo interrumpido: el proceso se
        # reinició (o pasó el plazo del token). Ya nadie puede aprobar eso, así
        # que se cierra como rechazo para dejar el historial consistente.
        config = {"configurable": {"thread_id": conversation_id}}
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception as exc:  # noqa: BLE001 - sin checkpointer no hay nada que cerrar
            logger.debug("sin_estado_previo", error_type=type(exc).__name__)
            return
        if not (snapshot and getattr(snapshot, "interrupts", None)):
            return

        logger.info("aprobacion_huerfana_cerrada", conversation_id=conversation_id)
        cierre = Run(
            id=f"run_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            credential_id=credential_id,
        )
        self._runs[cierre.id] = cierre
        cierre.task = asyncio.create_task(self._execute(cierre, aprobacion=False))
        await cierre.done.wait()

    def _compactar_viejos(self) -> None:
        """Deja los eventos de texto solo en los runs terminados más recientes.

        El texto definitivo vive en MESSAGES, así que un cliente que se
        reconecta a un run viejo lo pide por la API — y el stream se lo avisa.
        """
        terminados = sorted(
            (r for r in self._runs.values() if r.status in ESTADOS_TERMINALES),
            key=lambda r: r.finished_at or r.started_at,
            reverse=True,
        )
        for run in terminados[RUNS_CON_TEXTO_COMPLETO:]:
            if not run.compactado:
                run.compactar()

    def _purgable(self, run: Run) -> bool:
        """Un run pausado espera a una persona: no se descarta mientras su
        `resume_token` pueda seguir siendo válido. Si no, la aprobación
        pendiente desaparecería sin que nadie se entere."""
        if run.status in ESTADOS_TERMINALES:
            return True
        if run.status == "paused" and run.finished_at:
            return (_now() - run.finished_at).total_seconds() > self._resume_ttl_s
        return False

    def _prune(self) -> None:
        """Se olvidan los runs más viejos que ya no esperan nada."""
        if len(self._runs) <= MAX_RUNS_RETAINED:
            return
        olvidables = sorted(
            (r for r in self._runs.values() if self._purgable(r)),
            key=lambda r: r.finished_at or r.started_at,
        )
        for run in olvidables[: len(self._runs) - MAX_RUNS_RETAINED]:
            del self._runs[run.id]

    # --- Ejecución ---
    async def _execute(
        self,
        run: Run,
        *,
        user_content: str | None = None,
        aprobacion: bool | None = None,
    ) -> None:
        """Corre el grafo, ya sea desde el principio o retomando una aprobación."""
        log = logger.bind(run_id=run.id, conversation_id=run.conversation_id)
        if aprobacion is None:
            run.emit(
                AGUI.RUN_STARTED,
                {"threadId": run.conversation_id, "safeMode": run.safe_mode},
            )
            log.info("run_iniciado", chars=len(user_content or ""))
        else:
            # No se emite otro RUN_STARTED: es el mismo run. Que ya no espera
            # nada es parte del estado compartido.
            run.awaiting = None
            run.emit(AGUI.STATE_DELTA, {"awaiting_approval": None, "approved": aprobacion})
            log.info("run_reanudado", aprobado=aprobacion)
        try:
            async with asyncio.timeout(self._timeout_s):
                final_state = await self._run_graph(
                    run, user_content=user_content, aprobacion=aprobacion
                )

            if self._pausar_si_espera_aprobacion(run, final_state, log):
                return

            await self._persist(run, final_state)
            run.status = "finished"
            run.emit(
                AGUI.RUN_FINISHED,
                {
                    "status": "finished",
                    "threadId": run.conversation_id,
                    "message_id": run.message_id,
                    "langfuse_trace_id": None,  # Langfuse llega en la Fase 4
                    "sources": run.sources,
                },
            )
            log.info("run_terminado", iterations=run.iterations, sources=len(run.sources))
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.emit(AGUI.RUN_FINISHED, {"status": "cancelled", "threadId": run.conversation_id})
            log.info("run_cancelado")
            # No se re-lanza: la cancelación es un final esperado del run.
        except TimeoutError:
            run.status = "error"
            run.emit(
                AGUI.RUN_ERROR,
                {"code": "run_timeout", "message": f"El run pasó {self._timeout_s}s y se cortó"},
            )
            log.warning("run_timeout", timeout_s=self._timeout_s)
        except httpx.HTTPError as exc:
            # El modelo no está: es el caso más común en desarrollo y merece un
            # código propio (el contrato lo llama model_unavailable).
            run.status = "error"
            run.emit(
                AGUI.RUN_ERROR,
                {"code": "model_unavailable", "message": "El modelo no responde"},
            )
            log.warning("modelo_no_disponible", error_type=type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - al cliente va un mensaje genérico
            run.status = "error"
            run.emit(AGUI.RUN_ERROR, {"code": "internal_error", "message": "Error interno del run"})
            log.exception("run_error", error_type=type(exc).__name__)
        finally:
            run.finished_at = _now()
            self._compactar_viejos()
            run.done.set()

    async def _run_graph(
        self,
        run: Run,
        *,
        user_content: str | None = None,
        aprobacion: bool | None = None,
    ) -> dict[str, Any]:
        """Arma el estado de entrada y ejecuta el grafo.

        El hilo del checkpointer (`thread_id = conversation_id`) es el historial
        del agente. Si el hilo está vacío — primer mensaje, o reinicio con el
        checkpointer en memoria — se siembra desde MESSAGES, que es la fuente de
        verdad. Si ya tiene estado, alcanza con mandar el mensaje nuevo.
        """
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": run.conversation_id,
                "emitter": run,
                "safe_mode": run.safe_mode,
                # Para que compact pueda leer el resumen previo y guardar el nuevo.
                "repository": self._repo,
            },
            "recursion_limit": self._max_iterations * 2 + 10,
        }
        if aprobacion is not None:
            # El grafo retoma exactamente donde quedó el interrupt.
            from langgraph.types import Command

            return await self._graph.ainvoke(Command(resume=aprobacion), config=config)

        if await self._thread_has_state(config):
            messages: list[Any] = [HumanMessage(content=user_content or "")]
        else:
            messages = [SystemMessage(content=SYSTEM_PROMPT), *await self._seed_history(run)]

        inputs = {
            "messages": messages,
            "iterations": 0,
            # `None` resetea los acumuladores: son por run, el hilo no.
            "sources": None,
            "tools_used": None,
        }
        return await self._graph.ainvoke(inputs, config=config)

    async def compactar(self, conversation_id: str, llm: Any, dejar: int = 4) -> tuple[str, int]:
        """Compacta el hilo del checkpointer: resume lo viejo y lo saca.

        Es la versión manual de lo que `retrieve_context` hace solo al pasar el
        ~60% del contexto, y opera sobre la misma fuente: el estado del grafo,
        que es lo que realmente se le manda al modelo. Resumir MESSAGES en vez
        de esto dejaría el hilo intacto y el contexto no se liberaría.

        Devuelve (resumen, cuántos mensajes se compactaron). Si no hay nada que
        compactar, el contador vuelve en 0.
        """
        from agent.compact import resumir

        config = {"configurable": {"thread_id": conversation_id}}
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception as exc:  # noqa: BLE001 - sin checkpointer no hay hilo
            logger.debug("sin_estado_previo", error_type=type(exc).__name__)
            return ("", 0)

        mensajes = list((snapshot.values or {}).get("messages") or []) if snapshot else []
        # Se dejan los últimos: compactar todo borraría el turno en curso y la
        # conversación perdería el hilo inmediato.
        viejos = _elegibles_para_compactar(mensajes, dejar)
        if not viejos:
            return ("", 0)

        conversacion = await self._repo.get_conversation(conversation_id)
        previo = conversacion.summary if conversacion else None
        resumen = await resumir(llm, viejos, previo)
        if resumen is None:
            # Sin resumen no se saca nada del hilo: perder los mensajes sin
            # nada que los reemplace sería peor que no compactar.
            raise ByteError(
                "compaction_failed",
                "No se pudo generar el resumen (¿el modelo responde?)",
                status_code=503,
            )

        from langchain_core.messages import RemoveMessage

        # Sacarlos del hilo es lo que libera contexto: sin esto el run siguiente
        # mandaría todo el historial **más** el resumen.
        await self._graph.aupdate_state(
            config, {"messages": [RemoveMessage(id=m.id) for m in viejos if m.id]}
        )
        # El marcador que ya había se respeta: pasarle None lo borraría, y la UI
        # perdería el "compactada hasta acá" que dejó la compactación automática.
        marcador = conversacion.summary_up_to_message_id if conversacion else None
        await self._repo.set_summary(conversation_id, resumen, marcador)
        logger.info("compactacion_manual", conversation_id=conversation_id, mensajes=len(viejos))
        return (resumen, len(viejos))

    async def _thread_has_state(self, config: dict[str, Any]) -> bool:
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception as exc:  # noqa: BLE001 - sin checkpointer no hay estado previo
            logger.debug("sin_estado_previo", error_type=type(exc).__name__)
            return False
        return bool(snapshot and snapshot.values and snapshot.values.get("messages"))

    async def _seed_history(self, run: Run) -> list[Any]:
        """Historial desde MESSAGES (incluye el mensaje que disparó el run).

        Los mensajes de herramienta no se reinyectan: sin su `tool_call_id`
        original romperían el pareo que espera el modelo.
        """
        stored = await self._repo.history(run.conversation_id)
        seeded: list[Any] = []
        for message in stored:
            if message.role == "user":
                seeded.append(HumanMessage(content=message.content))
            elif message.role == "assistant":
                seeded.append(AIMessage(content=message.content))
        return seeded

    async def _persist(self, run: Run, final_state: dict[str, Any]) -> None:
        """Guarda en MESSAGES lo que produjo el run (respuestas y resultados)."""
        run.sources = list(final_state.get("sources") or [])
        run.iterations = int(final_state.get("iterations") or 0)
        tools_used = list(final_state.get("tools_used") or [])

        # Solo interesan los mensajes de este run: los del hilo previo ya están
        # guardados. Se toman los que aparecieron después del último HumanMessage.
        last_human = max(
            (
                index
                for index, message in enumerate(final_state.get("messages", []))
                if isinstance(message, HumanMessage)
            ),
            default=-1,
        )
        produced = [
            message
            for index, message in enumerate(final_state.get("messages", []))
            if index > last_human and isinstance(message, AIMessage | ToolMessage)
        ]

        for message in produced:
            if isinstance(message, ToolMessage):
                await self._repo.add_message(
                    run.conversation_id,
                    "tool",
                    str(message.content),
                    metadata={"tool": message.name, "tool_call_id": message.tool_call_id},
                )
                continue
            if not str(message.content).strip():
                # Un turno que solo pidió herramientas no es un mensaje para la UI.
                continue
            saved = await self._repo.add_message(
                run.conversation_id,
                "assistant",
                str(message.content),
                metadata={
                    "sources": run.sources,
                    "tools_used": tools_used,
                    "iterations": run.iterations,
                },
            )
            run.message_id = saved.id

    def _pausar_si_espera_aprobacion(self, run: Run, final_state: dict[str, Any], log: Any) -> bool:
        """Si el grafo se detuvo en un interrupt, deja el run en "paused".

        AG-UI no tiene evento de aprobación: se modela como estado, igual que
        dice el contrato. El cliente muestra el código, el usuario decide, y
        vuelve por POST /runs/{id}/resume.
        """
        interrupciones = final_state.get("__interrupt__") or []
        if not interrupciones:
            return False

        pendiente = dict(getattr(interrupciones[0], "value", {}) or {})
        run.awaiting = pendiente
        run.status = "paused"
        run.iterations = int(final_state.get("iterations") or run.iterations)
        resume_token = self._tokens.issue_resume_token(run.id, run.credential_id)
        run.emit(
            AGUI.STATE_DELTA,
            {"awaiting_approval": {**pendiente, "resume_token": resume_token}},
        )
        run.emit(
            AGUI.RUN_FINISHED,
            {"status": "paused", "threadId": run.conversation_id},
        )
        log.info("run_en_pausa", motivo=pendiente.get("reason"), tool=pendiente.get("tool"))
        return True

    async def resume(self, run: Run, aprobacion: bool) -> None:
        """Retoma un run pausado con la decisión del usuario."""
        if run.status != "paused":
            raise ByteError("conflict", "El run no está esperando aprobación", status_code=409)
        run.status = "running"
        run.finished_at = None
        run.done = asyncio.Event()
        run.task = asyncio.create_task(self._execute(run, aprobacion=aprobacion))

    # --- Observación ---
    async def stream(self, run: Run, last_event_id: int = 0) -> AsyncIterator[str]:
        """Genera el SSE: replay de lo perdido y después lo que va saliendo."""
        queue = run.add_subscriber()
        sent_seq = last_event_id
        try:
            # Si el cliente pide desde antes de lo que queda guardado, se perdió
            # texto: hay que avisarle en vez de seguir como si nada, porque la
            # respuesta le quedaría cortada sin que se entere.
            primero = run.primer_evento_disponible
            hay_hueco = run.compactado or (last_event_id and primero > last_event_id + 1)
            if hay_hueco:
                yield Event(
                    seq=last_event_id,
                    type=AGUI.STATE_DELTA,
                    data={
                        "runId": run.id,
                        "replay_incompleto": True,
                        "desde": primero,
                        "detalle": "faltan eventos: pedí el mensaje final por la API",
                    },
                ).to_sse()
            for event in run.events_after(last_event_id):
                sent_seq = event.seq
                yield event.to_sse()
            while True:
                if run.finished and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    yield keepalive()
                    continue
                if event.seq <= sent_seq:
                    continue  # ya fue en el replay
                sent_seq = event.seq
                yield event.to_sse()
        finally:
            run.remove_subscriber(queue)

    async def cancel(self, run: Run) -> None:
        """Corta el run: cancelar la tarea cierra el stream contra Ollama."""
        if run.finished or run.task is None:
            return
        run.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run.task

    async def cancel_conversation(self, conversation_id: str, credential_id: str) -> int:
        """Corta los runs en vuelo de una conversación (se usa al borrarla).

        Sin esto, borrar una conversación dejaría al agente hablando con un hilo
        que ya no existe: sigue gastando modelo y falla al guardar la respuesta.
        """
        en_vuelo = [
            run
            for run in self._runs.values()
            if run.conversation_id == conversation_id
            and run.credential_id == credential_id
            and not run.finished
        ]
        for run in en_vuelo:
            await self.cancel(run)
        return len(en_vuelo)

    async def wait(self, run: Run) -> None:
        await run.done.wait()

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            await self.cancel(run)
