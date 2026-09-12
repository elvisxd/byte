"""Contexto de la aplicación, autenticación y rate limiting."""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from slowapi import Limiter

from agent.runner import RunManager
from api.auth import JWTService, Passwords, RefreshService
from api.config import Settings, get_settings
from api.errors import ByteError
from api.security import SESSION_COOKIE, Credentials, TokenService
from db.repository import Repository
from models.schemas import User
from tools.base import ToolRegistry


@dataclass
class AppContext:
    """Todo lo que la app arma al arrancar y las rutas necesitan."""

    settings: Settings
    credentials: Credentials
    tokens: TokenService
    passwords: Passwords
    jwt: JWTService
    refresh: RefreshService
    repository: Repository
    runs: RunManager
    registry: ToolRegistry
    checkpointer: Any = None
    # None cuando Byte arranca sin Postgres: /documents y /search devuelven 503.
    rag: Any = None
    # El modelo sin bind_tools, para el /compact manual.
    llm: Any = None
    # Trazas del agente. Apagadas si no hay Langfuse configurado, pero nunca
    # `None`: el objeto apagado es un no-op y evita los `if` en cada llamada.
    trazas: Any = None


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


Context = Annotated[AppContext, Depends(get_context)]


def _bearer(request: Request) -> str | None:
    """El valor de `Authorization: Bearer`, si lo hay."""
    autorizacion = request.headers.get("Authorization", "")
    if not autorizacion.startswith("Bearer "):
        return None
    return autorizacion[7:].strip() or None


def credential_from_request(request: Request) -> str | None:
    """Identifica la credencial: X-API-Key (CLI), Bearer (clientes OpenAI) o
    cookie de sesión (web).

    Devuelve un id no sensible (prefijo del hash), nunca la clave.
    """
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None:
        return None
    api_key = request.headers.get("X-API-Key")
    if api_key and ctx.credentials.verify(api_key):
        return ctx.credentials.credential_id
    # `Authorization: Bearer` lleva dos cosas distintas, y se prueban en este
    # orden: primero el JWT de un usuario (Fase 4), después la API key estática,
    # que es lo único que mandan los clientes de OpenAI y lo que n8n guarda en
    # sus credenciales. Un JWT válido gana porque identifica a alguien; la clave
    # es una credencial de instancia.
    portador = _bearer(request)
    if portador:
        user_id = ctx.jwt.verify(portador)
        if user_id:
            return user_id
        if ctx.credentials.verify(portador):
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


async def require_user(request: Request) -> User:
    """El usuario del JWT, para los endpoints que necesitan una persona.

    La API key no sirve acá: identifica a la instancia, no a alguien. `/me` con
    una API key no tendría qué responder.
    """
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    user_id = ctx.jwt.verify(_bearer(request)) if ctx else None
    usuario = await ctx.repository.get_user(user_id) if ctx and user_id else None
    if usuario is None:
        # 401 y no 403: falta la credencial correcta, no permisos. Y el mismo
        # mensaje para un token inválido que para uno de un usuario borrado.
        raise ByteError("unauthorized", "Hace falta un token de usuario", status_code=401)
    return usuario


CurrentUser = Annotated[User, Depends(require_user)]


def owner_from_request(request: Request) -> str | None:
    """El dueño de lo que se cree o se consulte en este request.

    El `user_id` de la tabla `users` cuando alguien entró con un JWT, y `None`
    cuando entró con la API key o la cookie: esas identifican a la instancia,
    no a una persona, y `conversations.user_id` es `uuid REFERENCES users (id)`
    — el `credential_id` no es un uuid y la FK lo rechazaría.

    Ese `None` es el `SIN_USUARIO` de siempre, así que lo que existía antes del
    multi-usuario sigue siendo visible con la API key y solo con ella. Un
    usuario con JWT ve lo suyo y nada más.
    """
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None:
        return None
    return ctx.jwt.verify(_bearer(request))


async def require_owner(request: Request, _credential: CredentialId) -> str | None:
    """Como `owner_from_request`, pero exigiendo credencial válida primero.

    Las rutas la usan en lugar de `CredentialId` a secas: autentica igual y
    además dice de quién es lo que se toca.
    """
    return owner_from_request(request)


OwnerId = Annotated[str | None, Depends(require_owner)]


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
