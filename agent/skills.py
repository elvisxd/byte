"""Skills: instrucciones por tarea que el agente carga cuando le sirven.

El formato es el que usan otras herramientas de agente —una carpeta con su
`SKILL.md` y un frontmatter con `name` y `description`— así que una skill
escrita para otra se puede copiar tal cual.

**Solo entran las descripciones, no el contenido.** Meter todas enteras al
prompt lo llenaría con instrucciones sobre tareas que no se están haciendo; lo
que el agente ve es un índice de qué hay y para qué sirve, y abre la que
necesita con `leer_skill`.

Dicho lo cual, hay que ser honesto sobre el alcance. Las skills de Claude Code
tienen cientos de líneas de matiz y funcionan porque el modelo que las lee
sostiene ese detalle. Con un 8B local eso no se traslada —ya se midió que ignora
instrucciones más simples del propio system prompt—, así que lo que rinde son
reglas concretas y contables.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from api.logging import get_logger

logger = get_logger("agent.skills")

# Cuánto de una skill se le pasa al modelo. Más que esto no lo va a seguir y
# desplaza el resto de la conversación.
MAX_SKILL_CHARS = 6000


@dataclass(frozen=True, slots=True)
class Skill:
    nombre: str
    descripcion: str
    cuerpo: str


def _frontmatter(texto: str) -> tuple[dict[str, str], str]:
    """Separa el frontmatter YAML del cuerpo. Sin dependencias: son dos claves."""
    encontrado = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", texto, re.S)
    if not encontrado:
        return {}, texto
    campos: dict[str, str] = {}
    for linea in encontrado.group(1).splitlines():
        clave, sep, valor = linea.partition(":")
        if sep:
            campos[clave.strip()] = valor.strip().strip("\"'")
    return campos, encontrado.group(2)


def cargar(carpeta: str) -> list[Skill]:
    """Las skills de una carpeta: `<carpeta>/<nombre>/SKILL.md`.

    Una skill sin `description` se descarta: es lo que el agente lee para
    decidir si la necesita, y sin eso nunca la abriría.
    """
    if not carpeta:
        return []
    raiz = Path(carpeta).expanduser()
    if not raiz.is_dir():
        logger.warning("skills_sin_carpeta", carpeta=str(raiz))
        return []

    skills: list[Skill] = []
    for archivo in sorted(raiz.glob("*/SKILL.md")):
        try:
            texto = archivo.read_text(encoding="utf-8")
        except OSError:
            continue
        campos, cuerpo = _frontmatter(texto)
        nombre = campos.get("name") or archivo.parent.name
        descripcion = campos.get("description", "").strip()
        if not descripcion:
            logger.warning("skill_sin_descripcion", skill=nombre)
            continue
        skills.append(Skill(nombre=nombre, descripcion=descripcion, cuerpo=cuerpo.strip()))

    if skills:
        logger.info("skills_cargadas", cuantas=len(skills), nombres=[s.nombre for s in skills])
    return skills


def indice(skills: list[Skill]) -> str:
    """El índice que va al prompt: qué hay y para qué sirve cada una."""
    if not skills:
        return ""
    lineas = [f"- {s.nombre}: {s.descripcion}" for s in skills]
    return (
        "Instrucciones disponibles para tareas concretas. Si vas a hacer una de "
        "estas, leela primero con `leer_skill`:\n" + "\n".join(lineas)
    )
