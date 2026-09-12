"""Usuarios: contraseñas con argon2 y JWT de vida corta.

Es la mitad de la Fase 4 que convierte `SIN_USUARIO` en alguien. Lo que no
cambia: los endpoints siguen siendo los mismos y la API key sigue valiendo
(docs/api-contrato-byte.md). Un JWT es otra forma de llegar, no otra API.

Decisiones (docs/seguridad-byte.md):

- **argon2id** para las contraseñas, con los parámetros por defecto de
  `argon2-cffi`, que sigue las recomendaciones del RFC 9106. No se elige a mano
  un coste: la librería lo sube cuando el consenso cambia, y `check_needs_rehash`
  deja migrar los hashes viejos en el próximo login sin pedirle nada al usuario.
- **JWT de 30 minutos.** El contrato pide 15-60. Corto porque no hay revocación:
  un token robado vale hasta que expire, y no hay lista negra que lo corte.
- **El secreto es `BYTE_SECRET_KEY`**, el mismo que firma la cookie y los tokens
  de un solo uso. Un secreto aparte para JWT sería una variable más que rotar y
  un archivo más donde olvidarla; el checklist pide 256 bits, que es lo que
  `resolve_secret_key` ya exige.
- **El login no dice si el email existe.** Responde lo mismo ante un email
  desconocido y una contraseña incorrecta, y gasta el mismo tiempo: sin eso,
  `/auth/login` es un oráculo para enumerar usuarios.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from api.logging import get_logger

logger = get_logger("api.auth")

ALGORITMO = "HS256"

# Un hash de descarte para gastar el mismo tiempo cuando el email no existe. Sin
# esto, el login responde mucho más rápido ante un usuario desconocido que ante
# una contraseña incorrecta, y esa diferencia se mide: es una forma de enumerar
# quién está registrado.
_HASH_SEÑUELO = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2Vub3Vlbm9zZW51ZWxv$"
    "j8iC1cMMvoMzjhfpR3fPCGGiKlMgg0M2llH1V1x6y0Y"
)


class Passwords:
    """Hashea y verifica contraseñas con argon2id."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """Si la contraseña corresponde a ese hash.

        Se atrapa `VerificationError` entera, no solo `VerifyMismatchError`: un
        hash corrupto o truncado en la base levanta la clase padre, y eso tiene
        que ser un login fallido, no un 500 que además delata que el usuario
        existe. `InvalidHashError` cubre el caso de un valor que ni siquiera
        parece un hash.
        """
        try:
            self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
        return True

    def gastar_tiempo(self) -> None:
        """Verifica contra un hash de descarte, para que un email desconocido
        cueste lo mismo que una contraseña incorrecta."""
        self.verify(_HASH_SEÑUELO, "no importa")

    def necesita_rehash(self, password_hash: str) -> bool:
        """Si el hash quedó con parámetros viejos. Se rehashea en el login, que
        es el único momento en que la contraseña en claro está disponible."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return False


class JWTService:
    """Emite y verifica los access token."""

    def __init__(self, secret_key: str, ttl_s: int) -> None:
        self._secret = secret_key
        self._ttl_s = ttl_s

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def issue(self, user_id: str) -> str:
        ahora = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": user_id,
                "iat": ahora,
                "exp": ahora + timedelta(seconds=self._ttl_s),
            },
            self._secret,
            algorithm=ALGORITMO,
        )

    def verify(self, token: str | None) -> str | None:
        """El `user_id` del token, o `None` si no sirve.

        `algorithms` explícito y `require: ["sub", "exp"]`: sin lo primero, un
        token con `alg: none` pasaría; sin lo segundo, uno sin `exp` valdría
        para siempre.
        """
        if not token:
            return None
        try:
            datos: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITMO],
                options={"require": ["sub", "exp"]},
            )
        except jwt.InvalidTokenError:
            return None
        sub = datos.get("sub")
        return sub if isinstance(sub, str) and sub else None


class RefreshService:
    """Emite, canjea y revoca refresh tokens.

    El access token dura 30 minutos y no se revoca: robarlo cuesta poco. El
    refresh dura días, así que robarlo cuesta mucho más — y por eso este sí se
    guarda, se rota y se puede revocar.

    **Rotación con detección de reuso** (OAuth 2.0 BCP, 4.13.2): cada canje
    invalida el token usado y emite uno nuevo de la misma familia. Si aparece
    uno ya canjeado, hay dos copias dando vueltas —la del ladrón y la del dueño,
    sin forma de saber cuál es cuál— así que se revoca la familia entera y los
    dos tienen que volver a entrar. Es molesto a propósito: la alternativa es
    dejar la sesión robada viva.

    El token es aleatorio y **se guarda hasheado**, como una contraseña. Acá
    alcanza SHA-256 y no hace falta argon2: son 256 bits de aleatorio, no hay
    diccionario que probar, y el login no puede costar 30 ms por intento.
    """

    def __init__(self, repository: Any, ttl_s: int) -> None:
        self._repo = repository
        self._ttl_s = ttl_s

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def emitir(self, user_id: str, family_id: str | None = None) -> str:
        """Un token nuevo. Sin `family_id` arranca una familia (login nuevo)."""
        token = secrets.token_urlsafe(32)
        await self._repo.save_refresh_token(
            user_id,
            self._hash(token),
            family_id or str(uuid.uuid4()),
            datetime.now(UTC) + timedelta(seconds=self._ttl_s),
        )
        return token

    async def canjear(self, token: str | None) -> tuple[str, str] | None:
        """`(user_id, token nuevo)` si el canje vale, `None` si no.

        Un token reusado no solo falla: revoca la familia, así que la sesión
        robada y la legítima caen juntas.
        """
        if not token:
            return None
        resultado = await self._repo.use_refresh_token(self._hash(token))
        if resultado.reusado and resultado.family_id:
            revocados = await self._repo.revoke_refresh_family(resultado.family_id)
            logger.warning(
                "refresh_token_reusado",
                family_id=resultado.family_id,
                revocados=revocados,
                detail="un token ya canjeado volvió a aparecer: se corta la familia entera",
            )
            return None
        if not resultado.user_id or not resultado.family_id:
            return None
        return resultado.user_id, await self.emitir(resultado.user_id, resultado.family_id)

    async def revocar(self, token: str | None) -> bool:
        """Cierra la sesión: revoca la familia del token (logout).

        Consulta la familia sin canjear el token: si lo marcara como usado, el
        siguiente intento parecería un reuso y se loguearía como si alguien
        hubiera robado algo. Cerrar sesión no es un incidente.
        """
        if not token:
            return False
        family_id = await self._repo.family_of_refresh_token(self._hash(token))
        if family_id is None:
            return False
        await self._repo.revoke_refresh_family(family_id)
        return True
