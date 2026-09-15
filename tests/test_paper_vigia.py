"""El vigía: despierta al modelo por eventos, dentro de una ventana, con tope.

Ver la sección «modo vigía» de CRITERIO_CADENCIA.md. Lo que se prueba es la
cadencia, no el modelo: la vuelta se inyecta como una función que cuenta.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import paper.vigia as vigia
from paper.registro import Contexto, Registro
from paper.vigia import en_ventana, eventos, vigilar

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
        lambda r, prefijo="": {"ordenes": [], "predicciones": [], "cerradas": []},
    )
    monkeypatch.setattr(vigia, "publicar", lambda r: {"cerradas": [], "abiertas": []})
    monkeypatch.setattr(vigia, "publicar_resumen", lambda ruta, brazo: True)
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
    monkeypatch.setattr(
        vigia, "publicar_resumen", lambda ruta, brazo: resumenes.append((ruta, brazo))
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
