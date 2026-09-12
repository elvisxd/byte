"""Navegar un proyecto: listar, leer y buscar dentro de archivos.

Es lo que separa a un asistente que habla de código de uno que lo mira. Sin
esto, el modelo solo sabe lo que el usuario le pegue en el mensaje; con esto
puede recorrer un repo, abrir el archivo que importa y citar la línea.

**Todo cuelga de una raíz, y salirse es imposible.** No es una precaución
teórica: el modelo elige las rutas a partir de lo que leyó, y lo que leyó puede
venir de un README con instrucciones metidas adentro (ASI01). Sin confinamiento,
`../../.ssh/id_rsa` o un symlink plantado terminan en la respuesta. Se resuelve
con `Path.resolve()` —que sigue los symlinks— y se compara contra la raíz ya
resuelta: si el destino real queda afuera, no se lee, aunque la ruta escrita
parezca inocente.

Tres decisiones más, cada una por algo que rompe en la práctica:

- **Lo binario no se lee.** Un `.png` o un `.pdf` entra como basura al prompt,
  gasta contexto y no dice nada. Se detecta por bytes nulos, no por extensión:
  la extensión miente.
- **Los archivos se leen por trozos.** Uno de 3000 líneas no entra en el
  contexto, y recortarlo en silencio hace que el modelo razone sobre la mitad
  creyendo que la tiene entera. Se devuelve un rango con sus números de línea y
  se dice cuánto quedó afuera.
- **Los directorios ruidosos no se listan.** `.git`, `node_modules` y
  `.venv` tienen más archivos que el proyecto entero y ninguno es lo que el
  usuario quiere ver.
"""

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.archivos")

# Carpetas que nunca se recorren: su contenido es ruido y en `.git` hay objetos
# binarios que no le sirven a nadie.
IGNORADOS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".DS_Store",
        ".idea",
        ".vscode",
    }
)

# Cuántas entradas devuelve un listado como mucho.
MAX_ENTRADAS = 400
# Cuántas líneas devuelve una lectura por defecto.
LINEAS_POR_DEFECTO = 400
# Cuántas coincidencias devuelve una búsqueda.
MAX_COINCIDENCIAS = 60
# Un archivo más grande que esto no se lee entero ni se busca adentro.
MAX_BYTES = 2_000_000


class FueraDeLaRaiz(ValueError):
    """La ruta pedida cae afuera del directorio permitido."""


def resolver(raiz: Path, relativa: str) -> Path:
    """La ruta absoluta de `relativa` dentro de `raiz`, o falla.

    `resolve()` sigue los symlinks, así que un enlace que apunte afuera se
    detecta acá y no después de leerlo. La comparación es sobre las dos rutas ya
    resueltas: comparar texto dejaría pasar `..` y los enlaces.
    """
    if relativa.startswith(("/", "~")):
        raise FueraDeLaRaiz(f"la ruta tiene que ser relativa al proyecto, no '{relativa}'")
    destino = (raiz / relativa).resolve()
    if destino != raiz and raiz not in destino.parents:
        raise FueraDeLaRaiz(f"'{relativa}' queda fuera del proyecto")
    return destino


def _es_binario(ruta: Path) -> bool:
    """Un byte nulo en los primeros 8 KB. Es lo que usa `git` para decidir.

    Por contenido y no por extensión: un `.txt` puede ser binario y un archivo
    sin extensión puede ser código.
    """
    try:
        with ruta.open("rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return True


def _visible(nombre: str) -> bool:
    return nombre not in IGNORADOS and not nombre.startswith(".")


# --- list_files ---


class ListarArgs(BaseModel):
    path: str = Field(default="", description="Carpeta dentro del proyecto. Vacío = la raíz.")
    depth: int = Field(default=2, ge=1, le=4, description="Cuántos niveles bajar")


def _listar(raiz: Path, args: ListarArgs) -> ToolResult:
    try:
        base = resolver(raiz, args.path)
    except FueraDeLaRaiz as exc:
        return ToolResult(content=str(exc), summary={"error": str(exc)}, ok=False)
    if not base.is_dir():
        return ToolResult(
            content=f"'{args.path}' no es una carpeta",
            summary={"error": "no es una carpeta"},
            ok=False,
        )

    filas: list[str] = []
    cortado = False
    for actual, carpetas, archivos in os.walk(base):
        carpetas[:] = sorted(c for c in carpetas if _visible(c))
        aqui = Path(actual)
        nivel = len(aqui.relative_to(base).parts)
        if nivel >= args.depth:
            carpetas[:] = []
        for nombre in sorted(archivos):
            if not _visible(nombre):
                continue
            if len(filas) >= MAX_ENTRADAS:
                cortado = True
                break
            archivo = aqui / nombre
            try:
                tamaño = archivo.stat().st_size
            except OSError:
                continue
            filas.append(f"{archivo.relative_to(raiz)}  ({tamaño:,} B)")
        if cortado:
            break

    if not filas:
        cuerpo = "(no hay archivos visibles acá)"
    else:
        cuerpo = "\n".join(filas)
        if cortado:
            cuerpo += f"\n[...hay más de {MAX_ENTRADAS}: mirá una subcarpeta]"

    return ToolResult(
        content=wrap_untrusted(f"ARCHIVOS EN {args.path or '.'}", cuerpo, 20_000),
        summary={"archivos": len(filas), "path": args.path or "."},
    )


# --- read_file ---


class LeerArgs(BaseModel):
    path: str = Field(description="Archivo a leer, relativo a la raíz del proyecto")
    start_line: int = Field(default=1, ge=1, description="Primera línea (desde 1)")
    max_lines: int = Field(
        default=LINEAS_POR_DEFECTO, ge=1, le=2000, description="Cuántas líneas leer"
    )


def _leer(raiz: Path, args: LeerArgs, max_chars: int) -> ToolResult:
    try:
        archivo = resolver(raiz, args.path)
    except FueraDeLaRaiz as exc:
        return ToolResult(content=str(exc), summary={"error": str(exc)}, ok=False)
    if not archivo.is_file():
        return ToolResult(
            content=f"'{args.path}' no existe o no es un archivo",
            summary={"error": "no existe"},
            ok=False,
        )
    if archivo.stat().st_size > MAX_BYTES:
        return ToolResult(
            content=f"'{args.path}' pesa más de {MAX_BYTES // 1000} KB: leé un rango con grep",
            summary={"error": "demasiado grande"},
            ok=False,
        )
    if _es_binario(archivo):
        return ToolResult(
            content=f"'{args.path}' es binario: no hay texto para leer",
            summary={"error": "binario"},
            ok=False,
        )

    texto = archivo.read_text(encoding="utf-8", errors="replace")
    lineas = texto.splitlines()
    desde = args.start_line - 1
    trozo = lineas[desde : desde + args.max_lines]

    # Con número de línea: es lo que permite citar "archivos.py:42" y volver.
    ancho = len(str(desde + len(trozo)))
    cuerpo = "\n".join(f"{desde + i + 1:>{ancho}}  {linea}" for i, linea in enumerate(trozo))
    faltan = len(lineas) - (desde + len(trozo))
    if faltan > 0:
        cuerpo += f"\n[...quedan {faltan} líneas: seguí desde start_line={desde + len(trozo) + 1}]"

    return ToolResult(
        content=wrap_untrusted(f"ARCHIVO {args.path}", cuerpo, max_chars),
        summary={"path": args.path, "lineas": len(lineas), "mostradas": len(trozo)},
    )


# --- grep ---


class BuscarArgs(BaseModel):
    pattern: str = Field(description="Texto o expresión regular a buscar")
    path: str = Field(default="", description="Carpeta donde buscar. Vacío = todo el proyecto.")
    glob: str = Field(default="", description="Filtro de nombre, por ejemplo '*.py'")


def _buscar(raiz: Path, args: BuscarArgs) -> ToolResult:
    try:
        base = resolver(raiz, args.path)
    except FueraDeLaRaiz as exc:
        return ToolResult(content=str(exc), summary={"error": str(exc)}, ok=False)

    try:
        # Sin IGNORECASE por defecto: quien busca `Foo` rara vez quiere `foo`.
        patron = re.compile(args.pattern)
    except re.error as exc:
        return ToolResult(
            content=f"la expresión no compila: {exc}",
            summary={"error": "regex inválida"},
            ok=False,
        )

    filas: list[str] = []
    for actual, carpetas, archivos in os.walk(base):
        carpetas[:] = sorted(c for c in carpetas if _visible(c))
        aqui = Path(actual)
        for nombre in sorted(archivos):
            if not _visible(nombre):
                continue
            if args.glob and not Path(nombre).match(args.glob):
                continue
            archivo = aqui / nombre
            try:
                if archivo.stat().st_size > MAX_BYTES or _es_binario(archivo):
                    continue
                texto = archivo.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for numero, linea in enumerate(texto.splitlines(), 1):
                if patron.search(linea):
                    relativa = archivo.relative_to(raiz)
                    filas.append(f"{relativa}:{numero}: {linea.strip()[:200]}")
                    if len(filas) >= MAX_COINCIDENCIAS:
                        break
            if len(filas) >= MAX_COINCIDENCIAS:
                break
        if len(filas) >= MAX_COINCIDENCIAS:
            break

    if not filas:
        cuerpo = f"sin coincidencias para '{args.pattern}'"
    else:
        cuerpo = "\n".join(filas)
        if len(filas) >= MAX_COINCIDENCIAS:
            cuerpo += f"\n[...se cortó en {MAX_COINCIDENCIAS}: afiná el patrón]"

    return ToolResult(
        content=wrap_untrusted(f"COINCIDENCIAS DE {args.pattern}", cuerpo, 20_000),
        summary={"coincidencias": len(filas), "patron": args.pattern},
    )


# --- Armado ---


def build_file_tools(raiz: Path, max_chars: int) -> list[Tool]:
    """Las tres herramientas, atadas a una raíz de la que no se puede salir."""
    raiz = raiz.resolve()

    async def listar(args: BaseModel) -> ToolResult:
        return _listar(raiz, args)  # type: ignore[arg-type]

    async def leer(args: BaseModel) -> ToolResult:
        return _leer(raiz, args, max_chars)  # type: ignore[arg-type]

    async def buscar(args: BaseModel) -> ToolResult:
        return _buscar(raiz, args)  # type: ignore[arg-type]

    return [
        Tool(
            name="list_files",
            description=(
                "Lista los archivos del proyecto del usuario. Usala para saber qué hay "
                "antes de leer nada."
            ),
            args_model=ListarArgs,
            run=listar,
        ),
        Tool(
            name="read_file",
            description=(
                "Lee un archivo del proyecto, con números de línea. Usala cuando "
                "necesites ver el contenido real en vez de suponerlo."
            ),
            args_model=LeerArgs,
            run=leer,
        ),
        Tool(
            name="grep",
            description=(
                "Busca un texto o expresión regular en los archivos del proyecto y "
                "devuelve archivo:línea. Es la forma rápida de encontrar dónde está algo."
            ),
            args_model=BuscarArgs,
            run=buscar,
        ),
    ]
