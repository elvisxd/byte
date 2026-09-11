"""Herramientas disponibles para el agente."""

from fastapi import APIRouter

from api.deps import Context, CredentialId
from models.schemas import ToolInfo, ToolList

router = APIRouter(tags=["herramientas"])


@router.get("/tools", response_model=ToolList)
async def list_tools(ctx: Context, _credential: CredentialId) -> ToolList:
    return ToolList(
        tools=[ToolInfo(name=tool.name, source=tool.source) for tool in ctx.registry.all()]
    )
