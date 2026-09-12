"""Registro, login y quién soy.

`POST /auth/register`, `POST /auth/login` y `GET /me`, como pide el contrato.
El JWT que devuelve el login va en `Authorization: Bearer`, el mismo header que
ya acepta la API key: para el resto de la API un usuario autenticado por JWT y
una credencial estática son lo mismo (`api/deps.py`).

Seguridad (docs/seguridad-byte.md):

- Las contraseñas se guardan con **argon2id**, nunca en claro ni con un hash
  rápido. El hash no sale de `db/`.
- El login **no dice si el email existe**: mismo error y mismo tiempo ante un
  email desconocido y una contraseña incorrecta.
- Los dos endpoints están bajo el rate limit de runs, que es el más estricto:
  son los únicos que prueban secretos, así que son el blanco natural de un
  ataque de fuerza bruta.
"""

from fastapi import APIRouter, Request, status

from api.deps import Context, CurrentUser, limiter, runs_limit
from api.errors import ByteError
from api.logging import get_logger
from models.schemas import LoginRequest, RegisterRequest, TokenResponse, User

logger = get_logger("api.auth")

router = APIRouter(tags=["autenticación"])


@router.post("/auth/register", response_model=User, status_code=status.HTTP_201_CREATED)
@limiter.limit(runs_limit)
async def register(request: Request, payload: RegisterRequest, ctx: Context) -> User:
    """Crea un usuario.

    Abierto a propósito: Byte corre local y el registro es para el dueño de la
    instancia. Si alguna vez se expone a internet, esto necesita una invitación
    o una lista blanca — está anotado en el plan.
    """
    usuario = await ctx.repository.create_user(payload.email, ctx.passwords.hash(payload.password))
    if usuario is None:
        # El email ya está tomado. Acá sí se dice: quien registra necesita saber
        # por qué falló, y a diferencia del login no hay un secreto que proteger
        # (el email ya lo escribió quien está del otro lado).
        raise ByteError("conflict", "Ese email ya está registrado", status_code=409)
    logger.info("usuario_registrado", user_id=usuario.id)
    return usuario


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit(runs_limit)
async def login(request: Request, payload: LoginRequest, ctx: Context) -> TokenResponse:
    encontrado = await ctx.repository.get_user_by_email(payload.email)
    if encontrado is None:
        # Se gasta el mismo tiempo que una verificación real: si no, la
        # diferencia mide qué emails existen.
        ctx.passwords.gastar_tiempo()
        raise ByteError("unauthorized", "Email o contraseña incorrectos", status_code=401)

    usuario, password_hash = encontrado
    if not ctx.passwords.verify(password_hash, payload.password):
        raise ByteError("unauthorized", "Email o contraseña incorrectos", status_code=401)

    # El login es el único momento en que la contraseña en claro está
    # disponible, así que es donde se migra un hash con parámetros viejos.
    if ctx.passwords.necesita_rehash(password_hash):
        await ctx.repository.set_password_hash(usuario.id, ctx.passwords.hash(payload.password))
        logger.info("password_rehasheada", user_id=usuario.id)

    return TokenResponse(
        access_token=ctx.jwt.issue(usuario.id),
        expires_in=ctx.jwt.ttl_s,
    )


@router.get("/me", response_model=User)
async def me(usuario: CurrentUser) -> User:
    """Quién soy. Solo responde con un JWT: la API key no es un usuario."""
    return usuario
