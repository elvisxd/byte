"""Esquemas de entrada y salida de la API (contrato en docs/api-contrato-byte.md).

Cubre CONVERSATIONS y MESSAGES del ERD y, desde la Fase 2, DOCUMENTS y los
esquemas de la búsqueda. DOCUMENT_CHUNKS no se expone: los chunks solo salen
como resultados de búsqueda, nunca como recurso propio.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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


class User(BaseModel):
    """Un usuario. El hash de la contraseña nunca sale de `db/`: este modelo es
    lo que la API puede devolver."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    created_at: datetime


class RegisterRequest(BaseModel):
    email: EmailStr
    # 12 caracteres como mínimo, que es lo que recomienda NIST para una
    # contraseña sin complejidad obligatoria. El tope existe porque argon2 hashea
    # lo que le den: sin él, un cuerpo de 1 MB es un DoS de CPU por request.
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """El formato que pide el contrato, que es el de OAuth2 password grant."""

    access_token: str
    # "bearer" es el tipo de token de OAuth2, no un secreto (S105 lo confunde).
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    # El refresh se rota en cada uso: el que vuelve acá reemplaza al anterior,
    # que deja de valer en el momento.
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


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


class CompactResult(BaseModel):
    """Lo que devuelve el /compact manual."""

    summary: str
    compacted_messages: int


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


# --- Compatibilidad con OpenAI (Fase 3) ---
#
# Los nombres son los de OpenAI, no los de Byte: el punto del endpoint es que un
# cliente que ya existe (Open WebUI, Continue.dev, el SDK) funcione sin cambios,
# y eso obliga a hablar su vocabulario. Por eso acá hay `messages` en vez de
# `content`, y `object`/`created` que Byte no usa en el resto de la API.


class OpenAIMessage(BaseModel):
    """Un turno de la conversación tal como lo manda un cliente de OpenAI.

    `content` puede venir como texto o como lista de partes (el formato nuevo,
    que usan los clientes con imágenes). Se acepta la lista y se toma el texto:
    rechazarla dejaría afuera a clientes que solo mandan texto igual.
    """

    role: str
    content: str | list[dict[str, Any]] | None = None

    def texto(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            partes = [p.get("text", "") for p in self.content if p.get("type") == "text"]
            return "\n".join(t for t in partes if t)
        return ""


class ChatCompletionRequest(BaseModel):
    """Lo que Byte mira de un pedido de OpenAI.

    El resto de los campos del formato (`temperature`, `top_p`, `n`, `seed`…) se
    aceptan y se ignoran: los define la configuración de Byte, no el cliente.
    Ignorarlos en silencio es mejor que un 422, porque los clientes los mandan
    siempre y rechazarlos los rompería sin ganar nada.
    """

    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[OpenAIMessage] = Field(min_length=1)
    stream: bool = False


class OpenAIModel(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "byte"


class OpenAIModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[OpenAIModel]
