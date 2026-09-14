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
ATR = {"4h": 651.0, "1h": 380.0, "15m": 186.0}
ATR_4H, ATR_15M = ATR["4h"], ATR["15m"]


def _velas(precio: float, marco: str = "4h") -> dict[str, Any]:
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
                "marco": marco,  # para que el mock de indicadores sepa el ATR de cuál
            }
            for i in range(200)
        ],
    }


@pytest.fixture
def registro(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Registro:
    # Los reales llaman a Node: se simulan los dos, con un ATR por marco.
    monkeypatch.setattr(herramientas, "velas", lambda simbolo, marco, n: _velas(PRECIO, marco))
    monkeypatch.setattr(
        herramientas, "indicadores", lambda velas, cuales: {"atr": ATR[velas[0]["marco"]]}
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


def test_el_rechazo_en_4h_ofrece_1h_primero(registro: Registro) -> None:
    """1h es el marco de las predicciones: el rechazo en 4h manda ahí, no a 15m.
    79867.31 chocaba con la viva de 79827.4 en 4h; en 1h dista 1281 del precio
    con un mínimo de 570, y no hay vivas de 1h."""
    contenido = _pedir(registro, 79867.31, "4h")

    assert "sería la misma apuesta" in contenido
    assert "En 1h SÍ entra" in contenido
    assert "el ATR es 380" in contenido and "el mínimo 570" in contenido
    assert 'temporalidad="1h"' in contenido and "vence en 24 h" in contenido
    assert "15m" not in contenido.split("En 1h SÍ entra")[1]


def test_si_1h_esta_ocupado_baja_a_15m_y_lo_dice(registro: Registro) -> None:
    ctx = Contexto(
        precio=PRECIO,
        timestamp="2026-09-14T12:00:00+00:00",
        dia_semana=0,
        hora_utc=12,
        extra={"indicadores": {"atr": ATR["1h"]}},
    )
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=ctx,
        nivel=79900.0,
        hacia="arriba",
        probabilidad=0.5,
        razonamiento="ya en 1h",
        temporalidad="1h",
    )

    contenido = _pedir(registro, 79867.31, "4h")

    assert "En 1h chocaría con la predicción viva #3" in contenido
    assert "En 15m SÍ entra" in contenido
    assert "el ATR es 186" in contenido and "el mínimo 279" in contenido
    assert 'temporalidad="15m"' in contenido and "vence en 6 h" in contenido


def test_el_rechazo_en_1h_ofrece_15m(registro: Registro) -> None:
    contenido = _pedir(
        registro, PRECIO + 400, "1h"
    )  # a 400: ruido en 1h (mín 570), vale en 15m (mín 279)

    assert "ruido" in contenido
    assert "En 15m SÍ entra" in contenido
    assert 'temporalidad="1h"' not in contenido  # 1h es el origen, no el destino


def test_si_en_ningun_marco_entra_lo_dice_y_no_manda_a_chocar(registro: Registro) -> None:
    """Mandarlo a chocar otra vez sería gastar otra iteración."""
    contenido = _pedir(registro, PRECIO + 100, "4h")

    assert "En 1h tampoco" in contenido and "en 15m tampoco" in contenido
    assert "otro nivel, no otro marco" in contenido
    assert "SÍ entra" not in contenido


def test_si_1h_y_15m_estan_ocupados_lo_nombra_y_no_manda_a_chocar(registro: Registro) -> None:
    for marco in ("1h", "15m"):
        ctx = Contexto(
            precio=PRECIO,
            timestamp="2026-09-14T12:00:00+00:00",
            dia_semana=0,
            hora_utc=12,
            extra={"indicadores": {"atr": ATR[marco]}},
        )
        registro.predecir(
            simbolo="BTCUSDT",
            contexto=ctx,
            nivel=79900.0,
            hacia="arriba",
            probabilidad=0.5,
            razonamiento=f"ya en {marco}",
            temporalidad=marco,
        )

    contenido = _pedir(registro, 79867.31, "4h")

    assert "En 1h chocaría con la predicción viva #3" in contenido
    assert "en 15m chocaría con la predicción viva #4" in contenido
    assert "otro nivel, no otro marco" in contenido


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
