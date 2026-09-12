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
    """Quién hace este pedido: un usuario con JWT, o la instancia con su clave.

    **Las dos credenciales conviven a propósito**, y cada una tiene su lugar:

    - El **JWT** identifica a una persona. Lo que crea es suyo y nadie más lo ve
      (`owner_from_request` devuelve su `user_id`).
    - La **API key** identifica a la instancia, no a alguien. Sus datos son los
      que tienen `user_id = NULL`: lo que existía antes del multi-usuario, más
      lo que entra por el CLI, por n8n o por un cliente de OpenAI. Ninguno de
      esos tiene dónde guardar una sesión ni a quién pedirle una contraseña.

    Por eso la clave no se retira ni se limita a `/v1`: retirarla obligaría a
    registrarse para usar el CLI de tu propia instancia local, que es la
    herramienta con la que se trabaja todos los días. El costo de tenerla es
    real —es eterna y no se revoca sin cambiar el `.env`— y por eso `/me` la
    rechaza: para lo que necesita saber quién sos, no alcanza.

    Devuelve un id no sensible, nunca la clave (ver `Credentials.credential_id`).
    """
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None:
        return None
    # **El JWT primero, antes que cualquier otra cosa.** No es preferencia de
    # estilo: `owner_from_request` mira solo el Bearer, así que si la API key
    # ganara, un pedido con las dos cabeceras —lo que manda cualquier cliente
    # que dejó la clave configurada y además inició sesión— crearía la
    # conversación a nombre del usuario y el run a nombre de la instancia.
    # Cualquiera con la clave podría entonces ver y cancelar el run de esa
    # persona, mientras ella recibe 404 en el suyo.
    portador = _bearer(request)
    if portador:
        user_id = ctx.jwt.verify(portador)
        if user_id:
            return user_id

    # Después la API key, por header propio o como Bearer: es lo único que
    # mandan los clientes de OpenAI y lo que n8n guarda en sus credenciales.
    api_key = request.headers.get("X-API-Key")
    if api_key and ctx.credentials.verify(api_key):
        return ctx.credentials.credential_id
    if portador and ctx.credentials.verify(portador):
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
    """Como `owner_from_request`, pero exigiendo credencial válida y un usuario
    que exista de verdad.

    Las rutas la usan en lugar de `CredentialId` a secas: autentica igual y
    además dice de quién es lo que se toca.

    **El `sub` del token se verifica contra la base, no se cree.** Un JWT sigue
    siendo válido durante sus 30 minutos aunque su usuario ya no exista, y sin
    este chequeo el borrado no tendría efecto hasta que venciera: en Postgres,
    escribir daba un 500 por la FK —y en memoria, donde no hay integridad
    referencial, el token borrado seguía leyendo y escribiendo como si nada.
    De paso, un `sub` que no sea un uuid se rechaza acá en vez de llegar al SQL
    y volver como un 500.
    """
    user_id = owner_from_request(request)
    if user_id is None:
        # Entró con la API key o la cookie: la instancia, no una persona.
        return None
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None or await ctx.repository.get_user(user_id) is None:
        raise ByteError("unauthorized", "El usuario del token ya no existe", status_code=401)
    return user_id


OwnerId = Annotated[str | None, Depends(require_owner)]


def _ip(request: Request) -> str:
    client = request.client
    return f"ip:{client.host if client else 'desconocida'}"


def rate_limit_key(request: Request) -> str:
    """El rate limit es por credencial; si no hay, por IP."""
    credential_id = credential_from_request(request)
    if credential_id:
        return f"cred:{credential_id}"
    return _ip(request)


def rate_limit_por_ip(request: Request) -> str:
    """Siempre por IP, sin mirar la credencial.

    Es el que usan `/auth/login` y `/auth/register`, y la diferencia importa:
    ahí quien ataca **sí tiene** credenciales válidas —la API key, o una cuenta
    propia recién registrada— así que limitar por credencial le da una cubeta
    nueva por cada identidad que consiga. Con el registro abierto eso no tiene
    techo: N cuentas son N veces el límite contra la misma víctima, desde la
    misma IP.
    """
    return _ip(request)


def general_limit() -> str:
    return get_settings().rate_limit_general


def runs_limit() -> str:
    return get_settings().rate_limit_runs


# El límite general aplica a toda la API vía SlowAPIMiddleware; las rutas que
# crean runs suman el suyo, más ajustado, con @limiter.limit(runs_limit).
limiter = Limiter(key_func=rate_limit_key, default_limits=[general_limit])

# Para `/auth/*`: la misma cuenta de intentos, pero por IP. Ver
# `rate_limit_por_ip` — con el limitador normal, cada credencial que el atacante
# ya tenga le da una cubeta nueva contra la misma víctima.
limiter_auth = Limiter(key_func=rate_limit_por_ip, default_limits=[general_limit])
