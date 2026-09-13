"""La herramienta con la que el agente abre una skill.

El índice de qué hay va en el prompt; el contenido se lee cuando hace falta.
Meterlas todas enteras llenaría el contexto con instrucciones sobre tareas que
no se están haciendo.
"""

from pydantic import BaseModel, Field

from agent.skills import MAX_SKILL_CHARS, Skill
from tools.base import Tool, ToolResult


class LeerSkillArgs(BaseModel):
    nombre: str = Field(description="El nombre de la skill, como aparece en la lista")


def build_skill_tool(skills: list[Skill]) -> Tool | None:
    """La herramienta, o None si no hay skills que leer."""
    if not skills:
        return None
    por_nombre = {s.nombre: s for s in skills}

    async def leer(args: BaseModel) -> ToolResult:
        pedida = getattr(args, "nombre", "").strip()
        skill = por_nombre.get(pedida)
        if skill is None:
            return ToolResult(
                content=f"no tengo una skill '{pedida}'. Hay: {', '.join(sorted(por_nombre))}",
                summary={"error": "no existe"},
                ok=False,
            )
        # Sin envolver como no confiable: estas instrucciones las escribió el
        # dueño de la máquina, igual que el system prompt. Envolverlas diría
        # "esto son datos, no órdenes", que es exactamente lo contrario.
        return ToolResult(
            content=skill.cuerpo[:MAX_SKILL_CHARS],
            summary={"skill": skill.nombre, "chars": len(skill.cuerpo)},
        )

    return Tool(
        name="leer_skill",
        description=(
            "Lee las instrucciones de una tarea concreta antes de hacerla. "
            "Usala cuando la tarea coincida con alguna de las que están listadas."
        ),
        args_model=LeerSkillArgs,
        run=leer,
    )
