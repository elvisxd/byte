"""Las órdenes límite: el plan que queda puesto entre sesiones.

Las sesiones duran 40 minutos y el mercado corre 24/7, así que entre una y la
siguiente pasan ~23 horas sin nadie mirando. Sin órdenes, el agente solo puede
entrar en el instante exacto en que miró — el peor momento posible para un eje
cuya tesis es "entro cuando el precio VUELVA al borde del rango".
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paper.registro import Contexto, Registro


@pytest.fixture
def registro(tmp_path: Path) -> Registro:
    return Registro(tmp_path / "ops.db")


@pytest.fixture
def contexto() -> Contexto:
    return Contexto(precio=77000.0, timestamp="2026-09-13T13:30:00Z", dia_semana=0, hora_utc=13)


def _orden(registro: Registro, contexto: Contexto, **kw: object) -> int:
    args: dict = {
        "eje": "range-sweep",
        "simbolo": "BTCUSDT",
        "direccion": "long",
        "contexto": contexto,
        "razon": "El piso del rango está en 76.500. Si vuelve ahí, entro.",
        "precio_limite": 76500.0,
        "stop_loss": 76200.0,
        "take_profit": 77500.0,
    }
    args.update(kw)
    return registro.dejar_orden(**args)  # type: ignore[arg-type]


def _velas(low: float, high: float = 77100.0, cuantas: int = 8) -> list[dict]:
    """Velas POSTERIORES a la orden, que es el caso real.

    Una orden se deja y las velas que la evalúan son las que vienen después.
    `velas()` trae las últimas 200 —unas 50 horas—, así que al registro también
    le llegan las anteriores a la orden y las ignora a propósito: dispararse con
    el pasado sería entrar sabiendo ya lo que hizo el precio.
    """
    base = int(datetime.now(UTC).timestamp()) + 60
    return [
        {"time": base + i * 900, "high": high, "low": low, "close": (high + low) / 2}
        for i in range(cuantas)
    ]


def test_una_orden_tocada_se_convierte_en_operacion(registro: Registro, contexto: Contexto) -> None:
    """Es el punto entero: que el agente tenga operaciones sin estar encendido."""
    oid = _orden(registro, contexto)

    resueltas = registro.evaluar_ordenes(_velas(low=76400.0))

    assert resueltas[0]["resultado"] == "disparada"
    abierta = registro.abiertas()[0]
    assert abierta["precio_entrada"] == 76500.0, "entra al LÍMITE, no al precio de la vela"
    assert registro.ordenes_vivas() == []
    assert oid not in [o["id"] for o in registro.ordenes_vivas()]


def test_la_operacion_hereda_la_razon_sellada_de_la_orden(
    registro: Registro, contexto: Contexto
) -> None:
    """Lo que justificó la entrada se escribió al DEJAR la orden. Guardar el
    gráfico de hoy como "contexto de entrada" convertiría el registro en una
    reconstrucción a posteriori — justo lo que el sello impide."""
    _orden(registro, contexto)

    registro.evaluar_ordenes(_velas(low=76400.0))

    assert "Si vuelve ahí, entro" in registro.abiertas()[0]["razon"]
    assert registro.verificar_sellos() == []


def test_una_orden_que_no_se_toca_sigue_viva(registro: Registro, contexto: Contexto) -> None:
    """El precio bajó, pero no hasta el nivel. La orden espera."""
    _orden(registro, contexto)

    resueltas = registro.evaluar_ordenes(_velas(low=76600.0))

    assert resueltas == []
    assert len(registro.ordenes_vivas()) == 1
    assert registro.abiertas() == []


def test_se_mira_el_minimo_de_la_vela_y_no_el_cierre(
    registro: Registro, contexto: Contexto
) -> None:
    """Una orden límite se ejecuta cuando el precio TOCA el nivel, aunque la
    vela cierre lejos. Mirar solo el cierre perdería las entradas que se dieron
    dentro de la vela, que en 15 minutos son muchas."""
    _orden(registro, contexto)

    # Mínimo por debajo del límite, cierre muy por encima.
    velas = [
        {
            "time": int(datetime.now(UTC).timestamp()) + 60,
            "high": 77200.0,
            "low": 76450.0,
            "close": 77150.0,
        }
    ]

    assert registro.evaluar_ordenes(velas)[0]["resultado"] == "disparada"


def test_un_long_limite_por_encima_del_precio_no_es_una_orden(
    registro: Registro, contexto: Contexto
) -> None:
    """Se dispararía en el acto: es una entrada a mercado disfrazada, y su razón
    diría "espero a que baje" sobre algo que nunca bajó."""
    with pytest.raises(ValueError, match="entrar a mercado"):
        _orden(registro, contexto, precio_limite=77500.0, stop_loss=77200.0, take_profit=None)


def test_una_orden_vencida_no_entra(registro: Registro, contexto: Contexto) -> None:
    """Una orden de hace una semana responde a un gráfico que ya no existe:
    dispararla sería operar una tesis muerta."""
    _orden(registro, contexto, horas_vigencia=0.0001)
    import time

    time.sleep(0.5)

    resueltas = registro.evaluar_ordenes(_velas(low=76400.0))

    assert resueltas[0]["resultado"] == "vencida"
    assert registro.abiertas() == []


def test_una_orden_cancelada_no_se_dispara(registro: Registro, contexto: Contexto) -> None:
    oid = _orden(registro, contexto)
    registro.cancelar_orden(oid, nota="El rango se rompió.")

    assert registro.evaluar_ordenes(_velas(low=76400.0)) == []
    assert registro.abiertas() == []
