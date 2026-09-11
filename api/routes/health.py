"""Salud del servicio. `/health` es público y mínimo; el detalle pide credencial."""

from fastapi import APIRouter

from agent.llm import ollama_status
from api.deps import Context, CredentialId
from api.errors import ByteError
from models.schemas import Health, HealthDetails

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
    # El sandbox llega en la Fase 1.
    sandbox = "no_configurado" if not settings.sandbox_url else "sin_verificar"
    return HealthDetails(
        status="ok" if ollama == "ok" and db == "ok" else "degraded",
        model=settings.ollama_model,
        ollama=ollama,
        db=db,
        sandbox=sandbox,
        version=settings.version,
    )
