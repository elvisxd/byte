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
    monkeypatch.setattr(vigia, "armar", lambda ajustes, db: (Registro(db), object(), "falso"))
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
