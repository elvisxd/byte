"""Salud del servicio. `/health` es público y mínimo; el detalle pide credencial."""

from pathlib import Path

from fastapi import APIRouter

from agent.llm import ollama_status
from api.deps import Context, CredentialId
from api.errors import ByteError
from models.schemas import Health, HealthDetails
from tools.code_exec import sandbox_status

router = APIRouter(tags=["salud"])


@router.get("/health", response_model=Health)
async def health(ctx: Context) -> Health:
    if not await ctx.repository.ping():
        raise ByteError("service_unavailable", "Almacenamiento no disponible", status_code=503)
    return Health(status="ok")


@router.get("/health/details", response_model=HealthDetails)
async def health_details(ctx: Context, _credential: CredentialId) -> HealthDetails:
    """Alimenta el indicador "En línea, corriendo local" de la UI."""
    settings = ctx.settings
    ollama = await ollama_status(settings.ollama_base_url, settings.ollama_model)
    db = "ok" if await ctx.repository.ping() else "caido"
    sandbox = (
        await sandbox_status(settings.sandbox_url) if settings.sandbox_url else "no_configurado"
    )
    return HealthDetails(
        status="ok" if ollama == "ok" and db == "ok" else "degraded",
        model=settings.ollama_model,
        ollama=ollama,
        db=db,
        sandbox=sandbox,
        version=settings.version,
        models=[settings.ollama_model, *ctx.runs.modelos()],
        project=Path(settings.project_root).name if settings.project_root else "",
    )
