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


class ResumeRequest(BaseModel):
    """Decisión del usuario sobre lo que el run dejó esperando (modo seguro)."""

    resume_token: str = Field(min_length=1, max_length=512)
    approve: bool


class RunResumed(BaseModel):
    """Respuesta de POST /runs/{id}/resume."""

    run_id: str


class MessagePaused(BaseModel):
    """Respuesta de POST .../messages?wait=true cuando el run queda esperando.

    Con modo seguro no hay mensaje final que devolver: el run se detuvo y
    necesita una decisión humana. El `resume_token` viene adentro de
    `awaiting_approval` para que un script pueda seguir sin leer el SSE.
    """

    run_id: str
    status: Literal["paused"]
    awaiting_approval: dict[str, Any] = Field(default_factory=dict)


class RunState(BaseModel):
    status: RunStatusValue
    iterations: int
    started_at: datetime
    finished_at: datetime | None = None


# Topes de /execute (docs/api-contrato-byte.md).
MAX_EXECUTE_CODE_CHARS = 50 * 1024
MAX_EXECUTE_TIMEOUT_S = 30

# Tope de la query de /search. Coincide con el que se le impone al modelo en
# las herramientas de búsqueda (BYTE_MAX_SEARCH_QUERY_CHARS).
MAX_SEARCH_QUERY_CHARS = 200


class ExecuteRequest(BaseModel):
    """Ejecución directa de código, sin pasar por el agente (CLI y scripts)."""

    language: Literal["python"] = "python"
    # El tope se valida en la ruta para poder responder 413 y no 422.
    code: str = Field(min_length=1)
    timeout_s: int = Field(default=10, ge=1, le=MAX_EXECUTE_TIMEOUT_S)


class ExecuteResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool = False


class SessionRequest(BaseModel):
    """Canje de API key por cookie httpOnly (la web no puede mandar headers en SSE)."""

    api_key: str = Field(min_length=1, max_length=512)


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    """Formato de error del contrato. Lo devuelven todos los handlers."""

    error: ErrorDetail


class ToolInfo(BaseModel):
    name: str
    source: str


class ToolList(BaseModel):
    tools: list[ToolInfo]


class DocumentInfo(BaseModel):
    """Un documento del RAG, como lo muestra la pantalla de carga."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: Literal["processing", "indexed", "error"]
    size_bytes: int
    mime_type: str
    chunks: int
    uploaded_at: datetime
    error_message: str | None = None


class DocumentList(BaseModel):
    items: list[DocumentInfo]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_SEARCH_QUERY_CHARS)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    snippet: str
    score: float


class SearchResults(BaseModel):
    results: list[SearchHit]


class Health(BaseModel):
    status: Literal["ok", "degraded"]


class HealthDetails(BaseModel):
    status: Literal["ok", "degraded"]
    model: str
    ollama: str
    db: str
    sandbox: str
    version: str
