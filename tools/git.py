"""Git: ver qué cambió, commitear y subir — con el diff a la vista antes.

**El orden importa: primero se muestra, después se pregunta.** `git_estado` es
de solo lectura y corre sin permiso, así que el modelo puede mirar el diff y
proponer un mensaje de commit con fundamento. `git_commit` y `git_push` pasan
por la aprobación humana, que muestra exactamente qué archivos entran y con qué
mensaje.

**Commit y push son dos pasos separados** y no uno solo con una bandera. Un
commit que sale mal se deshace con `git reset --soft HEAD~1` y nadie se entera;
un push a un repo público ya lo tiene GitHub, y deshacerlo es reescribir un
historial que cualquiera pudo haber clonado. Que sean dos decisiones distintas
es lo que permite arrepentirse en el medio.

Nada de `--force`, ni `reset`, ni `rebase`, ni cambiar de rama: un modelo que se
equivoca commiteando cuesta un `reset`, uno que se equivoca con un `push
--force` cuesta el trabajo de alguien más.
"""

import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.git")

TIMEOUT_S = 60
# Cuánto diff se le muestra al modelo. Más que esto no entra en el contexto y
# tampoco se lee de un vistazo al aprobar.
MAX_DIFF_CHARS = 12_000


class GitNoDisponible(RuntimeError):
    """No hay `git`, o la carpeta no es un repositorio."""


def _git(raiz: Path, *args: str) -> str:
    """Corre git en la raíz del proyecto. Argumentos por lista, nunca por shell."""
    binario = shutil.which("git")
    if not binario:
        raise GitNoDisponible("git no está instalado")
    proceso = subprocess.run(  # noqa: S603 - argumentos por lista, sin shell
        [binario, "-C", str(raiz), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    if proceso.returncode != 0:
        error = (proceso.stderr or proceso.stdout or "").strip()
        raise RuntimeError(error[:400] or "git falló sin decir por qué")
    return proceso.stdout


# --- Ver qué cambió ---


class EstadoArgs(BaseModel):
    con_diff: bool = Field(
        default=True, description="Si incluir el diff completo además de la lista de archivos"
    )


def _estado(raiz: Path, args: EstadoArgs) -> ToolResult:
    """Qué cambió, sin tocar nada. Es lo que permite proponer un commit con
    fundamento en vez de un mensaje genérico."""
    rama = _git(raiz, "rev-parse", "--abbrev-ref", "HEAD").strip()
    estado = _git(raiz, "status", "--short").rstrip()
    if not estado:
        return ToolResult(
            content=f"No hay nada para commitear. Estás en '{rama}'.",
            summary={"rama": rama, "archivos": 0},
        )

    partes = [f"Rama: {rama}", "", "Archivos:", estado]
    if args.con_diff:
        # `HEAD` incluye lo que ya está en el índice y lo que no: al aprobar hay
        # que ver todo lo que entraría, no solo lo que alguien ya agregó.
        diff = _git(raiz, "diff", "HEAD").rstrip()
        if diff:
            partes += ["", "Cambios:", diff[:MAX_DIFF_CHARS]]
            if len(diff) > MAX_DIFF_CHARS:
                partes.append(f"[...recortado, {len(diff) - MAX_DIFF_CHARS} caracteres más]")

    return ToolResult(
        content=wrap_untrusted("GIT", "\n".join(partes), MAX_DIFF_CHARS + 4000),
        summary={"rama": rama, "archivos": len(estado.splitlines())},
    )


# --- Commit ---


class CommitArgs(BaseModel):
    mensaje: str = Field(
        description=(
            "El mensaje del commit. Primera línea corta diciendo qué cambia; si hace "
            "falta, una línea en blanco y el porqué."
        )
    )


def _commit(raiz: Path, args: CommitArgs) -> ToolResult:
    """Commitea todo lo que haya cambiado, en la rama actual."""
    mensaje = args.mensaje.strip()
    if not mensaje:
        return ToolResult(content="el mensaje está vacío", summary={"error": "vacío"}, ok=False)

    rama = _git(raiz, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if not _git(raiz, "status", "--short").strip():
        return ToolResult(
            content="no hay cambios para commitear", summary={"error": "limpio"}, ok=False
        )

    _git(raiz, "add", "-A")
    _git(raiz, "commit", "-m", mensaje)
    corto = _git(raiz, "rev-parse", "--short", "HEAD").strip()
    resumen = _git(raiz, "show", "--stat", "--format=", "HEAD").rstrip()

    logger.info("git_commit", rama=rama, commit=corto)
    return ToolResult(
        content=(
            f"Commiteado en '{rama}': {corto}\n{resumen}\n\n"
            "Todavía no está en GitHub. Si algo salió mal, `git reset --soft HEAD~1` lo deshace."
        ),
        summary={"commit": corto, "rama": rama},
    )


# --- Push ---


class PushArgs(BaseModel):
    pass


def _push(raiz: Path) -> ToolResult:
    """Sube la rama actual. Sin `--force`, sin elegir rama: la que esté."""
    rama = _git(raiz, "rev-parse", "--abbrev-ref", "HEAD").strip()
    pendientes = _git(raiz, "log", f"origin/{rama}..{rama}", "--oneline").strip()
    if not pendientes:
        return ToolResult(
            content=f"'{rama}' ya está al día con GitHub", summary={"error": "al día"}, ok=False
        )

    _git(raiz, "push", "origin", rama)
    logger.info("git_push", rama=rama, commits=len(pendientes.splitlines()))
    return ToolResult(
        content=f"Subido a GitHub: {len(pendientes.splitlines())} commit(s) en '{rama}'.",
        summary={"rama": rama, "commits": len(pendientes.splitlines())},
    )


# --- Armado ---


def build_git_tools(raiz: Path) -> list[Tool]:
    """Las herramientas de git, atadas a la raíz del proyecto."""
    raiz = raiz.expanduser().resolve()

    async def estado(args: BaseModel) -> ToolResult:
        return _estado(raiz, args)  # type: ignore[arg-type]

    async def commit(args: BaseModel) -> ToolResult:
        return _commit(raiz, args)  # type: ignore[arg-type]

    async def push(_args: BaseModel) -> ToolResult:
        return _push(raiz)

    return [
        Tool(
            name="git_estado",
            description=(
                "Muestra qué cambió en el proyecto: la rama, los archivos modificados y el "
                "diff. Usala ANTES de proponer un commit, para que el mensaje diga lo que "
                "de verdad cambió."
            ),
            args_model=EstadoArgs,
            run=estado,
        ),
        Tool(
            name="git_commit",
            description=(
                "Commitea todos los cambios en la rama actual. Mirá primero con git_estado "
                "y proponé un mensaje que explique qué cambia. No sube nada a GitHub."
            ),
            args_model=CommitArgs,
            run=commit,
        ),
        Tool(
            name="git_push",
            description=(
                "Sube a GitHub los commits de la rama actual. Es el paso que hace públicos "
                "los cambios: preguntá antes de usarla, aunque el commit ya esté hecho."
            ),
            args_model=PushArgs,
            run=push,
        ),
    ]
