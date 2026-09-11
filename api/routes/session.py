"""Sesión de la web: canje de API key por cookie httpOnly.

EventSource no puede mandar headers y la API key nunca debe ir en la URL, así que
la web trabaja con cookie `httpOnly` + `SameSite=Strict`.
"""

from fastapi import APIRouter, Request, Response, status

from api.deps import Context, limiter, runs_limit
from api.errors import ByteError
from api.security import SESSION_COOKIE
from models.schemas import SessionRequest

router = APIRouter(tags=["sesión"])


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(runs_limit)
async def create_session(
    request: Request, response: Response, payload: SessionRequest, ctx: Context
) -> Response:
    if not ctx.credentials.verify(payload.api_key):
        raise ByteError("unauthorized", "API key inválida", status_code=401)
    response.set_cookie(
        SESSION_COOKIE,
        ctx.tokens.issue_session(ctx.credentials.credential_id),
        max_age=ctx.settings.session_ttl_s,
        httponly=True,
        samesite="strict",
        secure=ctx.settings.env == "prod",
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
