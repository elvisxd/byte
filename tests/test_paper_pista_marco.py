"""Al rechazar una predicción en un marco grande, la herramienta dice qué pasaría en 15m.

⚠ EL MODELO SIGUE A LA HERRAMIENTA, NO AL PROMPT. Medido el 2026-09-14 con
`qwen3:14b` pensando: ante cada rechazo cita los números del mensaje y calcula
con ellos, y aun así en cuatro intentos seguidos movió el NIVEL y nunca el
MARCO, con el prompt diciéndole en mayúsculas que bajara a 15m. Su primer
intento entraba en 15m de sobra. Por eso la pista va en el rechazo y con cifras.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

import tools.paper as herramientas
from paper.registro import Contexto, Registro
from tools.paper import PredecirArgs, _predecir

PRECIO = 78586.42
ATR_4H = 651.0
ATR_15M = 186.0


def _velas(precio: float) -> dict[str, Any]:
    base = int(datetime(2026, 9, 14, 12, 0, tzinfo=UTC).timestamp())
    return {
        "fuente": "prueba",
        "velas": [
            {
                "time": base + i * 900,
                "open": precio,
                "high": precio + 50,
                "low": precio - 50,
                "close": precio,
                "volume": 10.0,
            }
            for i in range(200)
        ],
    }


@pytest.fixture
def registro(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Registro:
    # Los reales llaman a Node: se simulan los dos, con un ATR por marco.
    monkeypatch.setattr(herramientas, "velas", lambda simbolo, marco, n: _velas(PRECIO))
    monkeypatch.setattr(
        herramientas,
        "indicadores",
        lambda velas, cuales: {"atr": ATR_4H if len(cuales) > 1 else ATR_15M},
    )
    r = Registro(str(tmp_path / "op.db"))
    ctx = Contexto(
        precio=PRECIO,
        timestamp="2026-09-14T12:00:00+00:00",
        dia_semana=0,
        hora_utc=12,
        extra={"indicadores": {"atr": ATR_4H}},
    )
    # Las dos vivas de 4h que bloqueaban todo esa mañana.
    r.predecir(
        simbolo="BTCUSDT",
        contexto=ctx,
        nivel=79827.4,
        hacia="arriba",
        probabilidad=0.4,
        razonamiento="techo",
        temporalidad="4h",
    )
    r.predecir(
        simbolo="BTCUSDT",
        contexto=ctx,
        nivel=76077.62,
        hacia="abajo",
        probabilidad=0.7,
        razonamiento="piso",
        temporalidad="4h",
    )
    return r


def _pedir(registro: Registro, nivel: float, marco: str, hacia: str = "arriba") -> str:
    res = _predecir(
        registro,
        PredecirArgs(
            nivel=nivel,
            hacia=hacia,
            probabilidad=0.5,
            temporalidad=marco,
            razonamiento="prueba",
            regimen="RANGE",
            horas_vigencia=0,
        ),
        4000,
    )
    assert res.ok is False
    return res.content


def test_el_rechazo_en_4h_dice_que_en_15m_si_entra(registro: Registro) -> None:
    """El intento 3 de la vuelta 1: 79867.31 chocaba con la viva de 79827.4 en
    4h, y en 15m distaba 1280 del precio con un mínimo de 279."""
    contenido = _pedir(registro, 79867.31, "4h")

    assert "sería la misma apuesta" in contenido
    assert "En 15m SÍ entra" in contenido
    assert "el ATR es 186" in contenido and "el mínimo 279" in contenido
    assert 'temporalidad="15m"' in contenido


def test_si_en_15m_tampoco_entra_lo_dice_y_no_manda_a_chocar(registro: Registro) -> None:
    """Mandarlo a 15m para que vuelva a chocar sería gastar otra iteración."""
    contenido = _pedir(registro, PRECIO + 100, "4h")

    assert "En 15m tampoco" in contenido
    assert "otro nivel, no otro marco" in contenido
    assert "SÍ entra" not in contenido


def test_si_en_15m_choca_con_una_viva_lo_nombra(registro: Registro) -> None:
    ctx = Contexto(
        precio=PRECIO,
        timestamp="2026-09-14T12:00:00+00:00",
        dia_semana=0,
        hora_utc=12,
        extra={"indicadores": {"atr": ATR_15M}},
    )
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=ctx,
        nivel=79900.0,
        hacia="arriba",
        probabilidad=0.5,
        razonamiento="ya en 15m",
        temporalidad="15m",
    )

    contenido = _pedir(registro, 79867.31, "4h")

    assert "chocaría con la predicción viva #3" in contenido
    assert "Otro nivel en 15m sí entra" in contenido


def test_en_15m_no_hay_pista_que_dar(registro: Registro) -> None:
    """Rechazado ya en 15m: no hay marco menor al que mandarlo."""
    contenido = _pedir(registro, PRECIO + 10, "15m")

    assert "ruido" in contenido
    assert "En 15m" not in contenido


def test_sin_mercado_el_rechazo_sale_igual_sin_pista(
    registro: Registro, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La pista es un extra: si no hay velas de 15m, el rechazo original no se pierde."""
    from paper.mercado import MercadoNoDisponible

    def caido(*_: Any, **__: Any) -> dict[str, Any]:
        raise MercadoNoDisponible("sin red")

    monkeypatch.setattr(herramientas, "velas", caido)
    res = _predecir(
        registro,
        PredecirArgs(
            nivel=79867.31,
            hacia="arriba",
            probabilidad=0.5,
            temporalidad="4h",
            razonamiento="prueba",
            regimen="RANGE",
            horas_vigencia=0,
        ),
        4000,
    )

    assert res.ok is False
    assert "sin datos" in res.summary.get("error", "") or "no se registró" in res.content
