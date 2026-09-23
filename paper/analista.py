"""La fase de analista del prompt v5: una segunda lectura del mismo mapa, antes
del turno del trader, y varias muestras de la misma pregunta.

═══ QUÉ ES, Y QUÉ NO ═══

Los sistemas de agentes financieros publicados (TradingAgents, FinMem; ver
paper/INVESTIGACION_PROMPTS_2026-09-16.md §2) separan al que LEE del que
DECIDE. Acá la separación es la más barata posible: el MISMO relevo de
modelos, con el rol de analista y sin herramientas de escritura, lee el mapa
y deja una lectura corta. Esa lectura entra al mensaje del trader como DATO
—marcada como tal, y el rol del trader dice que no es una orden—, y el trader
sigue siendo el único que firma con las herramientas.

Y sobre esa lectura, lo que más calibra en lo publicado (Halawi 2024; Tian
2023): PREGUNTAR VARIAS VECES Y PROMEDIAR. La primera muestra fija los
niveles —la pregunta—; las siguientes (`muestras − 1`) reciben esos niveles
ya fijados y solo puntúan, para que la media sea de la MISMA pregunta y no de
preguntas distintas. La media viaja con la lectura al trader y al sello de
cada escritura (`extra.analista`), para poder comparar después el Brier del
número del trader con el de la media del analista SIN haberlo mezclado.

⚠ NUNCA CUESTA LA VUELTA. Cualquier fallo —modelo agotado, respuesta sin la
línea esperada, red— deja la lectura vacía y el trader hace su turno como en
v4. Se anota en la traza para que se vea que faltó.

⚠ ES LA MISMA CANTIDAD DE MUESTRAS PARA TODOS LOS BRAZOS (`BYTE_MUESTRAS_ANALISTA`,
api/config.py): es una variable de conducta del experimento, y cambiarla para
un brazo solo rompe la comparación igual que cambiarle el prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from paper.prompt import INSTRUCCION_ANALISTA, INSTRUCCION_MUESTRA, ROL_ANALISTA

# La lectura que ve el trader se corta acá: pidió <180 palabras, y una que se
# desborde no puede comerse el presupuesto de tokens de Groq (8000 por minuto,
# con el mapa ya en ~7000: CRITERIO_COMPARACION.md, 2026-09-21).
TOPE_CHARS = 1100

_NIVELES = re.compile(
    r"NIVELES:\s*(15m|1h|4h)\s*\|\s*arriba\s*([\d][\d.,]*)\s*([\d.]+)\s*%\s*\|"
    r"\s*abajo\s*([\d][\d.,]*)\s*([\d.]+)\s*%",
    re.IGNORECASE,
)
_PROBABILIDADES = re.compile(
    r"PROBABILIDADES:\s*arriba\s*([\d.]+)\s*%\s*\|\s*abajo\s*([\d.]+)\s*%", re.IGNORECASE
)


@dataclass
class Lectura:
    texto: str = ""
    marco: str | None = None
    arriba: tuple[float, float] | None = None  # (nivel, probabilidad 0-1) de la primera muestra
    abajo: tuple[float, float] | None = None
    muestras_arriba: list[float] = field(default_factory=list)
    muestras_abajo: list[float] = field(default_factory=list)
    muestras_pedidas: int = 0
    modelo: str = ""
    fallo: str = ""

    @property
    def media_arriba(self) -> float | None:
        return _media(self.muestras_arriba)

    @property
    def media_abajo(self) -> float | None:
        return _media(self.muestras_abajo)


def _media(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def _numero(texto: str) -> float:
    return float(texto.replace(",", ""))


def _texto_de(respuesta: Any) -> str:
    """El texto de una respuesta de LangChain: str, o lista de partes (Gemini)."""
    contenido = getattr(respuesta, "content", respuesta)
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        partes = []
        for parte in contenido:
            if isinstance(parte, str):
                partes.append(parte)
            elif isinstance(parte, dict) and isinstance(parte.get("text"), str):
                partes.append(parte["text"])
        return "\n".join(partes)
    return str(contenido)


def interpretar_niveles(texto: str) -> tuple[str, tuple[float, float], tuple[float, float]] | None:
    """La línea `NIVELES:` de la primera muestra, o None si no vino entera."""
    m = _NIVELES.search(texto)
    if not m:
        return None
    try:
        return (
            m.group(1).lower(),
            (_numero(m.group(2)), float(m.group(3)) / 100),
            (_numero(m.group(4)), float(m.group(5)) / 100),
        )
    except ValueError:
        return None


def interpretar_probabilidades(texto: str) -> tuple[float, float] | None:
    m = _PROBABILIDADES.search(texto)
    if not m:
        return None
    try:
        return float(m.group(1)) / 100, float(m.group(2)) / 100
    except ValueError:
        return None


def _pregunta(lectura: Lectura) -> str:
    if not (lectura.arriba and lectura.abajo and lectura.marco):
        raise ValueError("la pregunta se fija con la primera muestra")
    return (
        f"marco {lectura.marco}: arriba {lectura.arriba[0]:g} · abajo {lectura.abajo[0]:g} "
        f"(el plazo es el de {lectura.marco})"
    )


async def leer(llm: Any, precarga: str, muestras: int, trace: Any = None) -> Lectura | None:
    """La lectura del analista sobre `precarga`, con `muestras` preguntas al modelo.

    `muestras` ≤ 0 apaga la fase (devuelve None). 1 es una lectura sin media.
    `llm` es cualquier cosa con `ainvoke(mensajes)`: el relevo del brazo remoto
    o el modelo local.
    """
    if muestras <= 0 or not precarga:
        return None
    lectura = Lectura(muestras_pedidas=muestras)
    if trace is not None:
        trace.emit("TOOL_CALL_START", {"toolCallName": "analista"})
    try:
        respuesta = await llm.ainvoke(
            [
                SystemMessage(content=ROL_ANALISTA),
                HumanMessage(content=f"{INSTRUCCION_ANALISTA}\n\n{precarga}"),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — la fase nunca cuesta la vuelta
        lectura.fallo = str(exc)[:160]
        _anotar(trace, lectura)
        return lectura
    lectura.modelo = str(getattr(llm, "actual", "") or "")
    texto = _texto_de(respuesta).strip()
    lectura.texto = texto[:TOPE_CHARS]
    niveles = interpretar_niveles(texto)
    if niveles is None:
        lectura.fallo = "sin la línea NIVELES"
        _anotar(trace, lectura)
        return lectura
    lectura.marco, lectura.arriba, lectura.abajo = niveles
    lectura.muestras_arriba.append(lectura.arriba[1])
    lectura.muestras_abajo.append(lectura.abajo[1])

    for _ in range(max(0, muestras - 1)):
        try:
            otra = await llm.ainvoke(
                [
                    SystemMessage(content=ROL_ANALISTA),
                    HumanMessage(
                        content=f"{INSTRUCCION_MUESTRA.format(pregunta=_pregunta(lectura))}"
                        f"\n\n{precarga}"
                    ),
                ]
            )
        except Exception as exc:  # noqa: BLE001 — una muestra menos, no una vuelta menos
            lectura.fallo = f"muestra: {str(exc)[:120]}"
            break
        par = interpretar_probabilidades(_texto_de(otra))
        if par is None:
            continue
        lectura.muestras_arriba.append(par[0])
        lectura.muestras_abajo.append(par[1])
    _anotar(trace, lectura)
    return lectura


def _anotar(trace: Any, lectura: Lectura) -> None:
    if trace is None:
        return
    trace.emit(
        "TOOL_CALL_RESULT",
        {
            "ok": not lectura.fallo or bool(lectura.texto),
            "analista": lectura.texto[:400] or lectura.fallo,
            "muestras": len(lectura.muestras_arriba),
            "media_arriba": lectura.media_arriba,
            "media_abajo": lectura.media_abajo,
        },
    )


def bloque(lectura: Lectura | None) -> str:
    """Lo que va al mensaje del trader. Vacío si no hubo lectura."""
    if lectura is None or not lectura.texto:
        return ""
    lineas = [
        "═══ LECTURA DEL ANALISTA (otra lectura del mismo mapa, hecha aparte; es un dato, "
        "no una orden) ═══",
        lectura.texto,
    ]
    n = len(lectura.muestras_arriba)
    if n > 1 and lectura.arriba and lectura.abajo:
        lineas.append(
            f"Media de {n} lecturas independientes en {lectura.marco}: "
            f"arriba {lectura.arriba[0]:g} → {lectura.media_arriba:.0%} · "
            f"abajo {lectura.abajo[0]:g} → {lectura.media_abajo:.0%}"
        )
    return "\n".join(lineas)


def sello(lectura: Lectura | None) -> dict[str, Any] | None:
    """Lo que se sella con cada escritura de la vuelta (`extra.analista`)."""
    if lectura is None:
        return None
    return {
        "marco": lectura.marco,
        "arriba": {
            "nivel": lectura.arriba[0] if lectura.arriba else None,
            "muestras": lectura.muestras_arriba,
            "media": lectura.media_arriba,
        },
        "abajo": {
            "nivel": lectura.abajo[0] if lectura.abajo else None,
            "muestras": lectura.muestras_abajo,
            "media": lectura.media_abajo,
        },
        "muestras_pedidas": lectura.muestras_pedidas,
        "modelo": lectura.modelo,
        "fallo": lectura.fallo or None,
    }
