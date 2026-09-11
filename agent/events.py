"""Eventos del protocolo AG-UI y su serialización a SSE.

Se usan los nombres del estándar (no nombres propios) para que la web, el CLI en
Go y cualquier cliente AG-UI entiendan el stream sin traducción. Cada evento
lleva un `id:` incremental para que el cliente reconecte con `Last-Event-ID`.
"""

import json
from dataclasses import dataclass
from typing import Any


class AGUI:
    """Nombres de evento AG-UI que emite Byte (tabla del contrato)."""

    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"


@dataclass(frozen=True, slots=True)
class Event:
    """Un evento ya numerado, listo para mandar por SSE."""

    seq: int
    type: str
    data: dict[str, Any]

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, **self.data}, ensure_ascii=False)
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"


def keepalive() -> str:
    """Comentario SSE para que proxies y balanceadores no corten el stream."""
    return ": keepalive\n\n"
