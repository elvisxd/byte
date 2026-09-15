"""El relevo: una lista de modelos que se turnan cuando uno se agota.

Lo que protege es que la vuelta NO se pierda por un 429 —se pasa al siguiente
en la misma llamada—, que el agotado quede en cuarentena el tiempo que dice su
error, que el modelo que contestó quede en `actual` (es lo que sella cada
operación), y que NO se cambie de modelo a mitad de una respuesta.
"""

from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk

from agent.relevo import (
    CUARENTENA_MINUTO_S,
    PACIFICO,
    Relevo,
    RelevoAgotado,
    espera_sugerida,
    segundos_hasta_medianoche_pacifico,
    tipo_de_agotamiento,
)


class _Error(Exception):
    def __init__(self, code: int, texto: str = "") -> None:
        super().__init__(texto)
        self.code = code


class _Modelo:
    """Un chat model falso: contesta, o falla con lo que se le diga."""

    def __init__(self, nombre: str, fallo: Exception | None = None, trozos: int = 2) -> None:
        self.nombre = nombre
        self.fallo = fallo
        self.trozos = trozos
        self.llamadas = 0
        self.ligado: Any = None

    def bind_tools(self, esquemas: Any) -> "_Modelo":
        copia = _Modelo(self.nombre, self.fallo, self.trozos)
        copia.ligado = esquemas
        return copia

    async def astream(self, entrada: Any, **_: Any) -> Any:
        self.llamadas += 1
        if self.fallo is not None:
            raise self.fallo
        for i in range(self.trozos):
            yield f"{self.nombre}:{i}"

    async def ainvoke(self, entrada: Any, **_: Any) -> Any:
        self.llamadas += 1
        if self.fallo is not None:
            raise self.fallo
        return self.nombre


class _Reloj:
    def __init__(self) -> None:
        self.t = 1000.0
        self.dormido: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def dormir(self, s: float) -> None:
        self.dormido.append(s)
        self.t += s


async def _todo(relevo: Relevo, entrada: str = "hola") -> list[str]:
    return [t async for t in relevo.astream(entrada)]


def _relevo(*modelos: _Modelo, reloj: _Reloj | None = None, espera_s: float = 0.0) -> Relevo:
    r = reloj or _Reloj()
    return Relevo([(m.nombre, m) for m in modelos], espera_s=espera_s, reloj=r, dormir=r.dormir)


def test_clasifica_los_errores() -> None:
    assert tipo_de_agotamiento(_Error(429, "quota exceeded")) == "minuto"
    assert tipo_de_agotamiento(_Error(429, "GenerateRequestsPerDayPerProjectPerModel")) == "dia"
    assert tipo_de_agotamiento(_Error(503, "high demand")) == "caido"
    assert tipo_de_agotamiento(_Error(500, "Internal error")) == "caido"
    assert tipo_de_agotamiento(_Error(400, "argumento inválido")) is None
    # Medido: el servidor cortó sin contestar. Es una caída, no otra cosa.
    assert (
        tipo_de_agotamiento(RuntimeError("Server disconnected without sending a response."))
        == "caido"
    )
    assert tipo_de_agotamiento(type("ConnectError", (Exception,), {})("x")) == "caido"
    assert tipo_de_agotamiento(type("ReadTimeout", (Exception,), {})("x")) == "caido"
    assert tipo_de_agotamiento(ValueError("otra cosa")) is None


async def test_un_429_pasa_al_siguiente_en_la_misma_llamada() -> None:
    a = _Modelo("a", _Error(429, "quota"))
    b = _Modelo("b")
    relevo = _relevo(a, b)

    assert await _todo(relevo) == ["b:0", "b:1"]
    assert relevo.actual == "b"
    assert a.llamadas == 1 and b.llamadas == 1


async def test_el_agotado_queda_en_cuarentena_y_vuelve() -> None:
    reloj = _Reloj()
    a = _Modelo("a", _Error(429, "quota"))
    b = _Modelo("b")
    relevo = _relevo(a, b, reloj=reloj)

    await _todo(relevo)
    a.fallo = None
    await _todo(relevo)
    # Sigue en cuarentena: ni se lo intenta.
    assert a.llamadas == 1

    reloj.t += CUARENTENA_MINUTO_S + 1
    assert await _todo(relevo) == ["a:0", "a:1"]
    assert relevo.actual == "a"


async def test_el_tope_diario_lo_aparta_hasta_medianoche_del_pacifico() -> None:
    reloj = _Reloj()
    ahora = datetime(2026, 9, 15, 10, 0, tzinfo=PACIFICO)
    a = _Modelo("a", _Error(429, "GenerateRequestsPerDayPerProjectPerModel"))
    b = _Modelo("b")
    relevo = Relevo(
        [("a", a), ("b", b)], espera_s=0, reloj=reloj, dormir=reloj.dormir, ahora=lambda: ahora
    )

    await _todo(relevo)
    a.fallo = None
    reloj.t += 3 * 3600
    await _todo(relevo)
    assert a.llamadas == 1, "a las 13:00 del Pacífico la cuota diaria no se renovó"

    reloj.t += segundos_hasta_medianoche_pacifico(ahora)
    await _todo(relevo)
    assert a.llamadas == 2


def test_los_errores_de_openai_traen_status_code() -> None:
    """Groq va por el SDK de OpenAI: el HTTP está en `status_code`, no en `code`."""

    class RateLimitError(Exception):
        status_code = 429

    class InternalServerError(Exception):
        status_code = 503

    assert tipo_de_agotamiento(RateLimitError("Rate limit reached")) == "minuto"
    assert tipo_de_agotamiento(InternalServerError("Service Unavailable")) == "caido"

    class BadRequest(Exception):
        status_code = 413

    # Medido en Groq: «Request too large … TPM: Limit 8000, Requested 9252».
    assert tipo_de_agotamiento(BadRequest("Request too large for model")) == "caido"


def test_la_espera_la_dice_el_error_cuando_la_dice() -> None:
    assert espera_sugerida(RuntimeError("Please try again in 1h2m3.5s.")) == 3600 + 120 + 3.5
    assert espera_sugerida(RuntimeError("Please try again in 12.4s.")) == 12.4
    assert espera_sugerida(RuntimeError("{'retryDelay': '23s'}")) == 23.0
    assert espera_sugerida(RuntimeError("quota exceeded")) is None


async def test_el_429_con_espera_dicha_manda_sobre_la_constante() -> None:
    reloj = _Reloj()
    a = _Modelo("a", _Error(429, "Rate limit reached ... Please try again in 3m0s."))
    b = _Modelo("b")
    relevo = _relevo(a, b, reloj=reloj)

    await _todo(relevo)
    a.fallo = None
    reloj.t += CUARENTENA_MINUTO_S + 1
    await _todo(relevo)
    assert a.llamadas == 1, "un minuto no basta: el error pidió tres"
    reloj.t += 2 * 60 + 10
    await _todo(relevo)
    assert a.llamadas == 2


async def test_la_cuota_diaria_manda_sobre_el_retry_delay() -> None:
    """Medido: 20 peticiones/día agotadas y el error decía «retryDelay: 36s»."""
    reloj = _Reloj()
    ahora = datetime(2026, 9, 15, 10, 0, tzinfo=PACIFICO)
    a = _Modelo(
        "a", _Error(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier {'retryDelay': '36s'}")
    )
    b = _Modelo("b")
    relevo = Relevo(
        [("a", a), ("b", b)], espera_s=0, reloj=reloj, dormir=reloj.dormir, ahora=lambda: ahora
    )

    await _todo(relevo)
    a.fallo = None
    reloj.t += 120
    await _todo(relevo)
    assert a.llamadas == 1, "dos minutos después sigue en cuarentena: la cuota es del día"


def test_medianoche_del_pacifico_es_la_siguiente() -> None:
    ahora = datetime(2026, 9, 15, 23, 30, tzinfo=PACIFICO)
    # Media hora más el margen de un minuto.
    assert segundos_hasta_medianoche_pacifico(ahora) == pytest.approx(30 * 60 + 60)


async def test_si_todos_estan_agotados_lo_dice() -> None:
    relevo = _relevo(_Modelo("a", _Error(429, "q")), _Modelo("b", _Error(503, "high demand")))
    with pytest.raises(RelevoAgotado, match="a, b"):
        await _todo(relevo)


async def test_un_error_que_no_es_de_cuota_sube_tal_cual() -> None:
    relevo = _relevo(_Modelo("a", ValueError("argumento inválido")), _Modelo("b"))
    with pytest.raises(ValueError, match="argumento"):
        await _todo(relevo)


async def test_no_cambia_de_modelo_a_mitad_de_respuesta() -> None:
    """Pegar media respuesta de un modelo con media de otro no es una respuesta."""

    class _Corta(_Modelo):
        async def astream(self, entrada: Any, **_: Any) -> Any:
            yield AIMessageChunk(content="a:0")
            raise _Error(503, "high demand")

    relevo = _relevo(_Corta("a"), _Modelo("b"))
    with pytest.raises(_Error):
        await _todo(relevo)


async def test_si_solo_salio_pensamiento_si_cambia() -> None:
    """Medido: 3.7-flash pensó entero y el 503 llegó antes del texto. Eso no es media respuesta."""

    class _PiensaYCae(_Modelo):
        async def astream(self, entrada: Any, **_: Any) -> Any:
            yield AIMessageChunk(content=[{"type": "thinking", "thinking": "miro el rango"}])
            raise _Error(503, "high demand")

    relevo = _relevo(_PiensaYCae("a"), _Modelo("b"))
    trozos = await _todo(relevo)
    assert trozos[-1] == "b:1"
    assert relevo.actual == "b"


async def test_espacia_las_llamadas() -> None:
    reloj = _Reloj()
    relevo = _relevo(_Modelo("a"), reloj=reloj, espera_s=12)

    await _todo(relevo)
    reloj.t += 3
    await _todo(relevo)

    assert reloj.dormido == [9]


async def test_la_copia_con_herramientas_comparte_el_estado() -> None:
    """El grafo usa la ligada para el agente y la pelada para resumir: mismas cuarentenas."""
    a = _Modelo("a", _Error(429, "q"))
    b = _Modelo("b")
    relevo = _relevo(a, b)
    ligado = relevo.bind_tools([{"name": "x"}])

    await _todo(ligado)
    assert ligado.actual == "b" and relevo.actual == "b"
    assert await relevo.ainvoke("resumí") == "b"
    # La pelada ni intentó el agotado: vio la cuarentena que dejó la ligada.
    assert a.llamadas == 0
    assert ligado.modelos[0][1].llamadas == 1
    assert ligado.modelos[1][1].ligado == [{"name": "x"}]


def test_antes_de_la_primera_llamada_el_actual_es_el_primero() -> None:
    assert _relevo(_Modelo("a"), _Modelo("b")).actual == "a"
