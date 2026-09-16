"""Qué tan cerca está una oferta de lo que buscás, y por qué.

**El puntaje lo calcula el código, nunca el modelo.** Es la misma regla que
gobierna `paper/`: el modelo sirve para explicar y redactar, no para decir un
número. Un 7B pidiéndole "puntuá esta oferta del 1 al 100" devuelve algo
plausible y distinto cada vez; acá cada punto tiene una línea que lo justifica y
se puede discutir mirando el archivo `perfil/busqueda.toml`.

**Ninguna señal descarta sola.** Una oferta que dice "US only" o "no visa
sponsorship" baja de puesto, pero aparece con la señal a la vista. Filtrar en
silencio es cómo un criterio equivocado se vuelve invisible: no verías las
ofertas que te estás perdiendo, verías menos ofertas y nada más.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from empleo.oferta import Oferta

# Lo que se busca en el texto de la oferta. Cada patrón se probó contra la forma
# en que estas frases aparecen de verdad, no contra la forma "correcta": nadie
# escribe "visa sponsorship is not available", escriben "we can't sponsor".
SENALES: dict[str, re.Pattern[str]] = {
    "latam": re.compile(
        r"\b(lat(?:in)?[ -]?am(?:erica)?n?|south america|central america|the americas"
        r"|argentina|colombia|mexico|m[eé]xico|brazil|brasil|venezuela|chile|per[uú]"
        r"|uruguay|ecuador|costa rica)\b"
    ),
    "remoto_global": re.compile(
        r"\b(anywhere in the world|work from anywhere|worldwide|globally distributed"
        r"|fully distributed|any time ?zone|100% remote, anywhere)\b"
    ),
    "reubicacion": re.compile(
        r"\b(relocation (package|assistance|support|bonus|provided|offered)"
        r"|we (will )?(help|cover|pay).{0,24}relocat\w*|relocation and visa|visa and relocation)"
    ),
    "contractor": re.compile(
        r"\b(contractor|c2c|corp[ -]to[ -]corp|employer of record|eor|1099|deel"
        r"|remote\.com|independent contractor)\b"
    ),
    "freelance": re.compile(r"\b(freelance|per[ -]project|hourly rate|short[ -]term contract)\b"),
    "junior": re.compile(
        r"\b(junior|jr\.?|entry[ -]level|intern(ship)?|new grad|graduate program"
        r"|0[ -–-]2 years|1[ -–-]2 years)\b"
    ),
    # El orden importa: `sin_patrocinio` se evalúa antes que `patrocinio`, porque
    # "we do not offer visa sponsorship" contiene la frase positiva adentro.
    "sin_patrocinio": re.compile(
        r"\b((can ?not|cannot|can't|do not|don't|unable to|not able to|won't|will not)"
        r"\s+(currently\s+)?(provide|offer|support)?\s*(visa\s+)?sponsor\w*"
        r"|no (visa )?sponsorship|without sponsorship|sponsorship is not)"
    ),
    "patrocinio": re.compile(
        r"\b(visa sponsorship|we sponsor|sponsorship (is )?(available|provided|offered)"
        r"|h-?1b|green card|work permit)\b"
    ),
    "solo_us": re.compile(
        r"\b(u\.?s\.?a?[ -]only|united states only|us[ -]based (only|candidates)"
        r"|must (be |reside |live ).{0,30}(united states|u\.?s\.?a?\b)"
        r"|authorized to work in the (us|u\.?s\.?a?|united states))\b"
    ),
}


@dataclass(frozen=True, slots=True)
class Criterio:
    """Lo que dice `perfil/busqueda.toml`, ya validado."""

    stack: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pesos_stack: dict[str, int] = field(default_factory=dict)
    preferencias: dict[str, int] = field(default_factory=dict)
    penalizaciones: dict[str, int] = field(default_factory=dict)
    necesita_patrocinio: bool = False
    fuentes: dict[str, bool] = field(default_factory=dict)
    tope_por_aviso: int = 8
    puntaje_minimo: int = 25
    # Cuánto puede aportar el stack como máximo. Sin tope, una oferta que lista
    # treinta tecnologías en un párrafo de "nice to have" le gana a una que pide
    # exactamente lo que hacés.
    tope_stack: int = 60


# Cuánto vale encontrar un término de cada grupo.
PESOS = {"fuerte": 12, "medio": 6, "leve": 2}

DEFECTOS_PREFERENCIAS = {
    "latam": 30,
    "remoto_global": 20,
    "reubicacion": 15,
    "contractor": 12,
    "freelance": 5,
}
DEFECTOS_PENALIZACIONES = {"junior": 40, "sin_patrocinio": 25, "solo_us": 0}


def cargar_criterio(ruta: Path) -> Criterio:
    """Lee el TOML. Si no está, devuelve el criterio por defecto.

    Que falte el archivo no puede ser un error: el cazador tiene que poder correr
    recién clonado el repo y que el usuario lo ajuste después de ver el primer
    aviso, no antes.
    """
    crudo: dict = {}
    if ruta.is_file():
        try:
            crudo = tomllib.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            crudo = {}

    stack_crudo = crudo.get("stack") or {}
    stack = {
        grupo: tuple(str(t).lower() for t in (stack_crudo.get(grupo) or [])) for grupo in PESOS
    }
    preferencias = {**DEFECTOS_PREFERENCIAS, **_enteros(crudo.get("preferencias"))}
    penalizaciones = {**DEFECTOS_PENALIZACIONES, **_enteros(crudo.get("penalizaciones"))}
    aviso = crudo.get("aviso") or {}
    return Criterio(
        stack=stack,
        pesos_stack=dict(PESOS),
        preferencias=preferencias,
        penalizaciones=penalizaciones,
        necesita_patrocinio=bool((crudo.get("situacion") or {}).get("necesita_patrocinio", False)),
        fuentes={k: bool(v) for k, v in (crudo.get("fuentes") or {}).items()},
        tope_por_aviso=int(aviso.get("tope_por_aviso", 8)),
        puntaje_minimo=int(aviso.get("puntaje_minimo", 25)),
    )


def _enteros(seccion: object) -> dict[str, int]:
    if not isinstance(seccion, dict):
        return {}
    salida: dict[str, int] = {}
    for clave, valor in seccion.items():
        try:
            salida[str(clave)] = int(valor)
        except (TypeError, ValueError):
            continue
    return salida


def _patron(termino: str) -> re.Pattern[str]:
    """Un término del stack, como expresión que no pesque pedazos de palabra.

    Los términos con puntuación —`.net`, `c#`, `next.js`— no llevan borde por la
    izquierda a propósito: con él, `.net` no encontraría "ASP.NET", que es donde
    aparece la mitad de las veces.
    """
    escapado = re.escape(termino)
    izquierda = r"(?<![a-z0-9])" if termino[0].isalnum() else ""
    return re.compile(rf"{izquierda}{escapado}(?![a-z0-9])")


_CACHE_PATRONES: dict[str, re.Pattern[str]] = {}


def patron_de(termino: str) -> re.Pattern[str]:
    if termino not in _CACHE_PATRONES:
        _CACHE_PATRONES[termino] = _patron(termino)
    return _CACHE_PATRONES[termino]


@dataclass(frozen=True, slots=True)
class Puntaje:
    """El número y las líneas que lo explican."""

    total: int
    motivos: tuple[str, ...]
    senales: tuple[str, ...]
    terminos: tuple[str, ...]


def detectar_senales(oferta: Oferta) -> tuple[str, ...]:
    """Qué señales trae el texto. `sin_patrocinio` anula a `patrocinio`."""
    texto = oferta.buscable()
    encontradas = [nombre for nombre, patron in SENALES.items() if patron.search(texto)]
    if "sin_patrocinio" in encontradas and "patrocinio" in encontradas:
        encontradas.remove("patrocinio")
    # Upwork es freelance por definición: el texto de la oferta no tiene por qué
    # decirlo y perderíamos la señal.
    if oferta.fuente == "upwork" and "freelance" not in encontradas:
        encontradas.append("freelance")
    return tuple(encontradas)


def puntuar(oferta: Oferta, criterio: Criterio) -> Puntaje:
    """El puntaje de una oferta, con el detalle de de dónde salió cada punto."""
    texto = oferta.buscable()
    motivos: list[str] = []

    del_stack = 0
    encontrados: list[str] = []
    for grupo, peso in criterio.pesos_stack.items():
        for termino in criterio.stack.get(grupo, ()):
            if patron_de(termino).search(texto):
                del_stack += peso
                encontrados.append(termino)
    if del_stack > criterio.tope_stack:
        motivos.append(f"+{criterio.tope_stack} stack (tope; sumaba {del_stack})")
        del_stack = criterio.tope_stack
    elif del_stack:
        motivos.append(f"+{del_stack} stack: {', '.join(encontrados[:8])}")

    total = del_stack
    senales = detectar_senales(oferta)

    for senal in senales:
        puntos = criterio.preferencias.get(senal, 0)
        if puntos:
            total += puntos
            motivos.append(f"+{puntos} {senal}")

    for senal in senales:
        castigo = criterio.penalizaciones.get(senal, 0)
        # "no patrocina" solo importa si hoy necesitás que alguien patrocine.
        # Para alguien que ya puede trabajar donde está, es una línea de más en
        # el aviso, no un problema.
        if senal == "sin_patrocinio" and not criterio.necesita_patrocinio:
            continue
        if castigo:
            total -= castigo
            motivos.append(f"-{castigo} {senal}")

    return Puntaje(
        total=total,
        motivos=tuple(motivos),
        senales=senales,
        terminos=tuple(encontrados),
    )
