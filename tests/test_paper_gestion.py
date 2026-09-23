"""Gestión de abiertas (CRITERIO_GESTION.md).

Plazo que despierta, objetivo obligatorio, motivo tiempo.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import paper.vigia as vigia
import tools.paper as herramientas
from paper.registro import Contexto, Registro
from tools.paper import AbrirArgs, OrdenArgs, _abrir, _dejar_orden, _estado


def _ctx(precio: float = 78860.84) -> Contexto:
    return Contexto(
        precio=precio,
        timestamp="2026-09-14T16:53:04+00:00",
        dia_semana=0,
        hora_utc=16,
        extra={"indicadores": {"atr": 683.0}},
    )


def _abierta_hace(r: Registro, horas: float, marco: str = "4h") -> int:
    oid = r.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="techo",
        stop_loss=79867.31,
        take_profit=77000.0,
        temporalidad=marco,
    )
    hace = (datetime.now(UTC) - timedelta(hours=horas)).isoformat()
    r._con.execute("UPDATE operaciones SET abierta_en=? WHERE id=?", (hace, oid))
    r._con.commit()
    return oid


def test_tiempo_solo_lo_es_si_el_plazo_se_agoto(tmp_path: Any) -> None:
    """Por lo mismo que un «stop» solo lo es si el precio llegó al stop."""
    r = Registro(str(tmp_path / "op.db"))
    joven = _abierta_hace(r, horas=2)  # 4h: plazo 96 h
    vieja = _abierta_hace(r, horas=100)

    r.cerrar(joven, precio_salida=78700.0, motivo="tiempo")
    r.cerrar(vieja, precio_salida=78700.0, motivo="tiempo")

    motivos = dict(r._con.execute("SELECT id, motivo_cierre FROM operaciones").fetchall())
    assert motivos == {joven: "manual", vieja: "tiempo"}


def test_sin_marco_no_hay_plazo(tmp_path: Any) -> None:
    r = Registro(str(tmp_path / "op.db"))
    oid = r.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="vieja, sin marco",
        stop_loss=79867.31,
    )
    assert r.plazo_de(r.abiertas()[0]) is None
    assert not r.agoto_plazo(r.abiertas()[0])
    r.cerrar(oid, precio_salida=78700.0, motivo="tiempo")
    assert r._con.execute("SELECT motivo_cierre FROM operaciones").fetchone()[0] == "manual"


@pytest.fixture
def mercado(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_abrir_sin_objetivo_se_rechaza_y_lo_explica(tmp_path: Any, mercado: None) -> None:
    r = Registro(str(tmp_path / "op.db"))
    res = _abrir(
        r,
        AbrirArgs(
            eje="range-sweep",
            direccion="short",
            stop_loss=79867.31,
            razon="techo",
            ejes=(
                "range-sweep: sí; zone-reclaim: no; cvd-divergence: no; dip-trap: no; anti-smc: no"
            ),
            en_contra="prueba",
        ),
    )

    assert res.ok is False
    assert "sin objetivo" in res.content
    assert r.abiertas() == []

    ok = _abrir(
        r,
        AbrirArgs(
            eje="range-sweep",
            direccion="short",
            stop_loss=79867.31,
            take_profit=77000.0,
            razon="techo",
            ejes=(
                "range-sweep: sí; zone-reclaim: no; cvd-divergence: no; dip-trap: no; anti-smc: no"
            ),
            en_contra="prueba",
        ),
    )
    assert ok.ok


def test_dejar_orden_sin_objetivo_se_rechaza(tmp_path: Any, mercado: None) -> None:
    r = Registro(str(tmp_path / "op.db"))
    res = _dejar_orden(
        r,
        OrdenArgs(
            eje="range-sweep",
            direccion="long",
            precio_limite=76100.0,
            stop_loss=75500.0,
            razon="piso",
            ejes=(
                "range-sweep: sí; zone-reclaim: no; cvd-divergence: no; dip-trap: no; anti-smc: no"
            ),
            en_contra="prueba",
        ),
        4000,
    )
    assert res.ok is False and "objetivo" in res.content
    assert r.ordenes_vivas() == []


def test_estado_dice_el_plazo_y_cuando_se_agoto(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = Registro(str(tmp_path / "op.db"))
    _abierta_hace(r, horas=100)
    r.abrir(
        eje="dip-trap",
        simbolo="BTCUSDT",
        direccion="short",
        contexto=_ctx(),
        razon="sin destino",
        stop_loss=79867.31,
    )  # sin objetivo, sin marco: la vieja
    monkeypatch.setattr(herramientas, "_precio_ahora", lambda: 78803.28)

    texto = _estado(r).content

    assert "lleva 6000 min de 96 h" in texto
    assert "PLAZO AGOTADO: decidí" in texto
    assert "SIN OBJETIVO" in texto


def test_el_vigia_despierta_una_sola_vez_por_plazo_agotado(tmp_path: Any) -> None:
    r = Registro(str(tmp_path / "op.db"))
    oid = _abierta_hace(r, horas=100)
    avisados: set[int] = set()

    primera = vigia.eventos(
        r,
        precio=79000,
        atr_15m=200,
        pools=[],
        cierre_4h=1,
        cierre_4h_visto=1,
        plazos_avisados=avisados,
    )
    avisados.add(oid)
    segunda = vigia.eventos(
        r,
        precio=79000,
        atr_15m=200,
        pools=[],
        cierre_4h=1,
        cierre_4h_visto=1,
        plazos_avisados=avisados,
    )

    assert any("agotó su plazo de 96 h" in m for m in primera)
    assert segunda == []
