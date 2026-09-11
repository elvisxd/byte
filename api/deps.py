"""Contexto de la aplicación, autenticación y rate limiting."""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from slowapi import Limiter

from agent.runner import RunManager
from api.config import Settings, get_settings
from api.errors import ByteError
from api.security import SESSION_COOKIE, Credentials, TokenService
from db.repository import Repository
from tools.base import ToolRegistry


@dataclass
class AppContext:
    """Todo lo que la app arma al arrancar y las rutas necesitan."""

    settings: Settings
    credentials: Credentials
    tokens: TokenService
    repository: Repository
    runs: RunManager
    registry: ToolRegistry
    checkpointer: Any = None
    # None cuando Byte arranca sin Postgres: /documents y /search devuelven 503.
    rag: Any = None
    # El modelo sin bind_tools, para el /compact manual.
    llm: Any = None


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


Context = Annotated[AppContext, Depends(get_context)]


def credential_from_request(request: Request) -> str | None:
    """Identifica la credencial: header X-API-Key (CLI) o cookie de sesión (web).

    Devuelve un id no sensible (prefijo del hash), nunca la clave.
    """
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None:
        return None
    api_key = request.headers.get("X-API-Key")
    if api_key and ctx.credentials.verify(api_key):
        return ctx.credentials.credential_id
    session = request.cookies.get(SESSION_COOKIE)
    verified = ctx.tokens.verify_session(session)
    if verified and verified == ctx.credentials.credential_id:
        return verified
    return None


async def require_credential(request: Request) -> str:
    credential_id = credential_from_request(request)
    if credential_id is None:
        raise ByteError("unauthorized", "Credencial faltante o inválida", status_code=401)
    return credential_id


CredentialId = Annotated[str, Depends(require_credential)]


def rate_limit_key(request: Request) -> str:
    """El rate limit es por credencial; si no hay, por IP."""
    credential_id = credential_from_request(request)
    if credential_id:
        return f"cred:{credential_id}"
    client = request.client
    return f"ip:{client.host if client else 'desconocida'}"


def general_limit() -> str:
    return get_settings().rate_limit_general


def runs_limit() -> str:
    return get_settings().rate_limit_runs


# El límite general aplica a toda la API vía SlowAPIMiddleware; las rutas que
# crean runs suman el suyo, más ajustado, con @limiter.limit(runs_limit).
limiter = Limiter(key_func=rate_limit_key, default_limits=[general_limit])
