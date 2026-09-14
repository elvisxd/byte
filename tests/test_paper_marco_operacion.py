"""El marco de la tesis va en la operación, sellado, y `estado_paper` lo enseña.

La #1 (2026-09-14) se abrió sobre 4h y se cerró a los 17 minutos mirando 15m,
con su invalidación intacta: el marco se perdía entre la herramienta y el
registro, y sin él no había forma de decir «esta tesis lleva 0,07 velas».
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import tools.paper as herramientas
from paper.registro import Contexto, Registro, _sellar
from tools.paper import AbrirArgs, _abrir, _estado


def _ctx(precio: float = 78860.84) -> Contexto:
    return Contexto(
        precio=precio,
        timestamp="2026-09-14T16:53:04+00:00",
        dia_semana=0,
        hora_utc=16,
        extra={"indicadores": {"atr": 683.0}},
    )


def test_sin_marco_el_sello_es_el_de_antes(tmp_path: Any) -> None:
    """Las operaciones selladas antes del marco tienen que seguir verificando."""
    r = Registro(str(tmp_path / "op.db"))
    oid = r.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="techo",
        stop_loss=79867.31,
    )
    fila = r._con.execute("SELECT * FROM operaciones WHERE id=?", (oid,)).fetchone()

    viejo = _sellar(
        _ctx(),
        "techo",
        "range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        stop_loss=79867.31,
        take_profit=None,
    )
    assert fila["sello"] == viejo
    assert fila["temporalidad"] is None
    assert r.verificar_sellos() == []


def test_con_marco_entra_al_sello_y_editarlo_lo_rompe(tmp_path: Any) -> None:
    r = Registro(str(tmp_path / "op.db"))
    oid = r.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="techo",
        stop_loss=79867.31,
        temporalidad="4h",
    )
    assert (
        r._con.execute("SELECT temporalidad FROM operaciones WHERE id=?", (oid,)).fetchone()[0]
        == "4h"
    )
    assert r.verificar_sellos() == []

    r._con.execute("UPDATE operaciones SET temporalidad='15m' WHERE id=?", (oid,))
    r._con.commit()

    assert r.verificar_sellos() == [oid]


def test_la_herramienta_pasa_el_intervalo_al_registro(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = int(datetime(2026, 9, 14, 16, 0, tzinfo=UTC).timestamp())
    velas = {
        "fuente": "prueba",
        "velas": [
            {
                "time": base + i * 900,
                "open": 78860.84,
                "high": 78900.0,
                "low": 78800.0,
                "close": 78860.84,
                "volume": 1.0,
            }
            for i in range(200)
        ],
    }
    monkeypatch.setattr(herramientas, "velas", lambda s, marco, n: velas)
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: {"atr": 683.0})
    r = Registro(str(tmp_path / "op.db"))

    res = _abrir(
        r,
        AbrirArgs(
            eje="range-sweep", direccion="short", stop_loss=79867.31, razon="techo", intervalo="4h"
        ),
    )

    assert res.ok
    assert r.abiertas()[0]["temporalidad"] == "4h"


def test_estado_enseña_marco_edad_en_velas_e_invalidacion(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = Registro(str(tmp_path / "op.db"))
    oid = r.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="techo",
        stop_loss=79867.31,
        temporalidad="4h",
    )
    # abierta hace 17 minutos
    hace = (datetime.now(UTC) - timedelta(minutes=17)).isoformat()
    r._con.execute("UPDATE operaciones SET abierta_en=? WHERE id=?", (hace, oid))
    r._con.commit()
    monkeypatch.setattr(herramientas, "_precio_ahora", lambda: 78803.28)

    texto = _estado(r).content

    assert "tesis de 4h · abierta hace 17 min (0.1 velas de 4h)" in texto
    assert "invalida en 79867.31 (a 1064)" in texto
    assert "+0.06R a favor" in texto


def test_sin_marco_el_estado_no_inventa_velas(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = Registro(str(tmp_path / "op.db"))
    r.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="techo",
        stop_loss=79867.31,
    )
    monkeypatch.setattr(herramientas, "_precio_ahora", lambda: None)

    texto = _estado(r).content

    assert "velas de" not in texto
    assert "abierta hace" in texto
    assert (
        "invalida en 79867.31" in texto and "(a " not in texto.split("invalida en")[1].split("·")[0]
    )
