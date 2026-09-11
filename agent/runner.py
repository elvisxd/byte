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
MAX_EVENTS_PER_RUN = 2000
# Cuántos runs terminados se recuerdan (para GET /runs/{id} y reconexiones).
MAX_RUNS_RETAINED = 200


def _now() -> datetime:
    return datetime.now(UTC)


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
    events: list[Event] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    _seq: int = 0
    _subscribers: set[asyncio.Queue[Event]] = field(default_factory=set)
    _dropped_events: int = 0

    # --- Emisor de eventos (lo usan los nodos del grafo) ---
    def emit(self, event_type: str, data: dict[str, Any]) -> Event:
        self._seq += 1
        event = Event(seq=self._seq, type=event_type, data={"runId": self.id, **data})
        self.events.append(event)
        if len(self.events) > MAX_EVENTS_PER_RUN:
            # Se descartan los más viejos: un replay muy tardío puede quedar incompleto.
            self.events.pop(0)
            self._dropped_events += 1
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
        *,
        max_concurrent_runs: int = 2,
        run_timeout_s: int = 180,
        max_iterations: int = 6,
    ) -> None:
        self._graph = graph
        self._repo = repository
        self._max_concurrent = max_concurrent_runs
        self._timeout_s = run_timeout_s
        self._max_iterations = max_iterations
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

    # --- Creación ---
    async def start(
        self,
        conversation_id: str,
        credential_id: str,
        user_content: str,
        safe_mode: bool = False,
    ) -> Run:
        if self.active_for(credential_id) >= self._max_concurrent:
            raise ByteError(
                "too_many_runs",
                f"Ya hay {self._max_concurrent} runs en curso con esta credencial",
                status_code=429,
                headers={"Retry-After": "5"},
            )
        run = Run(
            id=f"run_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            credential_id=credential_id,
            safe_mode=safe_mode,
        )
        self._runs[run.id] = run
        self._prune()
        run.task = asyncio.create_task(self._execute(run, user_content))
        return run

    def _prune(self) -> None:
        """Se olvidan los runs terminados más viejos."""
        if len(self._runs) <= MAX_RUNS_RETAINED:
            return
        finished = sorted(
            (r for r in self._runs.values() if r.finished),
            key=lambda r: r.finished_at or r.started_at,
        )
        for run in finished[: len(self._runs) - MAX_RUNS_RETAINED]:
            del self._runs[run.id]

    # --- Ejecución ---
    async def _execute(self, run: Run, user_content: str) -> None:
        log = logger.bind(run_id=run.id, conversation_id=run.conversation_id)
        run.emit(
            AGUI.RUN_STARTED,
            {"threadId": run.conversation_id, "safeMode": run.safe_mode},
        )
        log.info("run_iniciado", chars=len(user_content))
        try:
            async with asyncio.timeout(self._timeout_s):
                final_state = await self._run_graph(run, user_content)
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
            run.done.set()

    async def _run_graph(self, run: Run, user_content: str) -> dict[str, Any]:
        """Arma el estado de entrada y ejecuta el grafo.

        El hilo del checkpointer (`thread_id = conversation_id`) es el historial
        del agente. Si el hilo está vacío — primer mensaje, o reinicio con el
        checkpointer en memoria — se siembra desde MESSAGES, que es la fuente de
        verdad. Si ya tiene estado, alcanza con mandar el mensaje nuevo.
        """
        config: dict[str, Any] = {
            "configurable": {"thread_id": run.conversation_id, "emitter": run},
            "recursion_limit": self._max_iterations * 2 + 10,
        }
        if await self._thread_has_state(config):
            messages: list[Any] = [HumanMessage(content=user_content)]
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

    # --- Observación ---
    async def stream(self, run: Run, last_event_id: int = 0) -> AsyncIterator[str]:
        """Genera el SSE: replay de lo perdido y después lo que va saliendo."""
        queue = run.add_subscriber()
        sent_seq = last_event_id
        try:
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

    async def wait(self, run: Run) -> None:
        await run.done.wait()

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            await self.cancel(run)
