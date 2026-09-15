"""Una lista de modelos que se turnan cuando uno se agota.

═══ POR QUÉ EXISTE ═══

El brazo remoto de la comparación (paper/CRITERIO_COMPARACION.md) usa la capa
gratuita de la API de Gemini, que tiene topes por minuto y por día que Google
no publica por modelo y que cambian. Medido el 2026-09-15: `gemini-3.8-flash`
contestó a una sonda y devolvió 503 «high demand» a la siguiente. Un brazo
atado a UN modelo se quedaría parado a media mañana sin que nadie lo viera.

Así que el brazo es una LISTA ordenada. Ante un 429 o un 5xx se pasa al
siguiente en la MISMA llamada —la vuelta no se pierde— y el que falló queda en
cuarentena: un minuto si el tope era por minuto, hasta la medianoche del
Pacífico si era por día (ahí se renuevan las cuotas), dos minutos si se cayó.

⚠ EL MODELO QUE CONTESTÓ QUEDA EN `actual`, Y ESO NO ES UN ADORNO. Las
herramientas sellan cada operación con la etiqueta del modelo, y esa etiqueta
lee `actual`: si el 3.8 se agotó y el 3.5-lite abrió la operación de la tarde,
el registro dice 3.5-lite. Decir «el brazo Gemini» mezclaría dos modelos de
calidad distinta bajo un nombre — ver el criterio.

⚠ ENTRE LLAMADAS SE ESPERA. Una vuelta son hasta seis llamadas seguidas en
segundos, y la capa gratuita tiene tope por minuto: sin espaciarlas se rotaría
de modelo por un tope que no dice nada del modelo.

═══ QUÉ NO HACE ═══

No reintenta a mitad de una respuesta. Si un modelo ya emitió texto o una
llamada a herramienta y falla, el error sube: cambiar de modelo en ese punto
pegaría media respuesta de uno con media de otro, y eso no es una respuesta de
nadie.

⚠ EL PENSAMIENTO NO CUENTA COMO RESPUESTA. Medido el 2026-09-15 en la primera
vuelta de prueba: `gemini-3.7-flash` emitió su pensamiento entero y el
servicio devolvió 503 justo antes del texto. Con la regla de arriba a secas,
la vuelta se perdía por un trozo que no forma parte de la respuesta —el
pensamiento no entra al historial—. Así que se descarta y se pide al
siguiente; la traza enseña los dos pensamientos, que es lo que pasó.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

CUARENTENA_MINUTO_S = 60.0
CUARENTENA_CAIDO_S = 120.0
# Las cuotas diarias de la API de Gemini se renuevan a medianoche del Pacífico.
PACIFICO = ZoneInfo("America/Los_Angeles")


class RelevoAgotado(RuntimeError):
    """Todos los modelos de la lista están en cuarentena."""


def tipo_de_agotamiento(exc: BaseException) -> str | None:
    """`minuto`, `dia` o `caido` si el error es de cuota o de servicio; None si es otra cosa.

    Un error de argumentos o de red no es agotamiento y no se tapa cambiando
    de modelo: subiría igual con el siguiente.
    """
    # Google pone el HTTP en `code`; el SDK de OpenAI (Groq) en `status_code`.
    codigo = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    texto = str(exc)
    if codigo == 429 or "RateLimit" in type(exc).__name__ or "RESOURCE_EXHAUSTED" in texto:
        bajo = texto.lower()
        return "dia" if ("perday" in bajo or "per day" in bajo or "daily" in bajo) else "minuto"
    if codigo in (500, 502, 503, 504) or "UNAVAILABLE" in texto or "high demand" in texto.lower():
        return "caido"
    # ⚠ UN CORTE DE TRANSPORTE TAMBIÉN ES UNA CAÍDA. Medido el 2026-09-15 a las
    # 08:11, primera vuelta real del brazo: 3.8 dio 503, y 3.7 «Server
    # disconnected without sending a response» —sin código HTTP, porque no
    # hubo respuesta—. No era de cuota ni de argumentos, así que subió y la
    # vuelta se perdió. Un servidor que corta antes de contestar es, para
    # quien espera, lo mismo que un 503.
    nombre = type(exc).__name__
    bajo = texto.lower()
    if (
        "Connect" in nombre
        or "Timeout" in nombre
        or "Protocol" in nombre
        or "disconnected" in bajo
        or "connection" in bajo
        or "timed out" in bajo
    ):
        return "caido"
    return None


# «Please try again in 1h23m45.6s» (Groq) y «'retryDelay': '23s'» (Google): el
# servicio dice cuánto esperar, y es mejor dato que cualquier constante.
_TRY_AGAIN = re.compile(r"try again in\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.I)
_RETRY_DELAY = re.compile(r"retryDelay'?\"?\s*:\s*'?\"?([\d.]+)s")


def espera_sugerida(exc: BaseException) -> float | None:
    """Segundos que el propio error pide esperar, si los dice. None si no."""
    texto = str(exc)
    m = _TRY_AGAIN.search(texto)
    if m and any(m.groups()):
        h, mi, s = m.groups()
        return float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)
    m = _RETRY_DELAY.search(texto)
    if m:
        return float(m.group(1))
    return None


def segundos_hasta_medianoche_pacifico(ahora: datetime | None = None) -> float:
    momento = (ahora or datetime.now(tz=PACIFICO)).astimezone(PACIFICO)
    manana = (momento + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Un margen: la renovación no es al segundo.
    return (manana - momento).total_seconds() + 60


def _es_respuesta(trozo: Any) -> bool:
    """¿Este trozo ya es parte de la respuesta —texto o llamada— o solo pensamiento?"""
    if getattr(trozo, "tool_call_chunks", None) or getattr(trozo, "tool_calls", None):
        return True
    contenido = getattr(trozo, "content", trozo)
    if isinstance(contenido, str):
        return bool(contenido)
    if isinstance(contenido, list):
        return any(
            (isinstance(b, str) and b)
            or (isinstance(b, dict) and b.get("type") == "text" and b.get("text"))
            for b in contenido
        )
    return False


class _Estado:
    """Lo que comparten un relevo y sus copias con herramientas ligadas."""

    def __init__(self) -> None:
        self.actual: str | None = None
        self.hasta: dict[str, float] = {}
        self.ultima_llamada = float("-inf")


class Relevo:
    """Se usa como un chat model de LangChain: `bind_tools`, `astream`, `ainvoke`."""

    def __init__(
        self,
        modelos: list[tuple[str, Any]],
        *,
        espera_s: float = 12.0,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
        ahora: Callable[[], datetime] | None = None,
        _estado: _Estado | None = None,
    ) -> None:
        if not modelos:
            raise ValueError("un relevo necesita al menos un modelo")
        self.modelos = modelos
        self.espera_s = espera_s
        self.reloj = reloj
        self.dormir = dormir
        self.ahora = ahora
        self._estado = _estado or _Estado()

    @property
    def actual(self) -> str:
        """El último modelo que contestó; antes de la primera llamada, el primero."""
        return self._estado.actual or self.modelos[0][0]

    @property
    def nombres(self) -> list[str]:
        return [n for n, _ in self.modelos]

    def bind_tools(self, *args: Any, **kwargs: Any) -> "Relevo":
        # La copia con herramientas comparte estado con la original: el grafo usa
        # la ligada para el agente y la pelada para resumir, y las dos tienen
        # que ver las mismas cuarentenas y el mismo `actual`.
        return Relevo(
            [(n, m.bind_tools(*args, **kwargs)) for n, m in self.modelos],
            espera_s=self.espera_s,
            reloj=self.reloj,
            dormir=self.dormir,
            ahora=self.ahora,
            _estado=self._estado,
        )

    def _disponibles(self) -> list[tuple[str, Any]]:
        t = self.reloj()
        return [(n, m) for n, m in self.modelos if self._estado.hasta.get(n, float("-inf")) <= t]

    async def _espaciar(self) -> None:
        falta = self._estado.ultima_llamada + self.espera_s - self.reloj()
        if falta > 0:
            await self.dormir(falta)
        self._estado.ultima_llamada = self.reloj()

    def _agotar(self, nombre: str, tipo: str, exc: BaseException) -> None:
        sugerida = espera_sugerida(exc)
        if sugerida is not None and tipo != "caido":
            # Un margen: la renovación no es al segundo.
            cuarentena = sugerida + 5.0
        elif tipo == "dia":
            cuarentena = segundos_hasta_medianoche_pacifico(self.ahora() if self.ahora else None)
        elif tipo == "minuto":
            cuarentena = CUARENTENA_MINUTO_S
        else:
            cuarentena = CUARENTENA_CAIDO_S
        self._estado.hasta[nombre] = self.reloj() + cuarentena
        print(
            f"[relevo] {nombre} agotado ({tipo}): vuelve en {cuarentena / 60:.0f} min · "
            f"{str(exc)[:80]}",
            flush=True,
        )

    def _contesto(self, nombre: str) -> None:
        if self._estado.actual != nombre:
            print(f"[relevo] contesta {nombre}", flush=True)
        self._estado.actual = nombre

    def _nadie(self) -> RelevoAgotado:
        t = self.reloj()
        espera = min((h - t for h in self._estado.hasta.values()), default=0.0)
        return RelevoAgotado(
            f"todos los modelos están agotados ({', '.join(self.nombres)}); "
            f"el primero vuelve en {espera / 60:.0f} min"
        )

    async def astream(self, entrada: Any, **kwargs: Any) -> AsyncIterator[Any]:
        await self._espaciar()
        for nombre, modelo in self._disponibles():
            emitio = False
            try:
                async for trozo in modelo.astream(entrada, **kwargs):
                    emitio = emitio or _es_respuesta(trozo)
                    yield trozo
            except Exception as exc:
                tipo = tipo_de_agotamiento(exc)
                if tipo is None or emitio:
                    raise
                self._agotar(nombre, tipo, exc)
                continue
            self._contesto(nombre)
            return
        raise self._nadie()

    async def ainvoke(self, entrada: Any, **kwargs: Any) -> Any:
        await self._espaciar()
        for nombre, modelo in self._disponibles():
            try:
                respuesta = await modelo.ainvoke(entrada, **kwargs)
            except Exception as exc:
                tipo = tipo_de_agotamiento(exc)
                if tipo is None:
                    raise
                self._agotar(nombre, tipo, exc)
                continue
            self._contesto(nombre)
            return respuesta
        raise self._nadie()
