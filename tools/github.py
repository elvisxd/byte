"""GitHub a través de `gh`, el CLI oficial ya autenticado en la máquina.

Por `gh` y no por la API con un token propio: la sesión ya existe, vive en el
llavero del sistema y no hay que guardar otra credencial en un `.env` que
después alguien publica por error.

**Lo que puede hacer está acotado a propósito.** Listar repos, leerlos, y
escribir descripciones y topics. No borra, no cambia visibilidad, no hace push
ni toca issues. La razón no es técnica —el token tiene `delete_repo` y `repo`,
podría hacer todo eso— sino que un modelo que se equivoca listando archivos
cuesta un turno perdido, y uno que se equivoca borrando un repo cuesta el repo.

Escribir sí pasa por la aprobación humana del modo seguro: una descripción mala
en un perfil público es visible para cualquiera que te busque.
"""

import json
import shutil
import subprocess

from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.github")

TIMEOUT_S = 30
# Cuántos repos se listan como mucho. Más que esto no se lee de un vistazo y
# llena el contexto con nombres.
MAX_REPOS = 40


class GhNoDisponible(RuntimeError):
    """`gh` no está instalado o no tiene sesión."""


def _gh(*args: str) -> str:
    """Corre `gh` y devuelve su salida. Los argumentos son fijos o validados.

    Nunca se arma una línea de comando por concatenación: los valores van como
    elementos de la lista, así que un nombre de repo con un `;` adentro es un
    nombre raro, no un comando.
    """
    binario = shutil.which("gh")
    if not binario:
        raise GhNoDisponible("`gh` no está instalado: brew install gh")
    proceso = subprocess.run(  # noqa: S603 - argumentos por lista, nunca por shell
        [binario, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    if proceso.returncode != 0:
        error = (proceso.stderr or "").strip()
        if "auth" in error.lower() or "logged in" in error.lower():
            raise GhNoDisponible("`gh` no tiene sesión: corré `gh auth login`")
        raise RuntimeError(error[:300] or "gh falló sin decir por qué")
    return proceso.stdout


# --- Listar repos ---


class ListarReposArgs(BaseModel):
    incluir_privados: bool = Field(
        default=True, description="Si incluir los repos privados además de los públicos"
    )


def _listar_repos(args: ListarReposArgs) -> ToolResult:
    """Los repos con lo que se ve desde afuera: nombre, si es público, y qué dice."""
    crudo = _gh(
        "repo",
        "list",
        "--limit",
        str(MAX_REPOS),
        "--json",
        "name,isPrivate,description,primaryLanguage,updatedAt,stargazerCount",
    )
    repos = json.loads(crudo or "[]")
    if not args.incluir_privados:
        repos = [r for r in repos if not r.get("isPrivate")]
    repos.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)

    filas = []
    sin_descripcion = 0
    for r in repos:
        visible = "privado" if r.get("isPrivate") else "PÚBLICO"
        lenguaje = (r.get("primaryLanguage") or {}).get("name") or "—"
        descripcion = r.get("description") or ""
        if not descripcion:
            sin_descripcion += 1
            descripcion = "(SIN DESCRIPCIÓN)"
        filas.append(
            f"{visible:8} {r['name'][:28]:28} {lenguaje[:12]:12} "
            f"{r.get('updatedAt', '')[:7]}  {descripcion[:60]}"
        )

    cuerpo = "\n".join(filas) or "(no hay repos)"
    if sin_descripcion:
        cuerpo += (
            f"\n\n{sin_descripcion} sin descripción: es la línea que GitHub muestra al buscarte."
        )
    return ToolResult(
        content=wrap_untrusted("REPOS DE GITHUB", cuerpo, 20_000),
        summary={"repos": len(repos), "sin_descripcion": sin_descripcion},
    )


# --- Describir un repo ---


class DescribirArgs(BaseModel):
    repo: str = Field(description="Nombre del repo, sin el usuario. Por ejemplo 'byte'")
    descripcion: str = Field(
        description="La descripción nueva. Una línea: es lo que se ve en el listado."
    )


def _describir(args: DescribirArgs) -> ToolResult:
    """Cambia la descripción de un repo. Es lo primero que ve quien te busca."""
    if "/" in args.repo:
        return ToolResult(
            content="pasame solo el nombre del repo, sin el usuario",
            summary={"error": "nombre inválido"},
            ok=False,
        )
    if len(args.descripcion) > 350:
        return ToolResult(
            content="GitHub corta las descripciones largas: usá una línea",
            summary={"error": "demasiado larga"},
            ok=False,
        )
    _gh("repo", "edit", args.repo, "--description", args.descripcion)
    logger.info("github_descripcion", repo=args.repo)
    return ToolResult(
        content=f"Listo: '{args.repo}' ahora dice «{args.descripcion}».",
        summary={"repo": args.repo},
    )


# --- Topics ---


class TopicsArgs(BaseModel):
    repo: str = Field(description="Nombre del repo, sin el usuario")
    topics: list[str] = Field(
        description="Las etiquetas, en minúsculas y con guiones: ai-agent, rag, nextjs"
    )


def _topics(args: TopicsArgs) -> ToolResult:
    """Los topics son por lo que un reclutador filtra al buscar perfiles."""
    if "/" in args.repo:
        return ToolResult(
            content="solo el nombre del repo", summary={"error": "inválido"}, ok=False
        )
    limpios = [t.strip().lower() for t in args.topics if t.strip()][:20]
    if not limpios:
        return ToolResult(content="no me diste ningún topic", summary={"error": "vacío"}, ok=False)

    usuario = _gh("api", "user", "--jq", ".login").strip()
    # Cada topic como su propio `-f names[]=…`: es la forma que `gh api` acepta
    # sin pasar por stdin, que `_gh` no maneja.
    campos: list[str] = []
    for t in limpios:
        campos += ["-f", f"names[]={t}"]
    _gh("api", "-X", "PUT", f"repos/{usuario}/{args.repo}/topics", *campos)
    logger.info("github_topics", repo=args.repo, topics=limpios)
    return ToolResult(
        content=f"'{args.repo}' quedó con: {', '.join(limpios)}",
        summary={"repo": args.repo, "topics": len(limpios)},
    )


# --- Armado ---


def build_github_tools() -> list[Tool]:
    """Las herramientas de GitHub. Solo se registran si `gh` tiene sesión."""

    async def listar(args: BaseModel) -> ToolResult:
        return _listar_repos(args)  # type: ignore[arg-type]

    async def describir(args: BaseModel) -> ToolResult:
        return _describir(args)  # type: ignore[arg-type]

    async def topics(args: BaseModel) -> ToolResult:
        return _topics(args)  # type: ignore[arg-type]

    return [
        Tool(
            name="listar_repos",
            description=(
                "Lista los repositorios de GitHub del usuario con su descripción, lenguaje y "
                "si son públicos. Usala antes de proponer cambios al perfil."
            ),
            args_model=ListarReposArgs,
            run=listar,
        ),
        Tool(
            name="describir_repo",
            description=(
                "Cambia la descripción de un repositorio: la línea que GitHub muestra en el "
                "listado y en las búsquedas. Proponé el texto y esperá la aprobación."
            ),
            args_model=DescribirArgs,
            run=describir,
        ),
        Tool(
            name="poner_topics",
            description=(
                "Define los topics de un repositorio, que son las etiquetas por las que se "
                "filtra al buscar proyectos. Reemplaza los que hubiera."
            ),
            args_model=TopicsArgs,
            run=topics,
        ),
    ]
