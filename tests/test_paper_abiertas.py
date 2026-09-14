"""Lo que el mercado cerró mientras nadie miraba: stops y objetivos de las abiertas.

Sin esto, una posición cuyo stop atravesó el precio de noche seguía abierta
hasta que el modelo la cerrara horas después, a otro precio —y ese R no es el
del stop—. Visto el 2026-09-14 con la primera operación del experimento.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from paper.registro import Contexto, Registro

# `abrir()` fija `abierta_en` en el AHORA real, así que las velas se anclan ahí:
# las de minutos negativos son anteriores a la apertura y deben ignorarse.
T0 = datetime.now(UTC).replace(microsecond=0)


def _ctx(precio: float) -> Contexto:
    return Contexto(precio=precio, timestamp=T0.isoformat(), dia_semana=0, hora_utc=12)


def _vela(minutos: int, *, high: float, low: float) -> dict[str, Any]:
    t = T0 + timedelta(minutes=minutos)
    return {
        "time": int(t.timestamp()),
        "open": low,
        "high": high,
        "low": low,
        "close": low,
        "volume": 1.0,
    }


@pytest.fixture
def registro(tmp_path: Any) -> Registro:
    return Registro(str(tmp_path / "op.db"))


def test_un_short_cuyo_stop_se_toco_se_cierra_al_precio_del_stop_y_a_esa_hora(
    registro: Registro,
) -> None:
    """El caso real: short a 78860.84, stop 79867.31, nadie mirando."""
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(78860.84),
        razon="techo",
        stop_loss=79867.31,
    )
    velas = [
        _vela(-15, high=80500.0, low=78000.0),  # ANTES de entrar: se ignora aunque toque
        _vela(15, high=79500.0, low=78700.0),  # no toca
        _vela(30, high=79950.0, low=79300.0),  # atraviesa el stop
        _vela(45, high=81000.0, low=79900.0),  # peor todavía: no importa, ya cerró
    ]

    cerradas = registro.evaluar_abiertas(velas)

    assert [(c["id"], c["motivo"], c["precio_salida"]) for c in cerradas] == [
        (oid, "stop", 79867.31)
    ]
    assert cerradas[0]["r"] == pytest.approx(-1.0)
    fila = registro._con.execute(
        "SELECT cerrada_en, motivo_cierre, r_multiplo FROM operaciones WHERE id=?", (oid,)
    ).fetchone()
    assert fila["cerrada_en"] == (T0 + timedelta(minutes=30)).isoformat()
    assert fila["motivo_cierre"] == "stop"
    assert registro.abiertas() == []


def test_un_long_que_llego_al_objetivo_se_cierra_por_objetivo(registro: Registro) -> None:
    registro.abrir(
        eje="dip-trap",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=_ctx(100.0),
        razon="dip",
        stop_loss=90.0,
        take_profit=120.0,
    )

    cerradas = registro.evaluar_abiertas([_vela(15, high=121.0, low=99.0)])

    assert cerradas[0]["motivo"] == "objetivo" and cerradas[0]["precio_salida"] == 120.0
    assert cerradas[0]["r"] == pytest.approx(2.0)
    assert registro.abiertas() == []


def test_si_stop_y_objetivo_caen_en_la_misma_vela_gana_el_stop(registro: Registro) -> None:
    """No se sabe cuál tocó primero: la duda se resuelve en contra."""
    registro.abrir(
        eje="dip-trap",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(100.0),
        razon="x",
        stop_loss=110.0,
        take_profit=90.0,
    )

    cerradas = registro.evaluar_abiertas([_vela(15, high=115.0, low=85.0)])

    assert cerradas[0]["motivo"] == "stop"
    assert cerradas[0]["r"] == pytest.approx(-1.0)


def test_el_stop_que_vale_es_el_movido(registro: Registro) -> None:
    oid = registro.abrir(
        eje="zone-reclaim",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=_ctx(100.0),
        razon="x",
        stop_loss=90.0,
    )
    registro.mover_stop(oid, nuevo_stop=98.0, razon="a la entrada")

    cerradas = registro.evaluar_abiertas(
        [_vela(15, high=101.0, low=97.5)]
    )  # no llega a 90, sí a 98

    assert cerradas[0]["precio_salida"] == 98.0
    # El R se mide contra el stop ORIGINAL (riesgo 10), como en cualquier cierre.
    assert cerradas[0]["r"] == pytest.approx(-0.2)


def test_lo_que_no_toco_nada_sigue_abierto(registro: Registro) -> None:
    registro.abrir(
        eje="anti-smc",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=_ctx(100.0),
        razon="x",
        stop_loss=90.0,
        take_profit=120.0,
    )

    assert (
        registro.evaluar_abiertas(
            [_vela(15, high=110.0, low=95.0), _vela(30, high=105.0, low=91.0)]
        )
        == []
    )
    assert len(registro.abiertas()) == 1


def test_sin_velas_posteriores_no_se_cierra_nada(registro: Registro) -> None:
    """Las 200 velas son casi todas pasado; una mecha de ayer no toca un stop de hoy."""
    registro.abrir(
        eje="anti-smc",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(100.0),
        razon="x",
        stop_loss=110.0,
    )

    assert (
        registro.evaluar_abiertas(
            [_vela(-60, high=200.0, low=50.0), _vela(-15, high=150.0, low=90.0)]
        )
        == []
    )
