"""El vigía: despierta al modelo por eventos, dentro de una ventana, con tope.

Ver la sección «modo vigía» de CRITERIO_CADENCIA.md. Lo que se prueba es la
cadencia, no el modelo: la vuelta se inyecta como una función que cuenta.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import paper.vigia as vigia
from paper.registro import Contexto, Registro
from paper.vigia import en_ventana, eventos, hora_del_dia, vigilar

T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_la_ventana_es_de_08_a_2030_local() -> None:
    assert en_ventana(datetime(2026, 9, 15, 8, 0))
    assert en_ventana(datetime(2026, 9, 15, 20, 30))
    assert not en_ventana(datetime(2026, 9, 15, 7, 59))
    assert not en_ventana(datetime(2026, 9, 15, 20, 31))
    assert not en_ventana(datetime(2026, 9, 15, 3, 0))


@pytest.fixture
def registro(tmp_path: Any) -> Registro:
    return Registro(str(tmp_path / "op.db"))


def _ctx(precio: float, atr: float = 200.0) -> Contexto:
    return Contexto(
        precio=precio,
        timestamp=T0.isoformat(),
        dia_semana=1,
        hora_utc=12,
        extra={"indicadores": {"atr": atr}},
    )


def test_sin_nada_vivo_ni_cierre_no_hay_motivo(registro: Registro) -> None:
    assert (
        eventos(registro, precio=79000, atr_15m=200, pools=[], cierre_4h=100, cierre_4h_visto=100)
        == []
    )


def test_un_cierre_de_4h_nuevo_es_motivo(registro: Registro) -> None:
    m = eventos(registro, precio=79000, atr_15m=200, pools=[], cierre_4h=200, cierre_4h_visto=100)
    assert len(m) == 1 and m[0].startswith("cerró la vela de 4h")


def test_el_primer_cierre_visto_no_dispara(registro: Registro) -> None:
    """Al arrancar no se sabe si la vela ya se leyó: el arranque tiene su propia vuelta."""
    assert (
        eventos(registro, precio=79000, atr_15m=200, pools=[], cierre_4h=200, cierre_4h_visto=None)
        == []
    )


def test_el_precio_cerca_de_un_nivel_vivo_es_motivo(registro: Registro) -> None:
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=_ctx(79000),
        nivel=77500,
        hacia="abajo",
        probabilidad=0.5,
        razonamiento="x",
        temporalidad="1h",
    )
    registro.dejar_orden(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=_ctx(79000),
        razon="x",
        precio_limite=76100,
        stop_loss=75500,
    )

    lejos = eventos(registro, precio=79000, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1)
    cerca_pred = eventos(
        registro, precio=77650, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1
    )
    cerca_orden = eventos(
        registro, precio=76250, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1
    )

    assert lejos == []
    assert any("predicción #1" in m for m in cerca_pred)
    assert any("orden #1" in m for m in cerca_orden)


def test_un_nivel_despierta_una_vez_mientras_el_precio_siga_cerca(registro: Registro) -> None:
    """Medido: tres vueltas en 40 min por la misma predicción. Histéresis."""
    # A 2,5 ATR del precio: el registro rechaza niveles a menos de 1,5.
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=_ctx(79000),
        nivel=79500,
        hacia="arriba",
        probabilidad=0.5,
        razonamiento="x",
        horas_vigencia=6,
        temporalidad="1h",
    )
    avisadas: set[str] = set()

    def motivos(precio: float) -> list[str]:
        return eventos(
            registro,
            precio=precio,
            atr_15m=200,
            pools=[],
            cierre_4h=1,
            cierre_4h_visto=1,
            cercanias_avisadas=avisadas,
        )

    assert motivos(79450), "al entrar en el margen (1 ATR = 200), avisa"
    assert motivos(79480) == [], "sigue cerca: no vuelve a avisar"
    assert motivos(79490) == [], "y tampoco al siguiente sondeo"
    assert motivos(78500) == [], "se alejó: nada que avisar, pero se rearma"
    assert motivos(79520), "volvió: avisa otra vez"
    # Sin el conjunto, el comportamiento de siempre (los tests viejos).
    assert eventos(registro, precio=79450, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1)


def test_un_pool_con_fuerza_cuenta_y_uno_suelto_no(registro: Registro) -> None:
    pools = [{"precio": 79100, "fuerza": 1}, {"precio": 79150, "fuerza": 2}]
    m = eventos(registro, precio=79000, atr_15m=200, pools=pools, cierre_4h=1, cierre_4h_visto=1)
    assert len(m) == 1 and "79150" in m[0]


def test_una_abierta_cerca_de_su_stop_es_motivo(registro: Registro) -> None:
    registro.abrir(
        eje="dip-trap",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=_ctx(79000),
        razon="x",
        stop_loss=78000,
        take_profit=81000,
    )
    assert (
        eventos(registro, precio=79000, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1) == []
    )
    assert any(
        "de su stop" in m
        for m in eventos(
            registro, precio=78150, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1
        )
    )
    assert any(
        "de su objetivo" in m
        for m in eventos(
            registro, precio=80900, atr_15m=200, pools=[], cierre_4h=1, cierre_4h_visto=1
        )
    )


# ── el bucle, con reloj, red y modelo falsos ──────────────────────────────


class _Mundo:
    """Un mercado que cambia a voluntad del test."""

    def __init__(self) -> None:
        self.precio = 79000.0
        self.cierre_4h = 1_000
        self.hora = datetime(2026, 9, 15, 9, 0).astimezone()

    def velas(self, _s: str, marco: str, n: int) -> dict[str, Any]:
        base = int(T0.timestamp())
        if marco == "4h":
            return {
                "fuente": "prueba",
                "velas": [
                    {
                        "time": self.cierre_4h - 14400,
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "volume": 1,
                    },
                    {
                        "time": self.cierre_4h,
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "volume": 1,
                    },
                    {
                        "time": self.cierre_4h + 14400,
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "volume": 1,
                    },
                ],
            }
        return {
            "fuente": "prueba",
            "velas": [
                {
                    "time": base + i * 900,
                    "open": self.precio,
                    "high": self.precio + 10,
                    "low": self.precio - 10,
                    "close": self.precio,
                    "volume": 1.0,
                }
                for i in range(200)
            ],
        }


@pytest.fixture
def mundo(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> _Mundo:
    m = _Mundo()
    monkeypatch.setattr(vigia, "velas_del_mercado", m.velas)
    monkeypatch.setattr(vigia, "indicadores", lambda v, cuales: {"atr": 200.0, "liquidity": []})
    monkeypatch.setattr(
        vigia,
        "poner_al_dia",
        lambda r, prefijo="", velas15=None: {"ordenes": [], "predicciones": [], "cerradas": []},
    )
    monkeypatch.setattr(vigia, "publicar", lambda r: {"cerradas": [], "abiertas": []})
    monkeypatch.setattr(vigia, "publicar_resumen", lambda ruta, brazo, **_: True)
    monkeypatch.setattr(
        vigia, "armar", lambda ajustes, db, modelos=None: (Registro(db), object(), "falso")
    )
    monkeypatch.setattr(vigia.TraceDeSesion, "publicar", lambda self, viva=True: True)
    return m


async def _nada(_: float) -> None:
    return None


async def test_arranca_con_una_vuelta_y_luego_solo_por_eventos(
    mundo: _Mundo, tmp_path: Any
) -> None:
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: mundo.hora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=4,
    )

    assert vueltas == [1]  # el arranque; los otros tres sondeos, sin evento, no despiertan


async def test_el_arranque_espera_a_la_ventana_en_vez_de_perderse(
    mundo: _Mundo, tmp_path: Any
) -> None:
    """Lanzado de madrugada, la vuelta de lectura es la primera de la ventana."""
    mundo.hora = datetime(2026, 9, 15, 0, 54).astimezone()
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    async def dormir(_: float) -> None:
        mundo.hora = datetime(2026, 9, 15, 8, 2).astimezone()

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: mundo.hora,
        dormir=dormir,
        correr_vuelta=vuelta,
        ticks=3,
    )

    assert vueltas == [1], "una sola vuelta de lectura, y dentro de la ventana"


async def test_un_cierre_de_4h_despierta_dentro_de_la_ventana(mundo: _Mundo, tmp_path: Any) -> None:
    vueltas: list[int] = []
    tick = {"n": 0}

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    def ahora() -> datetime:
        tick["n"] += 1
        if tick["n"] == 3:
            mundo.cierre_4h += 14400  # cerró otra vela de 4h
        return mundo.hora

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=4,
        primera_vuelta_al_arrancar=False,
    )

    assert vueltas == [1]


async def test_una_vuelta_perdida_por_cuota_corta_se_reintenta_y_no_gasta_tope(
    mundo: _Mundo, tmp_path: Any
) -> None:
    """Medido el 2026-09-15 a las 20:05: Groq perdió el cierre de 4h por «todos
    agotados… vuelve en 1 min» y el motivo no volvió, porque `cierre_4h_visto`
    ya se había anotado."""
    tick = {"n": 0}

    async def vuelta(n: int) -> str | None:
        # La primera falla por cuota corta; la siguiente va bien.
        if n == 1:
            return "todos los modelos están agotados (a, b); el primero vuelve en 1 min"
        return None

    def ahora() -> datetime:
        tick["n"] += 1
        if tick["n"] == 2:
            mundo.cierre_4h += 14400
        return mundo.hora

    resultado = await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=4,
        primera_vuelta_al_arrancar=False,
    )

    # Dos vueltas: la que falló por cuota y su reintento en el tick siguiente.
    assert resultado["vueltas"] == 2
    # Y el día cuenta UNA, no dos: la perdida no gasta tope.
    assert list(resultado["por_dia"].values()) == [1]


async def test_el_arranque_solo_no_gasta_tope_diario(mundo: _Mundo, tmp_path: Any) -> None:
    """Medido: 6 de 13 vueltas de un día fueron arranques por relanzar el
    proceso, y cada una se cobraba contra las ocho del día."""

    async def vuelta(_n: int) -> str | None:
        return None

    resultado = await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: mundo.hora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=1,
        primera_vuelta_al_arrancar=True,
    )

    assert resultado["vueltas"] == 1, "el arranque hace su vuelta"
    assert resultado["por_dia"] == {}, "pero no cuenta contra el tope"


def test_el_sondeo_se_alinea_a_la_rejilla_del_cuarto_de_hora() -> None:
    """Medido: los cierres de 16:00 y 20:00 se vieron a las 16:04 y 20:08."""
    from paper.vigia import MARGEN_REJILLA_S, _hasta_la_rejilla

    # 12:07:00 → faltan 8 min para las 12:15, más el margen.
    t = datetime(2026, 9, 16, 12, 7, 0, tzinfo=UTC).timestamp()
    assert _hasta_la_rejilla(t, 900) == 8 * 60 + MARGEN_REJILLA_S
    # Justo en la rejilla: se espera la vuelta entera, no cero.
    t = datetime(2026, 9, 16, 12, 15, 0, tzinfo=UTC).timestamp()
    assert _hasta_la_rejilla(t, 900) == 900 + MARGEN_REJILLA_S


def test_los_minutos_del_relevo_se_leen_del_error_y_solo_de_ese_error() -> None:
    from paper.vigia import _minutos_hasta_que_vuelva

    assert _minutos_hasta_que_vuelva("todos los modelos están agotados (a); vuelve en 1 min") == 1
    assert (
        _minutos_hasta_que_vuelva("todos ... agotados (a, b); el primero vuelve en 566 min") == 566
    )
    assert _minutos_hasta_que_vuelva("fetch failed") is None, "un fallo de red no es cuota"
    assert _minutos_hasta_que_vuelva("agotados pero sin minutos") is None


async def test_sin_vuelta_de_arranque_si_el_registro_acaba_de_escribir(
    mundo: _Mundo, tmp_path: Any
) -> None:
    """Relanzar por un cambio de código no debe releer lo que se acaba de leer."""
    ruta = str(tmp_path / "op.db")
    registro = Registro(ruta)
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=_ctx(79000),
        nivel=79500,
        hacia="arriba",
        probabilidad=0.5,
        razonamiento="x",
        horas_vigencia=6,
        temporalidad="1h",
    )
    # Un minuto después de lo que se acaba de escribir: es «hace un momento»
    # corra la suite a la hora que corra. Se lee antes de cerrar la conexión.
    escrito = registro.ultima_escritura()
    registro.cerrar_conexion()
    assert escrito is not None
    reloj_de_prueba = escrito + timedelta(minutes=1)
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    await vigilar(
        ruta_db=ruta,
        ruta_scripts="",
        # Las 09:00 ponen al vigía DENTRO de la ventana (08:00-20:30): fuera
        # de ella no hay vuelta por otro motivo y el test pasaría sin ejercer
        # la guarda. Pero la hora tiene que salir de la misma referencia que la
        # escritura de arriba, que usa el reloj real: con `datetime.now()`
        # crudo, correr la suite de madrugada dejaba la escritura siete horas
        # «en el futuro» —más que RECIENTE_S— y el arranque se hacía igual.
        ahora=lambda: reloj_de_prueba,
        # Ventana abierta todo el día en vez de fijar el reloj a las 09:00: lo
        # que este test aísla es la guarda de escritura reciente, y fuera de la
        # ventana no habría vuelta por otro motivo —pasaría sin ejercerla—.
        # Fijar la hora rompía la distancia con la escritura, que sale del
        # reloj real: corriendo la suite de madrugada quedaban siete horas.
        ventana=(hora_del_dia(0, 0), hora_del_dia(23, 59)),
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=1,
        primera_vuelta_al_arrancar=True,
    )

    assert vueltas == [], "la predicción es de hace un momento: el arranque sobra"


async def test_el_primero_de_la_lista_se_reserva_para_los_cierres_de_4h(
    mundo: _Mundo, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paper/CRITERIO_HORARIOS.md: la lectura de arranque va con reserva (del
    segundo en adelante); el cierre de 4h, sin ella (abre el primero)."""
    reservas: list[bool] = []

    def etiqueta() -> str:
        return "g"

    etiqueta.reservar_primero = reservas.append  # type: ignore[attr-defined]
    monkeypatch.setattr(
        vigia, "armar", lambda ajustes, db, modelos=None: (Registro(db), object(), etiqueta)
    )
    tick = {"n": 0}

    def ahora() -> datetime:
        tick["n"] += 1
        if tick["n"] == 3:
            mundo.cierre_4h += 14400
        return mundo.hora

    async def vuelta(_n: int) -> str | None:
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=4,
        modelos=["gemini-3.8-flash", "gemini-3.5-flash-lite"],
        publicar_al_panel=False,
        archivo_traza=str(tmp_path / "t.json"),
        brazo="gemini",
    )

    # Vuelta 1: arranque (gestión) → reservado. Vuelta 2: cierre de 4h → libre.
    assert reservas == [True, False]


async def test_el_local_sin_relevo_no_tiene_nada_que_reservar(mundo: _Mundo, tmp_path: Any) -> None:
    """La etiqueta del local es una cadena: el vigía no le pide nada."""
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: mundo.hora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=2,
    )
    assert vueltas == [1]


async def test_fuera_de_la_ventana_no_despierta_aunque_haya_motivo(
    mundo: _Mundo, tmp_path: Any
) -> None:
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    mundo.hora = datetime(2026, 9, 15, 23, 0).astimezone()
    tick = {"n": 0}

    def ahora() -> datetime:
        tick["n"] += 1
        if tick["n"] == 2:
            mundo.cierre_4h += 14400
        return mundo.hora

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=3,
    )

    assert vueltas == []  # ni el arranque ni el cierre: es de noche


async def test_el_tope_diario_para_las_vueltas(mundo: _Mundo, tmp_path: Any) -> None:
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    def ahora() -> datetime:
        mundo.cierre_4h += 14400  # cada sondeo, un cierre nuevo: motivo siempre
        return mundo.hora

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=12,
        tope_diario=3,
        primera_vuelta_al_arrancar=False,
    )

    assert vueltas == [1, 2, 3]


async def test_el_tope_es_por_dia_local(mundo: _Mundo, tmp_path: Any) -> None:
    vueltas: list[int] = []

    async def vuelta(n: int) -> str | None:
        vueltas.append(n)
        return None

    tick = {"n": 0}

    def ahora() -> datetime:
        tick["n"] += 1
        mundo.cierre_4h += 14400
        # los tres primeros sondeos, un día; los tres siguientes, el siguiente
        return mundo.hora + timedelta(days=1 if tick["n"] > 3 else 0)

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=6,
        tope_diario=2,
        primera_vuelta_al_arrancar=False,
    )

    assert vueltas == [1, 2, 3, 4]


async def test_la_parada_cancela_la_vuelta_en_curso(mundo: _Mundo, tmp_path: Any) -> None:
    """Un TERM en mitad de una vuelta no espera a que el modelo termine: la corta.
    Medido el 2026-09-14: esperar dejó Ollama ocupado y el relevo no arrancó."""
    import asyncio
    import os
    import signal

    empezo = asyncio.Event()

    async def vuelta_larga(n: int) -> str | None:
        empezo.set()
        await asyncio.sleep(3600)  # el modelo «pensando»
        return None

    async def mandar_term() -> None:
        await empezo.wait()
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(mandar_term()))
    resumen = await asyncio.wait_for(
        vigilar(
            ruta_db=str(tmp_path / "op.db"),
            ruta_scripts="",
            ahora=lambda: mundo.hora,
            dormir=_nada,
            correr_vuelta=vuelta_larga,
        ),
        timeout=10,
    )

    assert resumen["vueltas"] == 1  # arrancó una, se cortó, y el bucle terminó


async def test_al_cerrarse_la_ventana_avisa_una_vez(mundo: _Mundo, tmp_path: Any) -> None:
    """«Ya puedes apagar la Mac»: en la transición dentro → fuera, y solo ahí."""
    avisos: list[str] = []
    tick = {"n": 0}

    def ahora() -> datetime:
        tick["n"] += 1
        # dos sondeos dentro (20:00, 20:15), tres fuera (20:45, 21:00, 21:15)
        return datetime(2026, 9, 15, 20, 0).astimezone() + timedelta(
            minutes=[0, 15, 45, 60, 75][tick["n"] - 1]
        )

    async def sin_vuelta(n: int) -> str | None:
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=sin_vuelta,
        ticks=5,
        primera_vuelta_al_arrancar=False,
        avisar=lambda texto: (avisos.append(texto), True)[1],
    )

    assert len(avisos) == 1
    assert avisos[0].startswith("Vigía en reposo hasta las 08:00: la Mac se duerme sola")


async def test_pide_estar_despierta_solo_dentro_de_la_ventana(mundo: _Mundo, tmp_path: Any) -> None:
    """Dentro de la ventana la Mac no se duerme; fuera, sí; y al parar se suelta siempre."""
    pedidos: list[bool] = []
    horas = iter(
        [
            datetime(2026, 9, 15, 9, 0).astimezone(),
            datetime(2026, 9, 15, 20, 45).astimezone(),
            datetime(2026, 9, 15, 20, 50).astimezone(),
        ]
    )

    async def sin_vuelta(_n: int) -> str | None:
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: next(horas),
        dormir=_nada,
        correr_vuelta=sin_vuelta,
        ticks=3,
        avisar=lambda _t: True,
        mantener_despierta=pedidos.append,
    )

    assert pedidos == [True, False, False, False], "tres sondeos y la soltada final"


def test_el_caffeinate_se_pide_diez_minutos_antes_de_la_ventana() -> None:
    """Medido el 2026-09-16: la Mac despertó a las 07:55 y a las 07:56 volvió a
    dormirse, porque el vigía solo sostenía dentro de la ventana (08:00)."""
    from paper.vigia import hay_que_sostener

    h = lambda hh, mm: datetime(2026, 9, 16, hh, mm)  # noqa: E731
    assert not hay_que_sostener(h(7, 49))
    assert hay_que_sostener(h(7, 50))
    assert hay_que_sostener(h(7, 55)), "el despertar programado cae dentro del margen"
    assert hay_que_sostener(h(8, 0)) and hay_que_sostener(h(20, 30))
    assert not hay_que_sostener(h(20, 31))


async def test_al_despertar_la_mac_pide_caffeinate_en_segundos_y_no_al_tick_siguiente(
    mundo: _Mundo, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La Mac duerme once horas a mitad de un sueño de 15 min. Al despertar, el
    reloj de pared saltó: se reevalúa el caffeinate en ese mismo trozo de 5 s
    —antes del minuto de inactividad que la duerme otra vez— y el tick se hace ya."""
    import paper.vigia as vigia_mod

    pared = {"t": 1_000_000.0}
    monkeypatch.setattr(vigia_mod.time, "time", lambda: pared["t"])
    saltos = {"n": 0}

    async def dormir_con_sueno_del_sistema(s: float) -> None:
        saltos["n"] += 1
        # El primer trozo del sueño: la Mac se duerme y despierta once horas después.
        pared["t"] += s + (11 * 3600 if saltos["n"] == 1 else 0)

    horas = iter(
        [
            datetime(2026, 9, 15, 20, 40).astimezone(),  # tick 1: fuera de la ventana
            datetime(2026, 9, 16, 7, 55).astimezone(),  # al notar el salto
            datetime(2026, 9, 16, 7, 55).astimezone(),  # tick 2, adelantado
        ]
    )
    pedidos: list[bool] = []

    async def sin_vuelta(_n: int) -> str | None:
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: next(horas),
        dormir=dormir_con_sueno_del_sistema,
        correr_vuelta=sin_vuelta,
        ticks=2,
        primera_vuelta_al_arrancar=False,
        avisar=lambda _t: True,
        mantener_despierta=pedidos.append,
    )

    # Tick 1 fuera → False. Salto → True en el acto. Tick 2 a las 07:55 → True.
    # Al parar → False.
    assert pedidos == [False, True, True, False], pedidos
    # El tick 2 llegó tras UN trozo de 5 s, no tras el sueño entero (que con la
    # rejilla son hasta 180 trozos): el salto lo cortó en el primero.
    assert saltos["n"] < 1 + 180, f"el salto tenía que cortar el sueño: {saltos['n']} trozos"


async def test_si_arranca_ya_fuera_de_la_ventana_no_avisa(mundo: _Mundo, tmp_path: Any) -> None:
    avisos: list[str] = []
    mundo.hora = datetime(2026, 9, 15, 22, 0).astimezone()

    async def sin_vuelta(n: int) -> str | None:
        return None

    await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=lambda: mundo.hora,
        dormir=_nada,
        correr_vuelta=sin_vuelta,
        ticks=3,
        primera_vuelta_al_arrancar=False,
        avisar=lambda texto: (avisos.append(texto), True)[1],
    )

    assert avisos == []


async def test_el_brazo_remoto_no_publica_al_panel(
    mundo: _Mundo, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El panel enseña UN historial: el brazo remoto escribe su traza en disco y nada más."""
    publicaciones: list[Any] = []
    monkeypatch.setattr(vigia, "publicar", lambda r: publicaciones.append(r))
    resumenes: list[tuple[str, str]] = []
    partes: list[Any] = []
    monkeypatch.setattr(
        vigia,
        "publicar_resumen",
        lambda ruta, brazo, estado_modelos=None, **_: (
            resumenes.append((ruta, brazo)),
            partes.append(estado_modelos),
        ),
    )
    recibidos: list[Any] = []
    monkeypatch.setattr(
        vigia,
        "armar",
        lambda ajustes, db, modelos=None: (recibidos.append(modelos), Registro(db), object(), "g")[
            1:
        ],
    )
    archivo = tmp_path / "trazas" / "g.json"
    # El fixture `mundo` ya reemplazó `publicar` del trace por un doble, así que
    # lo que se mira es con qué se construyó: a qué archivo y con qué modelo.
    construido: dict[str, Any] = {}

    class _Trace(vigia.TraceDeSesion):
        def __init__(self, **kw: Any) -> None:
            construido.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(vigia, "TraceDeSesion", _Trace)

    async def vuelta(_n: int) -> str | None:
        return None

    await vigilar(
        ruta_db=str(tmp_path / "g.db"),
        ruta_scripts="",
        ahora=lambda: mundo.hora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=1,
        modelos=["gemini-3.8-flash"],
        publicar_al_panel=False,
        archivo_traza=str(archivo),
        brazo="gemini",
    )

    assert recibidos == [["gemini-3.8-flash"]]
    assert publicaciones == []
    # El resumen sí se publica, con el nombre del brazo: es lo que compara la página.
    assert resumenes and all(b == "gemini" for _, b in resumenes)
    # Y con él viaja el parte de las APIs, que cuelga de la etiqueta del relevo
    # (paper/sesion.py). Acá `armar` está doblado y devuelve una cadena, así que
    # no hay parte que mandar: lo que se prueba es que se pida sin reventar.
    assert partes and all(p is None for p in partes)
    assert construido["archivo"] == str(archivo)
    assert construido["modelo"] == "g"


def test_un_brazo_sin_publicar_no_avisa_por_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tres vigías, un solo «la Mac se duerme»: el aviso es del brazo que publica."""
    import sys

    recibido: dict[str, Any] = {}

    async def vigilar_falso(**kw: Any) -> dict[str, Any]:
        recibido.update(kw)
        return {"vueltas": 0, "por_dia": {}}

    monkeypatch.setattr(vigia, "vigilar", vigilar_falso)
    monkeypatch.setattr(
        sys,
        "argv",
        ["vigia", "--modelo", "gemini-3.8-flash", "--sin-publicar", "--brazo", "gemini"],
    )
    vigia.main()
    assert recibido["brazo"] == "gemini" and recibido["publicar_al_panel"] is False
    assert recibido["avisar"]("x") is False

    monkeypatch.setattr(sys, "argv", ["vigia"])
    vigia.main()
    assert recibido["brazo"] == "local" and recibido["avisar"] is vigia.avisar_por_telegram


async def test_una_vuelta_que_falla_sin_escribir_no_gasta_tope(
    mundo: _Mundo, tmp_path: Any
) -> None:
    """Medido el 2026-09-22: el brazo nvidia gastó sus ocho vueltas en ocho 410 de un
    modelo retirado, gemini las suyas en 503, y a las 20:00 los cuatro brazos dijeron
    «tope diario alcanzado» ante el cierre de 4h. Una vuelta que murió antes de la
    primera escritura no gastó nada de lo que el tope acota."""

    async def vuelta(n: int) -> str | None:
        # Falla siempre, y con un error que NO dice «vuelve en N min»: ni cuota
        # corta ni reintento. Es el 410 de NVIDIA o un 503 a secas.
        return "Error code: 410 - {'title': 'Gone', 'status': 410}"

    def ahora() -> datetime:
        mundo.cierre_4h += 14400  # un cierre de 4h en cada sondeo: motivo siempre
        return mundo.hora

    resultado = await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=3,
        primera_vuelta_al_arrancar=False,
    )
    # Se intentó más de una vez…
    assert resultado["vueltas"] >= 2
    # …y ninguna cuenta contra el tope: no escribieron nada.
    assert list(resultado["por_dia"].values()) in ([0], [])


async def test_una_vuelta_que_escribio_antes_de_fallar_si_gasta_tope(
    mundo: _Mundo, tmp_path: Any
) -> None:
    """La contraparte: si llegó a escribir antes de morir, ya produjo muestra y cuenta.
    Medido el 2026-09-18 a las 20:01: gemini escribió las #58 y #59 y después la
    vuelta murió por 503."""
    from paper.registro import Registro

    registro = Registro(str(tmp_path / "op.db"), modelo="t")

    async def vuelta(n: int) -> str | None:
        registro.predecir(
            simbolo="BTCUSDT",
            nivel=80500.0,
            hacia="arriba",
            probabilidad=0.4,
            temporalidad="1h",
            contexto=_ctx(80000.0),
            razonamiento="r",
        )
        return "503 UNAVAILABLE"

    def ahora() -> datetime:
        mundo.cierre_4h += 14400
        return mundo.hora

    resultado = await vigilar(
        ruta_db=str(tmp_path / "op.db"),
        ruta_scripts="",
        ahora=ahora,
        dormir=_nada,
        correr_vuelta=vuelta,
        ticks=2,
        primera_vuelta_al_arrancar=False,
    )
    assert resultado["vueltas"] == 2
    assert list(resultado["por_dia"].values()) == [2]
