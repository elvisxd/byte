"""Esquemas de entrada y salida de la API (contrato en docs/api-contrato-byte.md).

En el MVP solo existen CONVERSATIONS y MESSAGES del ERD; DOCUMENTS y
DOCUMENT_CHUNKS llegan en la Fase 2 con el RAG.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["user", "assistant", "tool"]
RunStatusValue = Literal["running", "paused", "finished", "cancelled", "error"]

# Tope duro de longitud de un mensaje de usuario (docs/seguridad-byte.md).
# BYTE_MAX_MESSAGE_CHARS puede bajarlo, nunca subirlo por encima de esto.
HARD_MAX_MESSAGE_CHARS = 8000


class Message(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: MessageRole
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    langfuse_trace_id: str | None = None
    created_at: datetime


class Conversation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    summary_up_to_message_id: str | None = None


class ConversationListItem(BaseModel):
    id: str
    title: str
    preview: str
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationListItem]
    next_cursor: str | None = None


class ConversationDetail(Conversation):
    messages: list[Message] = Field(default_factory=list)
    has_more: bool = False


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class PatchConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CreateMessageRequest(BaseModel):
    # El tope real se valida en la ruta contra BYTE_MAX_MESSAGE_CHARS y responde 413.
    content: str = Field(min_length=1)
    safe_mode: bool = False


class RunAccepted(BaseModel):
    run_id: str
    message_id: str
    events_url: str
    events_token: str


class MessageResult(BaseModel):
    """Respuesta de POST .../messages?wait=true (tests y scripts)."""

    message: Message
    sources: list[dict[str, Any]] = Field(default_factory=list)


class RunState(BaseModel):
    status: RunStatusValue
    iterations: int
    started_at: datetime
    finished_at: datetime | None = None


class SessionRequest(BaseModel):
    """Canje de API key por cookie httpOnly (la web no puede mandar headers en SSE)."""

    api_key: str = Field(min_length=1, max_length=512)


class ToolInfo(BaseModel):
    name: str
    source: str


class ToolList(BaseModel):
    tools: list[ToolInfo]


class Health(BaseModel):
    status: Literal["ok", "degraded"]


class HealthDetails(BaseModel):
    status: Literal["ok", "degraded"]
    model: str
    ollama: str
    db: str
    sandbox: str
    version: str
