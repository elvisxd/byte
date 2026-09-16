"""El cazador: trae, puntúa, recuerda y avisa. Un cron, no un endpoint.

    uv run python -m empleo.cazador            # una vuelta y avisa
    uv run python -m empleo.cazador --probar   # qué devuelve cada fuente, sin avisar
    uv run python -m empleo.cazador --sin-avisar

**Lo que no hace, y no va a hacer: postular.** Junta links y los ordena. Abrir
el link, leer la oferta y decidir si va tu tiempo ahí es tuyo — igual que el CV,
que Byte edita pero no publica. En Upwork esto no es una preferencia de diseño:
el auto-envío de propuestas se paga con suspensión permanente de la cuenta.
"""

import argparse
import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx

from api.logging import get_logger
from empleo import fuentes
from empleo.aviso import avisar
from empleo.criterio import Criterio, Puntaje, cargar_criterio, puntuar
from empleo.memoria import Memoria
from empleo.oferta import Oferta

logger = get_logger("empleo.cazador")

RAIZ = Path(__file__).resolve().parent.parent
CRITERIO_POR_DEFECTO = RAIZ / "perfil" / "busqueda.toml"


def carpeta_de_trabajo() -> Path:
    """Dónde van el digest y la memoria.

    Fuera del repo por defecto: son datos que cambian cada día y links de
    puestos ajenos, no código. Que no ensucien el `git status` es parte de que
    esto se pueda dejar corriendo en un cron sin pensarlo más.
    """
    return Path(os.environ.get("BYTE_EMPLEO_DIR", Path.home() / ".byte" / "empleo"))


async def recolectar(
    criterio: Criterio, consulta_upwork: str
) -> tuple[list[Oferta], dict[str, int]]:
    """Todas las fuentes encendidas, en paralelo. Devuelve las ofertas y el conteo.

    El conteo importa tanto como las ofertas: "hoy no llegó nada" y "hoy falló
    RemoteOK" se ven igual desde el teléfono, y son problemas distintos.
    """
    activas: dict[str, Callable[[httpx.AsyncClient], Awaitable[list[Oferta]]]] = {}
    if criterio.fuentes.get("remoteok", True):
        activas["remoteok"] = fuentes.remoteok
    if criterio.fuentes.get("remotive", True):
        activas["remotive"] = fuentes.remotive
    if criterio.fuentes.get("weworkremotely", True):
        activas["weworkremotely"] = fuentes.weworkremotely
    if criterio.fuentes.get("hackernews", True):
        activas["hackernews"] = fuentes.hackernews
    if criterio.fuentes.get("upwork", False):
        token = os.environ.get("UPWORK_TOKEN", "")
        activas["upwork"] = lambda c: fuentes.upwork(c, token, consulta_upwork)

    async with fuentes.cliente_http() as cliente:
        resultados = await asyncio.gather(
            *(adaptador(cliente) for adaptador in activas.values()),
            return_exceptions=True,
        )

    ofertas: list[Oferta] = []
    conteo: dict[str, int] = {}
    for nombre, resultado in zip(activas, resultados, strict=True):
        if isinstance(resultado, BaseException):
            logger.warning("fuente_excepcion", fuente=nombre, error_type=type(resultado).__name__)
            conteo[nombre] = -1
            continue
        conteo[nombre] = len(resultado)
        ofertas.extend(resultado)
    return ofertas, conteo


def seleccionar(
    ofertas: list[Oferta], criterio: Criterio, memoria: Memoria
) -> list[tuple[Oferta, Puntaje]]:
    """Puntúa, saca las repetidas y ordena de mejor a peor.

    La deduplicación mira la clave de la fuente **y** la huella empresa+puesto:
    la misma búsqueda publicada en dos boards es una oportunidad, no dos.
    """
    vistas_en_esta_vuelta: set[str] = set()
    seleccion: list[tuple[Oferta, Puntaje]] = []
    for oferta in ofertas:
        if memoria.conoce(oferta.clave, oferta.huella):
            continue
        if oferta.huella in vistas_en_esta_vuelta:
            continue
        vistas_en_esta_vuelta.add(oferta.huella)
        seleccion.append((oferta, puntuar(oferta, criterio)))
    seleccion.sort(key=lambda par: par[1].total, reverse=True)
    return seleccion


def _linea(oferta: Oferta, puntaje: Puntaje) -> str:
    empresa = f" — {oferta.empresa}" if oferta.empresa else ""
    senales = f"  [{', '.join(puntaje.senales)}]" if puntaje.senales else ""
    lugar = f"\n  {oferta.ubicacion}" if oferta.ubicacion else ""
    return f"{puntaje.total:>4}  {oferta.titulo}{empresa}{senales}{lugar}\n  {oferta.url}"


def armar_aviso(
    seleccion: list[tuple[Oferta, Puntaje]], criterio: Criterio, conteo: dict[str, int]
) -> str:
    """El mensaje que llega al teléfono."""
    dignas = [par for par in seleccion if par[1].total >= criterio.puntaje_minimo]
    cabecera = f"Ofertas — {datetime.now().strftime('%d/%m %H:%M')}"
    if not dignas:
        revisadas = sum(n for n in conteo.values() if n > 0)
        return (
            f"{cabecera}\nNada sobre {criterio.puntaje_minimo} puntos "
            f"entre {revisadas} ofertas nuevas.\n{_pie_fuentes(conteo)}"
        )

    cuerpo = "\n\n".join(_linea(o, p) for o, p in dignas[: criterio.tope_por_aviso])
    resto = len(dignas) - criterio.tope_por_aviso
    extra = f"\n\n(+{resto} más en el digest)" if resto > 0 else ""
    return f"{cabecera} — {len(dignas)} nuevas\n\n{cuerpo}{extra}\n\n{_pie_fuentes(conteo)}"


def _pie_fuentes(conteo: dict[str, int]) -> str:
    partes = [f"{n}: {'error' if c < 0 else c}" for n, c in sorted(conteo.items())]
    return "fuentes → " + " · ".join(partes)


def escribir_digest(
    carpeta: Path, seleccion: list[tuple[Oferta, Puntaje]], conteo: dict[str, int]
) -> Path:
    """Todo lo de la vuelta, también lo que no llegó al teléfono.

    Es donde se ve si el criterio quedó demasiado duro: si semana tras semana el
    digest tiene cosas buenas que el aviso no mandó, el que está mal es el
    puntaje mínimo, no el feed.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{datetime.now().strftime('%Y-%m-%d-%H%M')}.md"
    lineas = [
        f"# Ofertas — {datetime.now().isoformat(timespec='minutes')}",
        "",
        _pie_fuentes(conteo),
        "",
    ]
    for oferta, puntaje in seleccion:
        lineas += [
            f"## {puntaje.total} · {oferta.titulo}"
            + (f" — {oferta.empresa}" if oferta.empresa else ""),
            f"- {oferta.url}",
            f"- fuente: {oferta.fuente}" + (f" · {oferta.ubicacion}" if oferta.ubicacion else ""),
            f"- por qué: {'; '.join(puntaje.motivos) or 'nada que sume'}",
            "",
        ]
    destino.write_text("\n".join(lineas), encoding="utf-8")
    return destino


async def una_vuelta(
    criterio: Criterio, carpeta: Path, consulta_upwork: str, con_aviso: bool
) -> str:
    ofertas, conteo = await recolectar(criterio, consulta_upwork)
    memoria = Memoria(carpeta / "vistas.json")
    seleccion = seleccionar(ofertas, criterio, memoria)
    destino = escribir_digest(carpeta, seleccion, conteo)
    texto = armar_aviso(seleccion, criterio, conteo)

    if con_aviso:
        # Se anota **después** de avisar y solo lo que se avisó: si el panel está
        # caído, estas ofertas tienen que volver a aparecer en la próxima vuelta.
        if avisar(texto):
            for oferta, puntaje in seleccion:
                if puntaje.total >= criterio.puntaje_minimo:
                    memoria.anotar(oferta.clave, oferta.huella)
            memoria.guardar()
    logger.info("vuelta_terminada", nuevas=len(seleccion), digest=str(destino), **conteo)
    return texto


async def probar(criterio: Criterio, consulta_upwork: str) -> str:
    """Qué devuelve cada fuente, sin deduplicar ni avisar.

    Existe porque los feeds cambian de forma sin avisar y porque el adaptador de
    Upwork se escribió contra la documentación, no contra el servidor: cuando la
    key esté aprobada, esto dice en una corrida si la respuesta llega como se
    esperaba.
    """
    ofertas, conteo = await recolectar(criterio, consulta_upwork)
    lineas = [_pie_fuentes(conteo), ""]
    por_fuente: dict[str, Oferta] = {}
    for oferta in ofertas:
        por_fuente.setdefault(oferta.fuente, oferta)
    for nombre, muestra in sorted(por_fuente.items()):
        puntaje = puntuar(muestra, criterio)
        lineas += [
            f"[{nombre}] {muestra.titulo} — {muestra.empresa}",
            f"  url: {muestra.url}",
            f"  ubicación: {muestra.ubicacion or '(vacía)'}",
            f"  descripción: {len(muestra.descripcion)} caracteres",
            f"  puntaje: {puntaje.total} · {'; '.join(puntaje.motivos) or 'nada'}",
            "",
        ]
    return "\n".join(lineas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trae ofertas de trabajo y avisa por Telegram.")
    parser.add_argument(
        "--probar", action="store_true", help="Muestra qué devuelve cada fuente y no avisa"
    )
    parser.add_argument(
        "--sin-avisar", action="store_true", help="Corre entero pero no manda el mensaje"
    )
    parser.add_argument(
        "--minimo", type=int, default=None, help="Puntaje mínimo para avisar (pisa el del TOML)"
    )
    parser.add_argument("--criterio", type=Path, default=CRITERIO_POR_DEFECTO)
    parser.add_argument(
        "--consulta-upwork",
        default="AI agent LangGraph RAG Next.js",
        help="Qué buscar en Upwork, si esa fuente está encendida",
    )
    args = parser.parse_args()

    criterio = cargar_criterio(args.criterio)
    if args.minimo is not None:
        criterio = replace(criterio, puntaje_minimo=args.minimo)

    if args.probar:
        print(asyncio.run(probar(criterio, args.consulta_upwork)))
        return
    print(
        asyncio.run(
            una_vuelta(
                criterio, carpeta_de_trabajo(), args.consulta_upwork, con_aviso=not args.sin_avisar
            )
        )
    )


if __name__ == "__main__":
    main()
