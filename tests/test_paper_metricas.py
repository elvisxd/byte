"""Métricas que se le dan hechas al modelo porque las calculaba mal (paper/TRAMPAS.md).

No son filtros: no deciden nada. Son el mismo gesto que darle el ATR en vez de
las 200 velas. Trampa 1: leyó la ganancia de un short al revés. Trampa 2: dijo
«techo» a un 1,2 % por debajo y «piso» al 73 % del recorrido.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

import tools.paper as herramientas
from paper.registro import Contexto, Registro
from tools.paper import MirarArgs, _estado, _mirar

PISO, TECHO = 76077.62, 79827.4


def _velas(cierre: float) -> dict[str, Any]:
    base = int(datetime(2026, 9, 14, 12, 0, tzinfo=UTC).timestamp())
    velas = [
        {
            "time": base + i * 900,
            "open": cierre,
            "high": TECHO,
            "low": PISO,
            "close": cierre,
            "volume": 10.0,
        }
        for i in range(200)
    ]
    return {"fuente": "prueba", "velas": velas}


@pytest.fixture
def mercado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(herramientas, "velas", lambda simbolo, marco, n: _velas(78803.28))
    monkeypatch.setattr(herramientas, "indicadores", lambda velas, cuales: {"atr": 650.0})


def _abrir(registro: Registro, direccion: str, entrada: float, stop: float) -> int:
    ctx = Contexto(precio=entrada, timestamp="2026-09-14T12:00:00+00:00", dia_semana=0, hora_utc=12)
    return registro.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion=direccion,
        contexto=ctx,
        razon="prueba",
        stop_loss=stop,
    )


def test_un_short_con_el_precio_abajo_va_a_favor(tmp_path: Any, mercado: None) -> None:
    """El caso real: short a 78860.84, precio 78803.28 → +0.06R, a favor. El
    modelo dijo «en pérdida»."""
    r = Registro(str(tmp_path / "op.db"))
    _abrir(r, "short", 78860.84, 79867.31)

    texto = _estado(r).content

    assert "+0.06R a favor (precio 78803.28)" in texto


def test_un_long_con_el_precio_abajo_va_en_contra(tmp_path: Any, mercado: None) -> None:
    r = Registro(str(tmp_path / "op.db"))
    _abrir(r, "long", 78860.84, 77860.84)  # riesgo 1000

    texto = _estado(r).content

    assert "-0.06R en contra (precio 78803.28)" in texto


def test_sin_mercado_la_lista_sale_como_antes(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paper.mercado import MercadoNoDisponible

    def caido(*_: Any, **__: Any) -> dict[str, Any]:
        raise MercadoNoDisponible("sin red")

    monkeypatch.setattr(herramientas, "velas", caido)
    r = Registro(str(tmp_path / "op.db"))
    _abrir(r, "short", 78860.84, 79867.31)

    texto = _estado(r).content

    assert "#1 short BTCUSDT a 78860.84 (stop 79867.31) · eje" in texto
    assert "R " not in texto.split("Cómo va")[0]


def test_mirar_mercado_dice_en_que_porcentaje_del_rango_esta_el_precio(mercado: None) -> None:
    """78803.28 entre 76077.62 y 79827.4 es el 73 %: cerca del techo, no «en el piso»."""
    texto = _mirar(MirarArgs(simbolo="BTCUSDT", intervalo="4h"), 4000).content

    assert "al 73% del rango: 0% es el piso, 100% el techo" in texto
