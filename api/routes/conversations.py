"""Conversaciones y creación de runs (el corazón de la API)."""

from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

from agent.compact import resumir
from api.deps import Context, CredentialId, limiter, runs_limit
from api.errors import ByteError
from db.repository import title_from_content
from models.schemas import (
    CompactResult,
    Conversation,
    ConversationDetail,
    ConversationList,
    CreateConversationRequest,
    CreateMessageRequest,
    MessagePaused,
    MessageResult,
    PatchConversationRequest,
    RunAccepted,
)

router = APIRouter(tags=["conversaciones"])


async def _require_conversation(ctx: Context, conversation_id: str) -> Conversation:
    conversation = await ctx.repository.get_conversation(conversation_id)
    if conversation is None:
        raise ByteError("not_found", "La conversación no existe", status_code=404)
    return conversation


@router.post("/conversations", response_model=Conversation, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: CreateConversationRequest, ctx: Context, _credential: CredentialId
) -> Conversation:
    return await ctx.repository.create_conversation(payload.title or "Conversación nueva")


@router.get("/conversations", response_model=ConversationList)
async def list_conversations(
    ctx: Context,
    _credential: CredentialId,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> ConversationList:
    items, next_cursor = await ctx.repository.list_conversations(limit, cursor)
    return ConversationList(items=items, next_cursor=next_cursor)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    ctx: Context,
    _credential: CredentialId,
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = None,
    include_tool_messages: bool = False,
) -> ConversationDetail:
    conversation = await _require_conversation(ctx, conversation_id)
    messages, has_more = await ctx.repository.list_messages(
        conversation_id, limit=limit, before=before, include_tool_messages=include_tool_messages
    )
    return ConversationDetail(**conversation.model_dump(), messages=messages, has_more=has_more)


@router.patch("/conversations/{conversation_id}", response_model=Conversation)
async def patch_conversation(
    conversation_id: str,
    payload: PatchConversationRequest,
    ctx: Context,
    _credential: CredentialId,
) -> Conversation:
    updated = await ctx.repository.set_title(conversation_id, payload.title)
    if updated is None:
        raise ByteError("not_found", "La conversación no existe", status_code=404)
    return updated


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str, ctx: Context, credential: CredentialId
) -> Response:
    # Primero se cortan los runs en vuelo: si no, el agente seguiría generando
    # contra un hilo que está por desaparecer.
    await ctx.runs.cancel_conversation(conversation_id, credential)
    deleted = await ctx.repository.delete_conversation(conversation_id)
    if not deleted:
        raise ByteError("not_found", "La conversación no existe", status_code=404)
    # Borrar la conversación también borra el hilo del checkpointer (contrato).
    checkpointer = ctx.checkpointer
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    # La respuesta cambia según ?wait: se declaran las dos para que el spec
    # OpenAPI sirva para generar clientes tipados (el CLI en Go, Fase 6).
    response_model=None,
    responses={
        202: {
            "model": RunAccepted,
            "description": "Run creado: seguirlo por SSE. Con ?wait=true y modo seguro, "
            "el run queda en pausa y se devuelve MessagePaused.",
        },
        200: {"model": MessageResult, "description": "Con ?wait=true: mensaje final"},
    },
)
@limiter.limit(runs_limit)
async def create_message(
    request: Request,
    response: Response,
    conversation_id: str,
    payload: CreateMessageRequest,
    ctx: Context,
    credential: CredentialId,
    wait: bool = False,
) -> Any:
    """Crea el mensaje del usuario y arranca un run.

    Devuelve `202` con el `events_token` para suscribirse al SSE. Con
    `?wait=true` espera el final y devuelve el mensaje completo (tests y scripts).
    """
    await _require_conversation(ctx, conversation_id)
    content = payload.content.strip()
    if not content:
        raise ByteError("validation_error", "El mensaje está vacío", status_code=422)
    if len(content) > ctx.settings.max_message_chars:
        raise ByteError(
            "payload_too_large",
            f"El mensaje supera los {ctx.settings.max_message_chars} caracteres",
            status_code=413,
        )

    # Los chequeos que pueden rechazar el pedido (aprobación pendiente, run en
    # curso, tope de concurrencia) van ANTES de guardar: si no, queda un mensaje
    # del usuario sin ningún run que lo responda. Y si había una aprobación
    # huérfana, se cierra acá, así sus mensajes quedan antes del nuevo.
    await ctx.runs.preparar(conversation_id, credential)

    user_message = await ctx.repository.add_message(conversation_id, "user", content)
    # Si la conversación todavía no tiene título propio, se usa el primer mensaje.
    conversation = await ctx.repository.get_conversation(conversation_id)
    if conversation is not None and conversation.title == "Conversación nueva":
        await ctx.repository.set_title(conversation_id, title_from_content(content))

    run = await ctx.runs.start(conversation_id, credential, content, safe_mode=payload.safe_mode)

    if wait:
        await ctx.runs.wait(run)
        if run.status == "error":
            raise ByteError("run_failed", "El run terminó con error", status_code=500)
        if run.status == "paused":
            # Modo seguro: no hay mensaje que devolver todavía. Se responde con
            # lo que hace falta para decidir y continuar por /resume.
            pendiente = dict(run.awaiting or {})
            pendiente["resume_token"] = ctx.tokens.issue_resume_token(run.id, credential)
            return MessagePaused(run_id=run.id, status="paused", awaiting_approval=pendiente)
        message = await ctx.repository.get_message(run.message_id) if run.message_id else None
        if message is None:
            raise ByteError("run_failed", "El run no produjo respuesta", status_code=500)
        response.status_code = status.HTTP_200_OK
        return MessageResult(message=message, sources=run.sources)

    return RunAccepted(
        run_id=run.id,
        message_id=user_message.id,
        events_url=f"/api/v1/runs/{run.id}/events",
        events_token=ctx.tokens.issue_events_token(run.id),
    )


@router.post(
    "/conversations/{conversation_id}/compact",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CompactResult,
)
@limiter.limit(runs_limit)
async def compact_conversation(
    request: Request,
    conversation_id: str,
    ctx: Context,
    credential: CredentialId,
) -> CompactResult:
    """Fuerza la compactación del historial.

    Normalmente se dispara sola cuando el historial supera ~60% del contexto;
    esto la adelanta. Los mensajes originales no se borran: solo se resumen
    para dejar de mandarlos al modelo.
    """
    await _require_conversation(ctx, conversation_id)
    # Con un run en curso, los dos escribirían el summary de la misma
    # conversación y el último en terminar pisaría al otro.
    if ctx.runs.active_in(conversation_id, credential):
        raise ByteError(
            "conversation_busy",
            "Ya hay un run en curso en esta conversación",
            status_code=409,
        )

    historial = await ctx.repository.history(conversation_id)
    if not historial:
        raise ByteError("nada_para_compactar", "La conversación está vacía", status_code=422)

    conversacion = await ctx.repository.get_conversation(conversation_id)
    previo = conversacion.summary if conversacion else None

    from langchain_core.messages import AIMessage, HumanMessage

    # Los tool messages no van: su contenido es material externo y ya está
    # resumido en lo que respondió el asistente.
    mensajes = [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in historial
        if m.role in ("user", "assistant")
    ]
    if ctx.llm is None:
        raise ByteError("modelo_no_configurado", "No hay modelo para resumir", status_code=503)
    resumen = await resumir(ctx.llm, mensajes, previo)
    if resumen is None:
        raise ByteError(
            "compactacion_fallida",
            "No se pudo generar el resumen (¿el modelo responde?)",
            status_code=503,
        )

    ultimo = next((m.id for m in reversed(historial)), None)
    await ctx.repository.set_summary(conversation_id, resumen, ultimo)
    return CompactResult(summary=resumen, compacted_messages=len(mensajes))
