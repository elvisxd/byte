"""Observación y control de runs: SSE de eventos AG-UI, cancelar y consultar."""

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from api.deps import Context, CredentialId, OwnerId, credential_from_request
from api.errors import ByteError
from models.schemas import Message, ResumeRequest, RunResumed, RunState

router = APIRouter(tags=["runs"])

# Anti-buffering: sin esto, un proxy puede acumular el stream y la UI se ve congelada.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _last_event_id(request: Request, fallback: str | None) -> int:
    """Lee `Last-Event-ID` (o `?last_event_id=`) para retomar donde quedó."""
    raw = request.headers.get("Last-Event-ID") or fallback
    try:
        return max(int(raw), 0) if raw else 0
    except (TypeError, ValueError):
        return 0


@router.get("/runs/{run_id}/events")
async def run_events(
    request: Request,
    run_id: str,
    ctx: Context,
    token: str | None = None,
    last_event_id: str | None = None,
) -> StreamingResponse:
    """Stream SSE del run.

    Autoriza por credencial (header o cookie) o por `events_token`: firmado, de
    60 s, un solo uso y ligado a este run.
    """
    credential_id = credential_from_request(request)
    authorized = credential_id is not None or ctx.tokens.verify_events_token(token, run_id)
    if not authorized:
        raise ByteError("unauthorized", "Credencial faltante o inválida", status_code=401)

    run = ctx.runs.get(run_id)
    if run is None or (credential_id is not None and run.credential_id != credential_id):
        raise ByteError("not_found", "El run no existe", status_code=404)

    return StreamingResponse(
        ctx.runs.stream(run, _last_event_id(request, last_event_id)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(run_id: str, ctx: Context, credential: CredentialId) -> Response:
    """Botón "detener": corta la generación y el loop del grafo."""
    run = ctx.runs.require(run_id, credential)
    await ctx.runs.cancel(run)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/runs/{run_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunResumed,
)
async def resume_run(
    run_id: str,
    payload: ResumeRequest,
    ctx: Context,
    credential: CredentialId,
) -> RunResumed:
    """Retoma un run que quedó esperando confirmación humana (modo seguro).

    Es el mismo run: continúa desde el checkpoint y el cliente se vuelve a
    suscribir a `/runs/{id}/events` con el último `Last-Event-ID` que vio.
    """
    run = ctx.runs.require(run_id, credential)
    # El token es aleatorio, firmado, de un solo uso y está ligado a este run y
    # a esta credencial.
    if not ctx.tokens.verify_resume_token(payload.resume_token, run_id, credential):
        raise ByteError("unauthorized", "resume_token inválido o ya usado", status_code=401)
    await ctx.runs.resume(run, payload.approve)
    return RunResumed(run_id=run.id)


@router.get("/runs/{run_id}", response_model=RunState)
async def get_run(run_id: str, ctx: Context, credential: CredentialId) -> RunState:
    run = ctx.runs.require(run_id, credential)
    return RunState(
        status=run.status,
        iterations=run.iterations,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.get("/messages/{message_id}", response_model=Message)
async def get_message(message_id: str, ctx: Context, owner: OwnerId) -> Message:
    # Filtra por dueño: sin esto sería un IDOR directo a los mensajes de otro.
    message = await ctx.repository.get_message(message_id, owner)
    if message is None:
        raise ByteError("not_found", "El mensaje no existe", status_code=404)
    return message
