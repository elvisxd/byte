"""Ejecución directa de código en el sandbox (`byte run script.py` del CLI).

Es el mismo sandbox que usa el agente, con los mismos límites. La API nunca
ejecuta nada en su propio proceso.
"""

from fastapi import APIRouter, Request

from api.deps import Context, CredentialId, limiter, runs_limit
from api.errors import ByteError
from models.schemas import MAX_EXECUTE_CODE_CHARS, ExecuteRequest, ExecuteResult
from tools.code_exec import SandboxNoDisponible, ejecutar_en_sandbox

router = APIRouter(tags=["herramientas"])


@router.post("/execute", response_model=ExecuteResult)
@limiter.limit(runs_limit)
async def execute(
    request: Request,
    payload: ExecuteRequest,
    ctx: Context,
    _credential: CredentialId,
) -> ExecuteResult:
    settings = ctx.settings
    if not settings.sandbox_url or not settings.sandbox_token:
        raise ByteError(
            "sandbox_not_configured",
            "La ejecución de código no está configurada (SANDBOX_URL y SANDBOX_TOKEN)",
            status_code=503,
        )
    if len(payload.code) > MAX_EXECUTE_CODE_CHARS:
        raise ByteError(
            "payload_too_large",
            f"El código supera los {MAX_EXECUTE_CODE_CHARS} caracteres",
            status_code=413,
        )

    try:
        resultado = await ejecutar_en_sandbox(
            settings.sandbox_url, settings.sandbox_token, payload.code, payload.timeout_s
        )
    except SandboxNoDisponible as exc:
        raise ByteError("sandbox_unavailable", "El sandbox no responde", status_code=503) from exc

    return ExecuteResult(**resultado)
