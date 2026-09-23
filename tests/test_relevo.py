"""El relevo: una lista de modelos que se turnan cuando uno se agota.

Lo que protege es que la vuelta NO se pierda por un 429 —se pasa al siguiente
en la misma llamada—, que el agotado quede en cuarentena el tiempo que dice su
error, que el modelo que contestó quede en `actual` (es lo que sella cada
operación), y que NO se cambie de modelo a mitad de una respuesta.
"""

import sqlite3
from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent.relevo import (
    CUARENTENA_MINUTO_S,
    MARGEN_RECORTE,
    PACIFICO,
    Relevo,
    RelevoAgotado,
    espera_sugerida,
    por_minuto,
    presupuesto_del_413,
    recortar_entrada,
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


def test_un_404_o_un_402_son_del_modelo_y_son_permanentes() -> None:
    """Costó cuatro intentos con el brazo cerebras: antes devolvían None, el error
    subía y la vuelta se perdía SIN probar el segundo modelo de la lista."""
    assert (
        tipo_de_agotamiento(_Error(404, "Model does not exist or you do not have access"))
        == "permanente"
    )
    assert (
        tipo_de_agotamiento(_Error(402, "Payment required to access this resource")) == "permanente"
    )
    # También por el texto, que es como los envuelve el SDK de OpenAI.
    assert tipo_de_agotamiento(_Error(0, "{'type': 'not_found_error'}")) == "permanente"
    assert tipo_de_agotamiento(_Error(0, "{'type': 'payment_required'}")) == "permanente"
    # Y no se confunden con lo que sí se cura esperando.
    assert tipo_de_agotamiento(_Error(503, "high demand")) == "caido"
    assert tipo_de_agotamiento(_Error(413, "Request too large")) == "caido"
    # Una clave inválida NO es del modelo: rotar gastaría una llamada por cada
    # uno para fallar igual, así que sigue subiendo.
    assert tipo_de_agotamiento(_Error(401, "invalid api key")) is None


async def test_un_modelo_permanente_no_se_reintenta_y_deja_paso_al_siguiente() -> None:
    reloj = _Reloj()
    malo = _Modelo("malo", _Error(404, "Model does not exist"))
    bueno = _Modelo("bueno")
    relevo = _relevo(malo, bueno, reloj=reloj)

    # La vuelta NO se pierde: rota al segundo en la misma llamada.
    assert await _todo(relevo) == ["bueno:0", "bueno:1"]
    assert relevo.actual == "bueno"
    assert malo.llamadas == 1

    # Y no se vuelve a intentar, ni pasado un día: la causa no se cura esperando.
    reloj.t += 60 * 60 * 24
    assert await _todo(relevo) == ["bueno:0", "bueno:1"]
    assert malo.llamadas == 1, "un 404 no se reintenta nunca en esta sesión"


async def test_con_todos_permanentes_el_error_dice_que_esperar_no_sirve() -> None:
    relevo = _relevo(
        _Modelo("uno", _Error(404, "Model does not exist")),
        _Modelo("dos", _Error(402, "Payment required")),
    )
    with pytest.raises(RelevoAgotado) as e:
        await _todo(relevo)
    # Ni «vuelve en inf min», ni «espera»: lo que hace falta es arreglar la lista.
    assert "esperar no lo arregla" in str(e.value)
    assert "inf" not in str(e.value)


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


async def test_si_todos_estan_agotados_por_mucho_tiempo_lo_dice() -> None:
    reloj = _Reloj()
    dia = _Error(429, "GenerateRequestsPerDayPerProjectPerModel")
    relevo = _relevo(_Modelo("a", dia), _Modelo("b", dia), reloj=reloj)
    with pytest.raises(RelevoAgotado, match="a, b"):
        await _todo(relevo)
    assert reloj.dormido == [], "hasta medianoche no se espera: se pierde la vuelta y se avisa"


async def test_si_todos_estan_agotados_pero_por_segundos_espera() -> None:
    """Medido: «todos agotados; el primero vuelve en 0 min» y la vuelta se perdía."""
    reloj = _Reloj()
    a = _Modelo("a", _Error(429, "Rate limit reached. Please try again in 20s."))
    b = _Modelo("b", _Error(503, "high demand"))
    relevo = _relevo(a, b, reloj=reloj)

    # Primera llamada: los dos caen, y el primero (a) vuelve en 20 s.
    with pytest.raises(RelevoAgotado):
        await _todo(relevo)
    a.fallo = None
    # Segunda llamada: en vez de fallar, espera esos ~25 s y `a` contesta.
    assert await _todo(relevo) == ["a:0", "a:1"]
    assert any(20 <= d <= 30 for d in reloj.dormido), reloj.dormido


async def test_si_el_ultimo_cae_a_mitad_de_llamada_espera_si_es_corto() -> None:
    """Medido el 2026-09-15 a las 20:06: el 120b agotado del día, el 20b cae
    por tope de MINUTO en la cuarta llamada de la vuelta, y el relevo lanzó
    «todos agotados; el primero vuelve en 1 min» sin esperar ese minuto: la
    espera solo existía ANTES de empezar. La vuelta del cierre de 4h se perdió
    y no había reintento antes del reposo."""

    class _CaeUnaVez(_Modelo):
        """Falla la primera vez que se le llama y contesta después: un tope de minuto."""

        async def astream(self, entrada: Any, **_: Any) -> Any:
            self.llamadas += 1
            if self.fallo is not None:
                fallo, self.fallo = self.fallo, None
                raise fallo
            for i in range(self.trozos):
                yield f"{self.nombre}:{i}"

    reloj = _Reloj()
    ahora = datetime(2026, 9, 15, 17, 34, tzinfo=PACIFICO)
    a = _Modelo("a", _Error(429, "GenerateRequestsPerDayPerProjectPerModel"))
    b = _CaeUnaVez("b")
    relevo = Relevo(
        [("a", a), ("b", b)], espera_s=0, reloj=reloj, dormir=reloj.dormir, ahora=lambda: ahora
    )
    # 17:34: `a` se agota del día; `b` carga con todo.
    assert await _todo(relevo) == ["b:0", "b:1"]

    # 20:06: `b` choca con el tope de minuto A MITAD de la vuelta. Antes: «todos
    # agotados», vuelta perdida. Ahora: se espera ~25 s y `b` contesta.
    b.fallo = _Error(429, "Rate limit reached. Please try again in 20s.")
    assert await _todo(relevo) == ["b:0", "b:1"]
    assert b.llamadas == 3, "una que falló, y la que contestó tras esperar"
    assert any(20 <= d <= 30 for d in reloj.dormido), reloj.dormido
    assert a.llamadas == 1, "el agotado del día ni se intenta"


async def test_los_tokens_de_entrada_se_suman_y_no_se_pisan() -> None:
    """Gemini manda `usage_metadata` como DELTA por trozo: quedarse con el
    último daba «32 tokens de entrada» para una llamada de miles."""
    from agent.relevo import _sumar_uso

    class _ConUso:
        def __init__(self, uso: dict[str, Any] | None) -> None:
            self.usage_metadata = uso
            self.content = "x"

    class _ModeloConDeltas(_Modelo):
        async def astream(self, entrada: Any, **_: Any) -> Any:
            self.llamadas += 1
            yield _ConUso({"input_tokens": 32, "output_tokens": 1})
            yield _ConUso({"input_tokens": 1300, "output_tokens": 10})
            yield _ConUso(None)

    relevo = _relevo(_ModeloConDeltas("a"))
    await _todo(relevo)

    assert _sumar_uso({"input_tokens": 32}, {"input_tokens": 1300}) == {"input_tokens": 1332}
    assert _sumar_uso(None, {"input_tokens": 5}) == {"input_tokens": 5}
    assert _sumar_uso({"input_tokens": 5}, None) == {"input_tokens": 5}
    # Un campo no numérico no rompe la suma: gana el último.
    assert _sumar_uso({"modelo": "a"}, {"modelo": "b"}) == {"modelo": "b"}


async def test_con_reserva_el_primero_se_guarda_y_contesta_el_segundo() -> None:
    """paper/CRITERIO_HORARIOS.md: las vueltas de gestión van del segundo en
    adelante; el primero se guarda para los cierres de 4h."""
    a, b = _Modelo("a"), _Modelo("b")
    relevo = _relevo(a, b)

    relevo.reservar_primero(True)
    assert await _todo(relevo) == ["b:0", "b:1"]
    assert a.llamadas == 0

    relevo.reservar_primero(False)
    assert await _todo(relevo) == ["a:0", "a:1"]

    # La copia con herramientas ve la misma reserva.
    relevo.reservar_primero(True)
    assert await relevo.bind_tools([{"name": "x"}]).ainvoke("hola") == "b"


async def test_la_reserva_no_deja_a_nadie_sin_modelo() -> None:
    """Si el primero es el único vivo, contesta él: reservar no es agotar."""
    reloj = _Reloj()
    a = _Modelo("a")
    b = _Modelo("b", _Error(429, "GenerateRequestsPerDayPerProjectPerModel"))
    relevo = Relevo(
        [("a", a), ("b", b)],
        espera_s=0,
        reloj=reloj,
        dormir=reloj.dormir,
        ahora=lambda: datetime(2026, 9, 15, 10, 0, tzinfo=PACIFICO),
    )
    relevo.reservar_primero(True)
    # b cae del día en la primera llamada; con la reserva, a era el descartado…
    # pero al quedar solo, contesta.
    assert await _todo(relevo) == ["a:0", "a:1"]
    assert b.llamadas == 1 and a.llamadas == 1


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


async def test_contesto_alguien_separa_el_actual_del_que_de_verdad_contesto() -> None:
    """`actual` sirve para ESCRIBIR; `contesto_alguien`, para SELLAR.

    La primera escritura de una sesión remota ocurre con `actual` todavía en el
    primero de la lista —las llamadas a herramientas llegan dentro del stream,
    y `_contesto` se marca al terminarlo—, así que el registro necesita ese
    nombre aproximado. La traza no: una vuelta que muere con todos agotados no
    la hizo nadie, y sellarla con el primero es la atribución falsa que
    CRITERIO_COMPARACION.md prohíbe en mayúsculas.
    """
    relevo = _relevo(_Modelo("a"), _Modelo("b"))
    assert relevo.actual == "a" and not relevo.contesto_alguien

    await relevo.ainvoke("hola")
    assert relevo.actual == "a" and relevo.contesto_alguien


async def test_si_no_contesta_nadie_no_hay_a_quien_atribuirle_la_vuelta() -> None:
    """Todos agotados: la vuelta se pierde y NADIE la firmó."""
    relevo = _relevo(_Modelo("a", _Error(429, "quota")), _Modelo("b", _Error(429, "quota")))
    with pytest.raises(RelevoAgotado):
        await relevo.ainvoke("hola")
    assert relevo.actual == "a" and not relevo.contesto_alguien


async def test_el_parte_de_modelos_dice_quien_esta_en_cuarentena() -> None:
    """Para vigilar la API desde fuera.

    Un brazo con toda su lista agotada sigue vivo y sigue sondeando, pero no
    puede hacer una sola vuelta: hoy eso solo lo dice una línea de log que
    nadie mira de madrugada.
    """
    reloj = _Reloj()
    relevo = _relevo(_Modelo("a", _Error(429, "quota")), _Modelo("b"), reloj=reloj)

    await relevo.ainvoke("hola")
    parte = relevo.estado_modelos()
    assert list(parte) == ["a", "b"], "en orden alfabético, nunca por disponibilidad"
    assert parte["a"]["disponible"] is False and parte["a"]["vuelve_en_min"] >= 1
    assert parte["b"]["disponible"] is True and parte["b"]["contesto"] is True
    assert parte["a"]["contesto"] is False

    # Pasada la cuarentena, el agotado vuelve a estar disponible.
    reloj.t += CUARENTENA_MINUTO_S + 1
    assert relevo.estado_modelos()["a"]["disponible"] is True


def test_el_log_lleva_entrada_y_salida_para_poder_costear(capsys) -> None:
    """Sin los de SALIDA el coste de un brazo es una estimación, no un dato: en
    Gemini el pensamiento factura como salida y cuesta 5x la entrada."""
    relevo = _relevo(_Modelo("a"))

    relevo._contesto(
        "a",
        {
            "input_tokens": 6000,
            "output_tokens": 1500,
            "input_token_details": {"cache_read": 4000},
        },
    )

    linea = capsys.readouterr().out
    assert "6000 tokens de entrada" in linea
    assert "4000 de caché" in linea
    assert "1500 de salida" in linea


# ── el apunte en el catálogo ────────────────────────────────────────────────
# La cuarentena `permanente` dura una sesión y el hecho que la causa dura más:
# el 2026-09-18 el brazo openrouter descubrió que `z-ai/glm-5.2:free` no tiene
# llamada a herramientas, y tras el reinicio lo habría vuelto a intentar. Lo que
# se protege acá es QUÉ llega al catálogo, porque un apunte de más borra un
# modelo bueno: solo lo permanente, nunca una cuota ni un 413.


class _CatalogoFalso:
    def __init__(self, revienta: bool = False) -> None:
        self.descartados: list[tuple[str, int | None, str]] = []
        self.funcionaron: list[str] = []
        self.revienta = revienta

    def descartar(self, modelo: str, *, codigo: int | None, texto: str) -> None:
        if self.revienta:
            raise sqlite3.OperationalError("database is locked")
        self.descartados.append((modelo, codigo, texto))

    def funciono(self, modelo: str) -> None:
        if self.revienta:
            raise sqlite3.OperationalError("database is locked")
        self.funcionaron.append(modelo)


async def test_el_404_se_anota_en_el_catalogo():
    cat = _CatalogoFalso()
    glm = _Modelo("glm", _Error(404, "No endpoints found that support tool use"))
    relevo = Relevo([("glm", glm), ("qwen", _Modelo("qwen"))], espera_s=0, catalogo=cat)
    [t async for t in relevo.astream("hola")]
    assert [m for m, _, _ in cat.descartados] == ["glm"]
    assert cat.descartados[0][1] == 404
    # Y el que contestó queda anotado como que sirve, en la transición.
    assert cat.funcionaron == ["qwen"]


@pytest.mark.parametrize(
    "error",
    [
        _Error(429, "Rate limit reached, please try again in 1m0s"),
        _Error(503, "The model is overloaded due to high demand"),
        # ⚠ El 413 es el caso peligroso: lo da el brazo Groq varias veces al día
        # contra su tope de 8.000 tokens por petición, y es el brazo con más
        # muestra de la comparación. Anotarlo lo borraría por ser el que trabaja.
        _Error(413, "Request too large for model gpt-oss-120b"),
    ],
)
async def test_la_cuota_y_el_tamano_nunca_entran_al_catalogo(error):
    cat = _CatalogoFalso()
    relevo = Relevo(
        [("primero", _Modelo("primero", error)), ("segundo", _Modelo("segundo"))],
        espera_s=0,
        catalogo=cat,
    )
    [t async for t in relevo.astream("hola")]
    assert cat.descartados == []


async def test_un_catalogo_roto_no_se_lleva_la_vuelta():
    """Es un apunte, no un requisito: el volumen puede estar lleno o bloqueado."""
    cat = _CatalogoFalso(revienta=True)
    relevo = Relevo(
        [("glm", _Modelo("glm", _Error(404, "not found"))), ("qwen", _Modelo("qwen"))],
        espera_s=0,
        catalogo=cat,
    )
    trozos = [t async for t in relevo.astream("hola")]
    assert trozos == ["qwen:0", "qwen:1"]
    assert relevo.actual == "qwen"


async def test_sin_catalogo_todo_sigue_igual():
    """El brazo local y cualquier corrida de prueba van sin catálogo."""
    relevo = Relevo([("uno", _Modelo("uno"))], espera_s=0)
    assert [t async for t in relevo.astream("hola")] == ["uno:0", "uno:1"]


# ── el 413 es por minuto, no por tamaño ─────────────────────────────────────
# Medido el 2026-09-18 en el brazo groq: un input de 7604 pasa y uno de 7283 del
# MISMO modelo falla siete segundos antes. Lo que discrimina no es el tamaño de
# la petición sino cuántos tokens lleva gastados el minuto. El comentario del
# módulo decía lo contrario y de ahí salía «ninguna espera lo arregla», que es
# justo al revés: la espera corta del relevo es lo que salva esa vuelta.

GROQ_413 = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "`openai/gpt-oss-20b` in organization `org_x` service tier `on_demand` on "
    "tokens per minute (TPM): Limit 8000, Requested 8500, please reduce your "
    "message size and try again.'}}"
)


def test_el_413_trae_su_presupuesto():
    assert presupuesto_del_413(GROQ_413) == (8000, 8500)
    assert por_minuto(GROQ_413)


def test_un_413_sin_cifras_no_revienta():
    assert presupuesto_del_413("Request too large") is None


def test_el_413_sigue_siendo_caido():
    """Curable con tiempo, como un 429 de minuto — y nunca un veredicto del modelo."""

    class _E(Exception):
        status_code = 413

    assert tipo_de_agotamiento(_E(GROQ_413)) == "caido"


async def test_las_cifras_del_413_salen_en_el_log(capsys):
    """⚠ El recorte a 80 caracteres escondía «Limit 8000» y con él la causa."""
    relevo = Relevo(
        [("20b", _Modelo("20b", _Error(413, GROQ_413))), ("120b", _Modelo("120b"))],
        espera_s=0,
    )
    [t async for t in relevo.astream("hola")]
    salida = capsys.readouterr().out
    assert "tope 8000 por minuto, pedido 8500" in salida


# ── el 413 con cifras: recortar y reintentar el mismo modelo ────────────────
# Medido el 2026-09-20 a las 12:19 EDT: el 20b contestó con 7510 de entrada la
# misma petición que el 120b rechazó con «Limit 8000, Requested 9266», y 9266 −
# 7510 = 1756 = la salida anterior del 120b. Groq suma la última salida de ese
# modelo a la petición siguiente. Antes de esto el 413 rotaba y, con los dos de
# Groq contra el mismo muro, la vuelta se perdía («la vuelta 11 falló»).


def _paso(i: int, largo: int = 4000) -> list:
    """Un AIMessage con llamada y su ToolMessage: ~1000 tokens con largo=4000."""
    return [
        AIMessage(content="", tool_calls=[{"name": "velas", "args": {}, "id": f"c{i}"}]),
        ToolMessage(content="x" * largo, tool_call_id=f"c{i}"),
    ]


def _vuelta(pasos: int) -> list:
    entrada = [SystemMessage(content="rol"), HumanMessage(content="mapa " * 200)]
    for i in range(pasos):
        entrada += _paso(i)
    return entrada


def _413(pedido: int, tope: int = 8000) -> _Error:
    return _Error(
        413,
        f"Request too large for model `x` on tokens per minute (TPM): Limit {tope}, "
        f"Requested {pedido}, please reduce your message size and try again.",
    )


def test_recortar_quita_los_pasos_mas_viejos_y_deja_el_ultimo():
    entrada = _vuelta(4)
    recortada = recortar_entrada(entrada, 500)
    assert recortada is not None
    # Cabecera intacta y el último paso intacto.
    assert recortada[:2] == entrada[:2]
    assert recortada[-2:] == entrada[-2:]
    assert len(recortada) < len(entrada)
    # ⚠ Ningún ToolMessage huérfano: cada uno va precedido de su AIMessage.
    for a, b in zip(recortada, recortada[1:], strict=False):
        if isinstance(b, ToolMessage):
            assert isinstance(a, (AIMessage, ToolMessage))
    assert isinstance(recortada[2], AIMessage)


def test_recortar_no_toca_una_vuelta_de_un_solo_paso():
    assert recortar_entrada(_vuelta(1), 100) is None


def test_recortar_devuelve_none_si_no_alcanza():
    # 4 pasos de ~1000 tokens: quitar los 3 viejos da ~3000; pedir 5000 no se puede.
    assert recortar_entrada(_vuelta(4), 5000) is None


def test_recortar_ignora_lo_que_no_es_una_lista_de_mensajes():
    assert recortar_entrada("hola", 100) is None


class _ModeloQueSeQueja(_Modelo):
    """Falla con 413 mientras la entrada sea larga; contesta cuando se recorta.

    El excedente que declara es FIJO (1500): con pasos de ~1000 tokens, quitar
    uno no alcanza y quitar dos sí, que es justo lo que se quiere ver.
    """

    def __init__(self, nombre: str, tope_mensajes: int, excedente: int = 1500) -> None:
        super().__init__(nombre)
        self.tope_mensajes = tope_mensajes
        self.excedente = excedente
        self.entradas: list[Any] = []

    async def astream(self, entrada: Any, **_: Any) -> Any:
        self.llamadas += 1
        self.entradas.append(entrada)
        if len(entrada) > self.tope_mensajes:
            raise _413(8000 + self.excedente)
        for i in range(2):
            yield f"{self.nombre}:{i}"


async def test_el_413_con_cifras_recorta_y_reintenta_el_mismo_modelo(capsys):
    quejica = _ModeloQueSeQueja("120b", tope_mensajes=6)
    segundo = _Modelo("20b")
    relevo = Relevo([("120b", quejica), ("20b", segundo)], espera_s=0)
    trozos = [t async for t in relevo.astream(_vuelta(4))]  # 10 mensajes > 6
    assert trozos == ["120b:0", "120b:1"]
    # Dos llamadas al MISMO modelo, la segunda más corta; el segundo ni se tocó.
    assert quejica.llamadas == 2 and segundo.llamadas == 0
    assert len(quejica.entradas[1]) < len(quejica.entradas[0])
    assert relevo.actual == "120b"
    assert relevo.recortes_413 == 1
    assert "recorto" in capsys.readouterr().out


async def test_si_el_reintento_tambien_falla_se_rota_como_antes():
    # Tope 1: el recorte deja 4 mensajes, sigue siendo «largo» → el reintento
    # vuelve a dar 413 → rota. Y UN reintento, no un bucle.
    quejica = _ModeloQueSeQueja("120b", tope_mensajes=1)
    segundo = _Modelo("20b")
    relevo = Relevo([("120b", quejica), ("20b", segundo)], espera_s=0)
    trozos = [t async for t in relevo.astream(_vuelta(4))]
    assert trozos == ["20b:0", "20b:1"]
    assert quejica.llamadas == 2  # el original y UN reintento, nunca más
    assert relevo.recortes_413 == 0


async def test_un_413_sin_cifras_rota_sin_reintentar():
    tonto = _Modelo("120b", _Error(413, "Request too large"))
    relevo = Relevo([("120b", tonto), ("20b", _Modelo("20b"))], espera_s=0)
    [t async for t in relevo.astream(_vuelta(4))]
    assert tonto.llamadas == 1
    assert relevo.actual == "20b"


def test_el_margen_cubre_lo_que_el_contador_aproximado_se_deja():
    # Groq cuenta un 5-10 % más que chars//4: sin margen, el recorte justo es un segundo 413.
    assert MARGEN_RECORTE >= 64


def test_el_410_gone_es_permanente():
    """NVIDIA retiró deepseek-v4-flash-0731 el 2026-09-21 y el brazo perdió 15 vueltas
    seguidas porque el 410 no era ni cuota ni caída: subía y no rotaba."""

    class _E(Exception):
        status_code = 410

    texto = (
        "Error code: 410 - {'type': 'about:blank', 'title': 'Gone', 'status': 410, "
        "'detail': \"The model 'deepseek-ai/deepseek-v4-flash-0731' is gone\"}"
    )
    assert tipo_de_agotamiento(_E(texto)) == "permanente"
