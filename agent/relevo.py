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
from typing import Any, Protocol
from zoneinfo import ZoneInfo

CUARENTENA_MINUTO_S = 60.0
CUARENTENA_CAIDO_S = 120.0
# ⚠ HAY FALLOS QUE NO SE CURAN ESPERANDO, Y TRATARLOS COMO CAÍDAS CUESTA VUELTAS.
# Un 404 «model does not exist or you do not have access to it» y un 402 «payment
# required» no cambian en dos minutos: piden editar la configuración o pagar, o
# sea otro despliegue. Con `CUARENTENA_CAIDO_S` el modelo volvía a probarse en la
# vuelta siguiente y en la siguiente, gastando una llamada fallida cada vez para
# descubrir lo mismo. Con infinito sale de la rotación para esta sesión, que es
# exactamente lo que durará la causa.
CUARENTENA_PERMANENTE_S = float("inf")
# Si todos están en cuarentena pero el primero vuelve en menos de esto, se
# espera en vez de perder la vuelta. Medido el 2026-09-15: «todos agotados; el
# primero vuelve en 0 min» y la vuelta se perdió por segundos.
ESPERA_CORTA_S = 150.0
# Las cuotas diarias de la API de Gemini se renuevan a medianoche del Pacífico.
PACIFICO = ZoneInfo("America/Los_Angeles")


class Catalogo(Protocol):
    """Lo que el relevo necesita de `agent/catalogo.py`, sin importarlo.

    ⚠ LA CUARENTENA `permanente` DURA UNA SESIÓN Y EL HECHO QUE LA CAUSA DURA
    MÁS. Su propio mensaje lo dice —«no vuelve en esta sesión»— y eso bastaba
    mientras la causa fuese un typo en la configuración: el despliegue siguiente
    lo arreglaba. Pero el 2026-09-18 apareció otra causa: el brazo openrouter
    recibió 404 «No endpoints found that support tool use» de
    `z-ai/glm-5.2:free`, o sea un modelo SIN llamada a herramientas, que es lo
    único que este agente hace. Eso no lo arregla ningún reinicio, y tras el
    reinicio el relevo lo volvía a intentar. Con este protocolo el
    descubrimiento sale del proceso y queda en el volumen.

    Es opcional y se llama a prueba de fallos: ver `_avisar_catalogo`.
    """

    def descartar(self, modelo: str, *, codigo: int | None, texto: str) -> Any: ...

    def funciono(self, modelo: str) -> Any: ...


class RelevoAgotado(RuntimeError):
    """Todos los modelos de la lista están en cuarentena."""


def _sumar_uso(acumulado: Any, trozo: Any) -> Any:
    """Suma los contadores de `usage_metadata` de dos trozos. Ver `astream`."""
    if not isinstance(trozo, dict):
        return acumulado
    if not isinstance(acumulado, dict):
        return dict(trozo)
    junto = dict(acumulado)
    for clave, valor in trozo.items():
        previo = junto.get(clave)
        junto[clave] = (
            previo + valor if isinstance(previo, int) and isinstance(valor, int) else valor
        )
    return junto


def _hora() -> str:
    """La hora local en cada línea del relevo.

    ⚠ SIN ESTO NO SE PUEDE EVALUAR EL HORARIO DE LOS BRAZOS. Las líneas
    `contesta`/`agotado` son lo único que dice cuándo se acabó cada modelo, y
    hasta el 2026-09-15 iban sin reloj: para saber que el 120b de Groq se agotó
    a las 17:34 hubo que cruzar el mtime del log con las velas de al lado. Ver
    paper/CRITERIO_HORARIOS.md.
    """
    return datetime.now().strftime("%H:%M:%S")


def codigo_http(exc: BaseException) -> int | None:
    """El HTTP del error, si lo trae.

    Google lo pone en `code`; el SDK de OpenAI (Groq, Cerebras, NVIDIA, Mistral,
    OpenRouter) en `status_code`. Lo leen dos sitios —la clasificación y la ficha
    del catálogo—, así que vive en una función y no en dos copias.
    """
    codigo = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return codigo if isinstance(codigo, int) else None


def tipo_de_agotamiento(exc: BaseException) -> str | None:
    """`minuto`, `dia`, `caido` o `permanente`; None si el error no es de cuota ni de servicio.

    Un error de argumentos o de red no es agotamiento y no se tapa cambiando
    de modelo: subiría igual con el siguiente.
    """
    codigo = codigo_http(exc)
    texto = str(exc)
    bajo_todo = texto.lower()
    # ⚠ 404 Y 402 SON DEL MODELO, NO DEL SERVICIO, Y SON PERMANENTES. Medido el
    # 2026-09-17 montando el brazo cerebras: `llama-3.3-70b` (con un guion de
    # más) daba 404 «Model does not exist or you do not have access to it», y el
    # nombre correcto daba 402 «Payment required». Los dos devolvían None acá, o
    # sea que el error subía y LA VUELTA SE PERDÍA SIN PROBAR EL SEGUNDO MODELO
    # de la lista — con dos configurados, el brazo moría por el primero y el
    # segundo no se ejercitó ni una vez. Costó cuatro intentos averiguar algo que
    # una rotación habría dicho en el primero.
    #
    # Van antes que todo lo demás porque el texto de un 402 puede contener
    # palabras que las reglas de abajo confundirían.
    if codigo in (402, 404) or "not_found_error" in bajo_todo or "payment_required" in bajo_todo:
        return "permanente"
    if codigo == 429 or "RateLimit" in type(exc).__name__ or "RESOURCE_EXHAUSTED" in texto:
        bajo = texto.lower()
        return "dia" if ("perday" in bajo or "per day" in bajo or "daily" in bajo) else "minuto"
    if codigo in (500, 502, 503, 504) or "UNAVAILABLE" in texto or "high demand" in texto.lower():
        return "caido"
    # 413 «Request too large»: el prompt supera el tope POR PETICIÓN del modelo
    # (Groq gratuito: 8.000 tokens). Ninguna espera lo arregla; se prueba con
    # el siguiente de la lista, que puede tener otro tope, y se anota.
    if codigo == 413 or "too large" in texto.lower():
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
        # Ver `Relevo.reservar_primero`. Compartido con las copias ligadas.
        self.reservar_primero = False


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
        catalogo: Catalogo | None = None,
        _estado: _Estado | None = None,
    ) -> None:
        if not modelos:
            raise ValueError("un relevo necesita al menos un modelo")
        self.modelos = modelos
        self.espera_s = espera_s
        self.reloj = reloj
        self.dormir = dormir
        self.ahora = ahora
        self.catalogo = catalogo
        self._estado = _estado or _Estado()

    @property
    def actual(self) -> str:
        """El último modelo que contestó; antes de la primera llamada, el primero."""
        return self._estado.actual or self.modelos[0][0]

    @property
    def contesto_alguien(self) -> bool:
        """Si algún modelo de la lista llegó a contestar.

        `actual` cae al primero de la lista mientras nadie haya contestado —es
        lo que necesita el prompt, y está probado—, pero eso NO sirve para
        SELLAR: una vuelta que murió con todos agotados quedaría atribuida al
        primero, que no escribió nada. Ver paper/trace.py.
        """
        return self._estado.actual is not None

    @property
    def nombres(self) -> list[str]:
        return [n for n, _ in self.modelos]

    def estado_modelos(self) -> dict[str, dict[str, Any]]:
        """Qué modelo está disponible y cuál en cuarentena, y por cuánto.

        Para vigilar la API desde fuera: un brazo remoto con TODA su lista
        agotada sigue vivo y sigue sondeando, pero no puede hacer una sola
        vuelta —y lo único que lo delata hoy es una línea de log. En orden
        alfabético, nunca por disponibilidad: es un parte, no un ranking.
        """
        t = self.reloj()
        return {
            nombre: {
                "disponible": self._estado.hasta.get(nombre, float("-inf")) <= t,
                "vuelve_en_min": max(0, round((self._estado.hasta.get(nombre, t) - t) / 60)),
                "contesto": self._estado.actual == nombre,
            }
            for nombre in sorted(self.nombres)
        }

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
            catalogo=self.catalogo,
            _estado=self._estado,
        )

    def reservar_primero(self, reservar: bool) -> None:
        """Si el primero de la lista se guarda para otra vuelta.

        ⚠ ES LA REGLA DE paper/CRITERIO_HORARIOS.md. Medido el 2026-09-15: el
        modelo bueno de cada lista se gastó por la mañana en vueltas de lectura
        y de niveles cercanos, y el cierre de 4h de las 20:00 —la vuelta que
        más vale— lo hizo el lite o nadie. Con la reserva puesta, las vueltas
        de gestión van del segundo en adelante y el primero llega a los cierres.
        La reserva NO deja a nadie sin modelo: si el primero es el único
        disponible, contesta él.
        """
        self._estado.reservar_primero = reservar

    def _disponibles(self) -> list[tuple[str, Any]]:
        t = self.reloj()
        vivos = [(n, m) for n, m in self.modelos if self._estado.hasta.get(n, float("-inf")) <= t]
        if self._estado.reservar_primero and len(vivos) > 1 and vivos[0][0] == self.modelos[0][0]:
            return vivos[1:]
        return vivos

    async def _espaciar(self) -> None:
        falta = self._estado.ultima_llamada + self.espera_s - self.reloj()
        if falta > 0:
            await self.dormir(falta)
        self._estado.ultima_llamada = self.reloj()

    def _avisar_catalogo(self, metodo: str, *args: Any, **kwargs: Any) -> None:
        """Llama al catálogo sin que pueda tumbar la vuelta.

        ⚠ EL CATÁLOGO ES UN APUNTE, NO UN REQUISITO. Vive en un SQLite del
        volumen que escriben hasta seis vigías a la vez; un `database is locked`,
        un volumen sin montar o un disco lleno tienen que costar un apunte
        perdido y una línea de log, nunca la vuelta —que es lo único que produce
        muestra—. De ahí el `except Exception` a secas: acá cualquier fallo del
        apunte es menos grave que propagarlo.
        """
        if self.catalogo is None:
            return
        try:
            getattr(self.catalogo, metodo)(*args, **kwargs)
        except Exception as exc:
            print(f"[relevo] {_hora()} no se pudo anotar en el catálogo: {exc}", flush=True)

    def _agotar(self, nombre: str, tipo: str, exc: BaseException) -> None:
        sugerida = espera_sugerida(exc)
        # ⚠ LA CUOTA DIARIA MANDA SOBRE LA ESPERA SUGERIDA. Medido el
        # 2026-09-15: gemini-3.5-flash agotó sus 20 peticiones/día de la capa
        # gratuita y el error traía «retryDelay: 36s»; con la sugerida
        # mandando, el relevo lo reintentaba en cada vuelta y gastaba una
        # llamada fallida antes de bajar al siguiente.
        if tipo == "minuto" and sugerida is not None:
            # Un margen: la renovación no es al segundo.
            cuarentena = sugerida + 5.0
        elif tipo == "dia":
            cuarentena = segundos_hasta_medianoche_pacifico(self.ahora() if self.ahora else None)
        elif tipo == "minuto":
            cuarentena = CUARENTENA_MINUTO_S
        elif tipo == "permanente":
            cuarentena = CUARENTENA_PERMANENTE_S
        else:
            cuarentena = CUARENTENA_CAIDO_S
        self._estado.hasta[nombre] = self.reloj() + cuarentena
        # «vuelve en inf min» no dice nada; lo que hace falta saber de un fallo
        # permanente es que no hay que esperarlo.
        cuando = (
            "no vuelve en esta sesión"
            if cuarentena == float("inf")
            else f"vuelve en {cuarentena / 60:.0f} min"
        )
        print(
            f"[relevo] {_hora()} {nombre} agotado ({tipo}): {cuando} · {str(exc)[:80]}",
            flush=True,
        )
        # ⚠ SOLO LO PERMANENTE ENTRA EN EL CATÁLOGO, Y ES LA LÍNEA QUE IMPORTA DE
        # ESTE MÉTODO. Un `minuto` es cuota, un `caido` es carga ajena o el tope
        # por petición: ninguno dice nada del modelo. El brazo Groq da 413
        # —`caido`— varias veces al día y es el que más muestra lleva de la
        # comparación; anotarlo como descartado lo borraría del relevo por ser
        # el que más trabaja.
        if tipo == "permanente":
            self._avisar_catalogo("descartar", nombre, codigo=codigo_http(exc), texto=str(exc))

    def _contesto(self, nombre: str, uso: Any = None) -> None:
        # Los tokens de entrada de cada llamada, para saber a qué distancia del
        # tope por petición va este brazo (Groq gratuito: 8.000).
        entrada = (uso or {}).get("input_tokens") if isinstance(uso, dict) else None
        # ⚠ Y LOS DE SALIDA, QUE SON LOS CAROS. En Gemini el pensamiento factura
        # como salida y cuesta cinco veces más que la entrada ($3,75 vs $0,75
        # por millón en 3.8-flash); en Groq, cuatro ($0,60 vs $0,15). Sin este
        # número, el coste diario de un brazo es una estimación mía y no un
        # dato, justo cuando toque decidir si alguno merece pagarse
        # (paper/CRITERIO_COMPARACION.md, a las 50 predicciones por brazo).
        salida = (uso or {}).get("output_tokens") if isinstance(uso, dict) else None
        # Lo que el proveedor sirvió de su caché: es lo único que dice si el
        # prefijo fijo (rol + instrucción + esquemas) se está reutilizando.
        detalle = (uso or {}).get("input_token_details") if isinstance(uso, dict) else None
        cache = detalle.get("cache_read") if isinstance(detalle, dict) else None
        peso = f" · {entrada} tokens de entrada" if entrada else ""
        if cache:
            peso += f" ({cache} de caché)"
        if salida:
            peso += f" · {salida} de salida"
        if self._estado.actual != nombre:
            print(f"[relevo] {_hora()} contesta {nombre}{peso}", flush=True)
            # En la TRANSICIÓN, no en cada vuelta: son unas pocas escrituras por
            # proceso en vez de una por llamada, y lo que hace falta saber es que
            # este modelo contestó una vuelta de verdad —con herramientas y
            # 7-10k de entrada—, no cuántas.
            self._avisar_catalogo("funciono", nombre)
        elif peso:
            print(f"[relevo] {_hora()} {nombre}{peso}", flush=True)
        self._estado.actual = nombre

    def _espera_hasta_el_primero(self) -> float:
        t = self.reloj()
        return max(0.0, min((h - t for h in self._estado.hasta.values()), default=0.0))

    async def _esperar_si_es_corto(self) -> bool:
        """Con todos en cuarentena: si el primero vuelve pronto, se espera. True si se esperó."""
        espera = self._espera_hasta_el_primero()
        if espera > ESPERA_CORTA_S:
            return False
        print(
            f"[relevo] {_hora()} todos en cuarentena; el primero vuelve en {espera:.0f} s: espero",
            flush=True,
        )
        await self.dormir(espera + 1.0)
        return True

    def _nadie(self) -> RelevoAgotado:
        espera = self._espera_hasta_el_primero()
        if espera == float("inf"):
            # Todos permanentes: no es «espera más», es «arregla la lista».
            return RelevoAgotado(
                f"ningún modelo del brazo se puede usar ({', '.join(self.nombres)}): "
                "404 o 402 en todos. Revisar los nombres contra /v1/models del proveedor "
                "y si la cuenta tiene acceso; esperar no lo arregla."
            )
        return RelevoAgotado(
            f"todos los modelos están agotados ({', '.join(self.nombres)}); "
            f"el primero vuelve en {espera / 60:.0f} min"
        )

    # ⚠ DOS PASADAS, Y LA ESPERA VA ENTRE LAS DOS. La primera versión solo
    # esperaba ANTES de empezar, si ya no había nadie disponible. Medido el
    # 2026-09-15 a las 20:06: el 120b llevaba agotado desde las 17:34, el 20b
    # cayó por tope de minuto A MITAD de la vuelta del cierre de 4h —«vuelve en
    # 1 min»—, y el relevo lanzó «todos agotados» sin esperar ese minuto: la
    # vuelta se perdió y no había reintento antes del reposo. Ahora, si la
    # lista se vacía durante la pasada y el primero vuelve pronto, se espera
    # UNA vez y se vuelve a pasar. Una sola vez: si el segundo intento también
    # se vacía, es que el tope no es de segundos.
    async def astream(self, entrada: Any, **kwargs: Any) -> AsyncIterator[Any]:
        await self._espaciar()
        for pasada in (1, 2):
            for nombre, modelo in self._disponibles():
                emitio = False
                uso: Any = None
                try:
                    async for trozo in modelo.astream(entrada, **kwargs):
                        emitio = emitio or _es_respuesta(trozo)
                        # ⚠ SE ACUMULA, NO SE PISA. Gemini manda `usage_metadata`
                        # como DELTA por trozo (langchain_google_genai resta el
                        # uso previo en streaming), así que quedarse con el
                        # último daba «32 tokens de entrada» en el log para una
                        # llamada de miles. Groq manda totales, y sumar deltas
                        # que solo llegan una vez da lo mismo.
                        uso = _sumar_uso(uso, getattr(trozo, "usage_metadata", None))
                        yield trozo
                except Exception as exc:
                    tipo = tipo_de_agotamiento(exc)
                    if tipo is None or emitio:
                        raise
                    self._agotar(nombre, tipo, exc)
                    continue
                self._contesto(nombre, uso)
                return
            if pasada == 1 and await self._segunda_pasada():
                continue
            raise self._nadie()

    async def _segunda_pasada(self) -> bool:
        """Si merece volver a pasar la lista tras vaciarse la primera.

        Dos casos: la reserva del primero dejó fuera al único que sigue vivo
        (se suelta sola: `_disponibles` no reserva cuando queda uno), o todos
        están en cuarentena pero el primero vuelve en segundos (se espera).
        """
        if self._disponibles():
            return True
        return await self._esperar_si_es_corto()

    async def ainvoke(self, entrada: Any, **kwargs: Any) -> Any:
        await self._espaciar()
        for pasada in (1, 2):
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
            if pasada == 1 and await self._segunda_pasada():
                continue
            raise self._nadie()
        raise self._nadie()  # inalcanzable; para el tipado
