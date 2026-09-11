"""Credenciales, tokens firmados y cabeceras de seguridad.

Decisiones (ver docs/seguridad-byte.md):
- La API key se guarda hasheada y se compara con `secrets.compare_digest`.
- La web usa cookie httpOnly + SameSite=Strict, porque EventSource no puede
  mandar headers y la API key nunca debe ir en la URL.
- `GET /runs/{id}/events` acepta además un token firmado de 60 s, de un solo
  uso y ligado al run_id.
"""

import hashlib
import secrets
import time

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from api.config import Settings
from api.logging import get_logger

logger = get_logger("api.security")

SESSION_COOKIE = "byte_session"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Credentials:
    """Guarda el hash de la API key y valida las que llegan."""

    def __init__(self, api_key: str) -> None:
        # El valor en claro no se conserva: solo su hash.
        self._key_hash = hash_secret(api_key) if api_key else ""

    @property
    def configured(self) -> bool:
        return bool(self._key_hash)

    @property
    def credential_id(self) -> str:
        """Identificador estable y no sensible, para rate limiting y dueño del run."""
        return self._key_hash[:12] if self._key_hash else "anon"

    def verify(self, provided: str | None) -> bool:
        if not self._key_hash or not provided:
            return False
        return secrets.compare_digest(hash_secret(provided), self._key_hash)


class TokenService:
    """Tokens firmados: cookie de sesión y events_token de un solo uso."""

    def __init__(self, secret_key: str, events_ttl_s: int, session_ttl_s: int) -> None:
        self._session_signer = TimestampSigner(secret_key, salt="byte-session")
        self._events_signer = TimestampSigner(secret_key, salt="byte-events")
        self._events_ttl_s = events_ttl_s
        self._session_ttl_s = session_ttl_s
        # Nonces ya canjeados: garantiza el "un solo uso".
        self._spent: dict[str, float] = {}

    # --- Sesión web ---
    def issue_session(self, credential_id: str) -> str:
        return self._session_signer.sign(credential_id).decode()

    def verify_session(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            value = self._session_signer.unsign(token, max_age=self._session_ttl_s)
        except (BadSignature, SignatureExpired):
            return None
        return value.decode()

    # --- events_token ---
    def issue_events_token(self, run_id: str) -> str:
        nonce = secrets.token_urlsafe(8)
        return self._events_signer.sign(f"{run_id}:{nonce}").decode()

    def verify_events_token(self, token: str | None, run_id: str) -> bool:
        """Válido si la firma es buena, no venció, es del run pedido y no se usó."""
        if not token:
            return False
        try:
            raw = self._events_signer.unsign(token, max_age=self._events_ttl_s).decode()
        except (BadSignature, SignatureExpired):
            return False
        token_run_id, _, nonce = raw.partition(":")
        if token_run_id != run_id or not nonce:
            return False
        self._prune_spent()
        if nonce in self._spent:
            return False
        self._spent[nonce] = time.monotonic()
        return True

    def _prune_spent(self) -> None:
        """Los nonces solo importan mientras el token podría seguir vigente."""
        cutoff = time.monotonic() - self._events_ttl_s
        for nonce, seen_at in list(self._spent.items()):
            if seen_at < cutoff:
                del self._spent[nonce]


def resolve_secret_key(settings: Settings) -> str:
    """En prod el secreto es obligatorio; en dev se genera uno efímero."""
    if settings.secret_key:
        return settings.secret_key
    if settings.env == "prod":
        raise RuntimeError("BYTE_SECRET_KEY es obligatorio con BYTE_ENV=prod")
    ephemeral = secrets.token_urlsafe(32)
    logger.warning(
        "secret_key_efimero", detail="sin BYTE_SECRET_KEY: las sesiones no sobreviven al reinicio"
    )
    return ephemeral


# Swagger UI y ReDoc cargan sus assets de este CDN. Es la única excepción a la
# CSP y aplica solo a /docs y /redoc, nunca a la página del chat ni a la API.
DOCS_CDN = "https://cdn.jsdelivr.net"


def security_headers(env: str, *, docs: bool = False) -> dict[str, str]:
    """CSP sin 'unsafe-inline': el JS de la página va en un archivo aparte.

    Con `docs=True` se permite el CDN de Swagger UI y sus estilos inline, que es
    lo que /docs necesita para renderizar.
    """
    if docs:
        csp = (
            f"default-src 'self'; script-src 'self' {DOCS_CDN}; "
            f"style-src 'self' 'unsafe-inline' {DOCS_CDN}; "
            f"img-src 'self' data: {DOCS_CDN}; worker-src blob:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
    else:
        csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'self'"
        )
    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    }
    if env == "prod":
        # HSTS solo con HTTPS real, si no rompe el desarrollo en localhost.
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
