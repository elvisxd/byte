"""Formato de errores del contrato: {"error": {code, message, request_id}}.

Al cliente nunca le llegan stack traces ni rutas internas: el detalle va al log
con el mismo request_id para poder correlacionar.
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.logging import get_logger, get_request_id

logger = get_logger("api.errors")

# Código por defecto para cada status HTTP del contrato.
_CODE_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


class ByteError(Exception):
    """Error de dominio con código estable para el cliente.

    El `code` va en **snake_case en inglés**, siempre. Es lo que el CLI en Go y
    la UI van a usar en un switch, así que cruza la frontera hacia el cliente y
    no puede mezclar idiomas: los mensajes van en español, los códigos no.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.headers = headers


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": get_request_id()}}
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ByteError)
    async def _byte_error(_request: Request, exc: ByteError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_BY_STATUS.get(exc.status_code, "error")
        detail = exc.detail if isinstance(exc.detail, str) else code
        headers = dict(exc.headers) if exc.headers else None
        return error_response(exc.status_code, code, detail, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Se resume el primer problema; el detalle completo queda en el log.
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
        message = f"{field}: {first.get('msg', 'inválido')}"
        logger.info("validation_error", errors=len(exc.errors()))
        return error_response(422, "validation_error", message)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error_type=type(exc).__name__)
        return error_response(500, "internal_error", "Error interno")
