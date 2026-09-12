"""Mantener el CV: agregar un certificado, una experiencia, un proyecto.

Byte ya sabía *leer* el CV. Esto le da lo que faltaba: cambiarlo cuando se lo
pedís —"agregá el certificado de Kubernetes"— y regenerar los PDF, que es la
parte que nadie hace a mano cada vez.

**El CV se edita en los HTML, no en el markdown.** `perfil/cv.md` es la fuente
legible, pero los PDF salen de `build/cv-{en,es}.html`, que están escritos a
mano y tienen el diseño. Editar solo el markdown dejaría el CV real sin cambiar
— que es exactamente el problema de las seis copias desincronizadas.

**Dos versiones, siempre.** El CV existe en inglés y español; agregar algo a una
sola las desincroniza, y la desincronización es el problema que esto viene a
resolver. Las herramientas piden los dos textos.

**Nada se publica solo.** Se editan los HTML y se regeneran los PDF locales; el
commit del portfolio lo hace el usuario. Publicar es una decisión.
"""

import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.cv")

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# Dónde insertar cada cosa. La clave es el ancla del HTML: el bloque nuevo va
# **antes** de ese marcador, que es el final de la sección.
SECCIONES = {
    "certificacion": {
        "en": ("CERTIFICATIONS", '<div class="cert">'),
        "es": ("CERTIFICACIONES", '<div class="cert">'),
    },
}


def _escapar(texto: str) -> str:
    """Escapa lo que el modelo escribe antes de meterlo en el HTML.

    Un `&` o un `<` en un texto —"R&D", "C# < Java"— rompe el documento o, peor,
    abre una etiqueta que no se cierra y se come el resto del CV. Se preservan
    las entidades que el CV ya usa (`&mdash;`, `&middot;`) porque son parte del
    estilo y escaparlas las mostraría literales.
    """
    partes = re.split(r"(&[a-z]+;)", texto)
    return "".join(
        parte if i % 2 else parte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for i, parte in enumerate(partes)
    )


class ArchivosDelCV:
    """Dónde vive el CV. Se resuelve una vez y se reusa."""

    def __init__(self, carpeta: Path) -> None:
        self.carpeta = carpeta
        self.html = {
            "en": carpeta / "build" / "cv-en.html",
            "es": carpeta / "build" / "cv-es.html",
        }
        self.pdf = {
            "en": carpeta / "Elvis-Pino-CV-en.pdf",
            "es": carpeta / "Elvis-Pino-CV-es.pdf",
        }

    def existe(self) -> bool:
        return all(h.is_file() for h in self.html.values())


# --- Leer ---


class VerArgs(BaseModel):
    seccion: str = Field(
        default="",
        description="Sección a ver: certifications, experience, projects, skills. Vacío = todo.",
    )
    idioma: str = Field(default="en", description="'en' o 'es'")


def _texto_plano(html: str) -> str:
    """El HTML sin etiquetas, para que el modelo lea el contenido y no el markup."""
    sin_estilo = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.S)
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"<[^>]+>", " ", sin_estilo)).strip()


def _ver(archivos: ArchivosDelCV, args: VerArgs, max_chars: int) -> ToolResult:
    idioma = args.idioma if args.idioma in archivos.html else "en"
    ruta = archivos.html[idioma]
    if not ruta.is_file():
        return ToolResult(content=f"no encuentro {ruta}", summary={"error": "falta"}, ok=False)

    texto = _texto_plano(ruta.read_text(encoding="utf-8"))
    if args.seccion:
        # Desde el título de la sección hasta el siguiente en mayúsculas.
        patron = re.compile(
            rf"{re.escape(args.seccion)}(.*?)(?=\n[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ]{{6,}}\n|$)",
            re.S | re.I,
        )
        encontrado = patron.search(texto)
        if encontrado:
            texto = args.seccion.upper() + encontrado.group(1)

    return ToolResult(
        content=wrap_untrusted(f"CV ({idioma})", texto, max_chars),
        summary={"idioma": idioma, "seccion": args.seccion or "todo"},
    )


# --- Agregar una certificación ---


class CertificacionArgs(BaseModel):
    nombre: str = Field(description="Nombre del certificado, por ejemplo 'Kubernetes CKA'")
    emisor: str = Field(description="Quién lo emite: Udemy, Coursera, Anthropic…")
    anio: str = Field(description="Año, por ejemplo '2026'")
    credencial: str = Field(default="", description="Id de la credencial, si lo hay")


def _agregar_certificacion(archivos: ArchivosDelCV, args: CertificacionArgs) -> ToolResult:
    """Agrega la certificación a las dos versiones, con el markup que ya usan.

    Se copia la estructura del último bloque existente en vez de escribir HTML
    nuevo: así hereda las clases y el estilo sin que haya que conocerlos, y si
    el diseño cambia, esto sigue funcionando.
    """
    if not archivos.existe():
        return ToolResult(
            content="no encuentro los HTML del CV", summary={"error": "falta"}, ok=False
        )

    credencial = f" · <span class='cred'>{args.credencial}</span>" if args.credencial else ""
    tocados = []
    for idioma, ruta in archivos.html.items():
        html = ruta.read_text(encoding="utf-8")
        # El último bloque de certificación es el molde.
        bloques = list(re.finditer(r'<div class="cert">.*?</div>', html, re.S))
        if not bloques:
            return ToolResult(
                content=f"no encuentro la sección de certificaciones en {ruta.name}",
                summary={"error": "sin ancla"},
                ok=False,
            )
        ultimo = bloques[-1]
        nuevo = (
            f'<div class="cert"><strong>{args.nombre}</strong> — {args.emisor}, '
            f"{args.anio}{credencial}</div>"
        )
        if args.nombre in html:
            return ToolResult(
                content=f"'{args.nombre}' ya está en el CV ({idioma})",
                summary={"ya_estaba": True},
                ok=False,
            )
        html = html[: ultimo.end()] + "\n  " + nuevo + html[ultimo.end() :]
        ruta.write_text(html, encoding="utf-8")
        tocados.append(idioma)

    logger.info("cv_certificacion_agregada", nombre=args.nombre, idiomas=tocados)
    return ToolResult(
        content=(
            f"Agregué '{args.nombre}' ({args.emisor}, {args.anio}) a las dos versiones del CV.\n"
            "Los PDF todavía no se regeneraron: usá regenerar_cv cuando termines de editar."
        ),
        summary={"nombre": args.nombre, "idiomas": tocados},
    )


# --- Regenerar los PDF ---


class RegenerarArgs(BaseModel):
    pass


def _regenerar(archivos: ArchivosDelCV, portfolio: Path | None) -> ToolResult:
    """Imprime los HTML a PDF con Chrome y los copia al portfolio.

    El mismo Chrome con el que se imprimían a mano, así que el resultado es el
    de siempre. `--no-pdf-header-footer` saca la fecha y la URL que Chrome pone
    en los márgenes por defecto.
    """
    if not CHROME.is_file():
        return ToolResult(
            content="no encuentro Chrome: los PDF hay que imprimirlos a mano",
            summary={"error": "sin chrome"},
            ok=False,
        )

    lineas = []
    for idioma, html in archivos.html.items():
        destino = archivos.pdf[idioma]
        subprocess.run(  # noqa: S603 - rutas resueltas, no entrada del modelo
            [
                str(CHROME),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={destino}",
                f"file://{html}",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        # Menos de 10 KB no tiene ni la foto ni el contenido: falló aunque
        # Chrome haya devuelto 0.
        if not destino.is_file() or destino.stat().st_size < 10_000:
            return ToolResult(
                content=f"{destino.name} no se generó bien",
                summary={"error": "pdf vacío"},
                ok=False,
            )
        lineas.append(f"{destino.name} ({destino.stat().st_size:,} bytes)")
        if portfolio and (portfolio / "public").is_dir():
            nombre = "Elvis-Pino-CV.pdf" if idioma == "en" else "Elvis-Pino-CV-es.pdf"
            shutil.copy2(destino, portfolio / "public" / nombre)
            lineas.append(f"  → portfolio/public/{nombre}")

    cierre = ""
    if portfolio and (portfolio / ".git").is_dir():
        cierre = (
            "\n\nEl portfolio quedó con los PDF nuevos sin commitear — publicar es tu decisión:"
            f"\n  cd {portfolio} && git add public && git commit"
        )
    return ToolResult(content="PDF regenerados:\n" + "\n".join(lineas) + cierre, summary={"pdf": 2})


# --- Agregar una experiencia ---


class ExperienciaArgs(BaseModel):
    puesto_en: str = Field(description="Puesto en inglés, ej 'Senior Backend Engineer'")
    puesto_es: str = Field(description="Puesto en español, ej 'Ingeniero Backend Senior'")
    empresa: str = Field(description="Nombre de la empresa")
    periodo: str = Field(description="Período, ej '2026 — Present' o '2024 — 2026'")
    lugar: str = Field(default="Remote", description="Lugar: Remote, Orlando FL…")
    logros_en: list[str] = Field(description="Logros en inglés, uno por bullet")
    logros_es: list[str] = Field(description="Los mismos logros en español")
    stack: str = Field(default="", description="Tecnologías separadas por · ")


def _agregar_experiencia(archivos: ArchivosDelCV, args: ExperienciaArgs) -> ToolResult:
    """Agrega un puesto al principio de Experience, que es donde va el más nuevo.

    Los CV se leen de arriba hacia abajo y el puesto actual es lo que importa:
    agregarlo al final lo escondería detrás de trabajos de hace ocho años.
    """
    if not archivos.existe():
        return ToolResult(
            content="no encuentro los HTML del CV", summary={"error": "falta"}, ok=False
        )
    if len(args.logros_en) != len(args.logros_es):
        return ToolResult(
            content=(
                f"me diste {len(args.logros_en)} logros en inglés y {len(args.logros_es)} en "
                "español: tienen que ser los mismos para que las dos versiones digan lo mismo"
            ),
            summary={"error": "desbalanceado"},
            ok=False,
        )

    textos = {"en": (args.puesto_en, args.logros_en), "es": (args.puesto_es, args.logros_es)}
    for idioma, ruta in archivos.html.items():
        html = ruta.read_text(encoding="utf-8")
        puesto, logros = textos[idioma]
        if f">{args.empresa}<" in html:
            return ToolResult(
                content=f"ya hay una experiencia en '{args.empresa}' en el CV",
                summary={"ya_estaba": True},
                ok=False,
            )
        # El primer .item de Experience: el puesto nuevo va justo antes.
        ancla = re.search(r'(<div class="sec-h">Experien\w+</div>\s*)', html)
        if not ancla:
            return ToolResult(
                content=f"no encuentro la sección de experiencia en {ruta.name}",
                summary={"error": "sin ancla"},
                ok=False,
            )
        bullets = "\n        ".join(f"<li>{_escapar(x)}</li>" for x in logros)
        stack = f'\n      <div class="stack">{_escapar(args.stack)}</div>' if args.stack else ""
        bloque = (
            f'\n  <div class="item">\n'
            f'    <div class="rail"><span class="yr">{_escapar(args.periodo)}</span>'
            f'<span class="loc">{_escapar(args.lugar)}</span></div>\n'
            f"    <div>\n"
            f'      <div class="j-title">{_escapar(puesto)}</div>\n'
            f'      <div class="j-co">{_escapar(args.empresa)}</div>\n'
            f"      <ul>\n        {bullets}\n      </ul>{stack}\n"
            f"    </div>\n  </div>\n"
        )
        html = html[: ancla.end()] + bloque + html[ancla.end() :]
        ruta.write_text(html, encoding="utf-8")

    logger.info("cv_experiencia_agregada", empresa=args.empresa)
    return ToolResult(
        content=(
            f"Agregué '{args.puesto_en}' en {args.empresa} ({args.periodo}) arriba de todo en "
            "Experience, en las dos versiones.\nUsá regenerar_cv para que salga en los PDF."
        ),
        summary={"empresa": args.empresa, "logros": len(args.logros_en)},
    )


# --- Agregar un proyecto ---


class ProyectoArgs(BaseModel):
    nombre: str = Field(description="Nombre del proyecto")
    descripcion_en: str = Field(description="Qué es y qué prueba, en inglés. Una o dos frases.")
    descripcion_es: str = Field(description="Lo mismo en español")
    stack: str = Field(default="", description="Tecnologías separadas por · ")
    etiqueta: str = Field(default="", description="Etiqueta: 'IN PROGRESS', 'APP STORE'…")
    enlace: str = Field(default="", description="URL o dominio, si lo hay")


def _agregar_proyecto(archivos: ArchivosDelCV, args: ProyectoArgs) -> ToolResult:
    """Agrega un proyecto a Selected Projects, arriba (lo más reciente primero)."""
    if not archivos.existe():
        return ToolResult(
            content="no encuentro los HTML del CV", summary={"error": "falta"}, ok=False
        )

    textos = {"en": args.descripcion_en, "es": args.descripcion_es}
    for idioma, ruta in archivos.html.items():
        html = ruta.read_text(encoding="utf-8")
        if f">{args.nombre}" in html or f">{args.nombre} " in html:
            return ToolResult(
                content=f"'{args.nombre}' ya está en el CV", summary={"ya_estaba": True}, ok=False
            )
        ancla = re.search(
            r'(<div class="sec-h">(?:Selected Projects|Proyectos[^<]*)</div>\s*)', html
        )
        if not ancla:
            return ToolResult(
                content=f"no encuentro la sección de proyectos en {ruta.name}",
                summary={"error": "sin ancla"},
                ok=False,
            )
        etiqueta = f' <span class="tag">{_escapar(args.etiqueta)}</span>' if args.etiqueta else ""
        pie = " &middot; ".join(x for x in (_escapar(args.enlace), _escapar(args.stack)) if x)
        bloque = (
            f'\n  <div class="proj">\n'
            f'    <div class="p-name">{_escapar(args.nombre)}{etiqueta}</div>\n'
            f'    <div class="p-body">{_escapar(textos[idioma])}</div>\n'
            f'    <div class="p-link">{pie}</div>\n'
            f"  </div>\n"
        )
        html = html[: ancla.end()] + bloque + html[ancla.end() :]
        ruta.write_text(html, encoding="utf-8")

    logger.info("cv_proyecto_agregado", nombre=args.nombre)
    return ToolResult(
        content=(
            f"Agregué '{args.nombre}' arriba de Selected Projects, en las dos versiones.\n"
            "Usá regenerar_cv para que salga en los PDF."
        ),
        summary={"nombre": args.nombre},
    )


# --- Reemplazar un texto cualquiera ---


class ReemplazarArgs(BaseModel):
    viejo: str = Field(description="El texto exacto a reemplazar, como aparece en el CV")
    nuevo_en: str = Field(description="El texto nuevo en inglés")
    nuevo_es: str = Field(
        default="", description="El texto nuevo en español. Vacío: usa el inglés."
    )


def _reemplazar(archivos: ArchivosDelCV, args: ReemplazarArgs) -> ToolResult:
    """Cambia un texto del CV, con respaldo antes de tocar nada.

    Es la herramienta más peligrosa de las tres: el modelo elige qué reemplazar
    y podría partir una etiqueta HTML por la mitad. Tres defensas:

    - **Respaldo antes de escribir**, para poder volver.
    - **El texto viejo tiene que aparecer exactamente una vez.** Dos veces y no
      se sabe cuál se quiso cambiar; cero veces y el modelo se lo imaginó.
    - **No puede contener `<` ni `>`.** Reemplazar markup es cómo se rompe el
      diseño, y lo que se quiere cambiar acá es texto visible.
    """
    if not archivos.existe():
        return ToolResult(
            content="no encuentro los HTML del CV", summary={"error": "falta"}, ok=False
        )
    if "<" in args.viejo or ">" in args.viejo:
        return ToolResult(
            content="no reemplazo markup: pasame solo el texto visible que querés cambiar",
            summary={"error": "markup"},
            ok=False,
        )

    nuevos = {"en": args.nuevo_en, "es": args.nuevo_es or args.nuevo_en}
    # Primero se verifica en las dos, después se escribe: si una falla, ninguna
    # se toca y el CV no queda a medio cambiar.
    for idioma, ruta in archivos.html.items():
        veces = ruta.read_text(encoding="utf-8").count(args.viejo)
        if veces == 0:
            return ToolResult(
                content=f"no encuentro ese texto en la versión {idioma}. Usá ver_cv para mirarlo.",
                summary={"error": "no está"},
                ok=False,
            )
        if veces > 1:
            return ToolResult(
                content=(
                    f"ese texto aparece {veces} veces en la versión {idioma}: dame un fragmento "
                    "más largo para saber cuál cambiar"
                ),
                summary={"error": "ambiguo"},
                ok=False,
            )

    for idioma, ruta in archivos.html.items():
        html = ruta.read_text(encoding="utf-8")
        respaldo = ruta.with_suffix(".html.bak")
        respaldo.write_text(html, encoding="utf-8")
        ruta.write_text(html.replace(args.viejo, _escapar(nuevos[idioma])), encoding="utf-8")

    logger.info("cv_texto_reemplazado", largo=len(args.viejo))
    return ToolResult(
        content=(
            "Cambié el texto en las dos versiones. Los originales quedaron en "
            "`build/cv-*.html.bak` por si hay que volver.\nUsá regenerar_cv para los PDF."
        ),
        summary={"reemplazado": True},
    )


# --- Armado ---


def build_cv_tools(carpeta: Path, max_chars: int, portfolio: Path | None = None) -> list[Tool]:
    """Las herramientas del CV, atadas a una carpeta concreta."""
    archivos = ArchivosDelCV(carpeta.expanduser())

    async def ver(args: BaseModel) -> ToolResult:
        return _ver(archivos, args, max_chars)  # type: ignore[arg-type]

    async def certificacion(args: BaseModel) -> ToolResult:
        return _agregar_certificacion(archivos, args)  # type: ignore[arg-type]

    async def experiencia(args: BaseModel) -> ToolResult:
        return _agregar_experiencia(archivos, args)  # type: ignore[arg-type]

    async def proyecto(args: BaseModel) -> ToolResult:
        return _agregar_proyecto(archivos, args)  # type: ignore[arg-type]

    async def reemplazar(args: BaseModel) -> ToolResult:
        return _reemplazar(archivos, args)  # type: ignore[arg-type]

    async def regenerar(_args: BaseModel) -> ToolResult:
        return _regenerar(archivos, portfolio)

    return [
        Tool(
            name="ver_cv",
            description=(
                "Lee el CV del usuario, entero o una sección (certifications, experience, "
                "projects, skills). Usala antes de proponer cambios: el CV real puede no "
                "coincidir con lo que se recuerde de la conversación."
            ),
            args_model=VerArgs,
            run=ver,
        ),
        Tool(
            name="agregar_certificacion",
            description=(
                "Agrega una certificación al CV, en inglés y español a la vez. Después hay "
                "que llamar a regenerar_cv para que salga en los PDF."
            ),
            args_model=CertificacionArgs,
            run=certificacion,
        ),
        Tool(
            name="agregar_experiencia",
            description=(
                "Agrega un puesto de trabajo al CV, arriba de todo en Experience (lo más "
                "reciente primero), en inglés y español. Pedile al usuario los logros "
                "concretos: un puesto sin logros no dice nada."
            ),
            args_model=ExperienciaArgs,
            run=experiencia,
        ),
        Tool(
            name="agregar_proyecto",
            description=(
                "Agrega un proyecto a Selected Projects del CV, en inglés y español. La "
                "descripción tiene que decir qué prueba el proyecto, no solo qué hace."
            ),
            args_model=ProyectoArgs,
            run=proyecto,
        ),
        Tool(
            name="reemplazar_en_cv",
            description=(
                "Cambia un texto del CV en las dos versiones. Usala para corregir o "
                "reescribir algo que ya está. Mirá antes con ver_cv: el texto viejo tiene "
                "que ser exacto y aparecer una sola vez."
            ),
            args_model=ReemplazarArgs,
            run=reemplazar,
        ),
        Tool(
            name="regenerar_cv",
            description=(
                "Regenera los PDF del CV desde los HTML y los copia al portfolio. Usala "
                "después de editar el CV. No publica nada: el commit lo hace el usuario."
            ),
            args_model=RegenerarArgs,
            run=regenerar,
        ),
    ]
