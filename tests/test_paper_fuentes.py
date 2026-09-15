"""El parte de las fuentes de velas: qué exchange sirvió y cuál falló.

⚠ LA CASCADA TAPA LAS AVERÍAS. `velas.mjs` prueba MEXC → Binance → Bybit y
devuelve la primera que conteste, así que con MEXC caído todo sigue funcionando
y nadie se entera. Es el modo degradado invisible que este proyecto ya se comió
dos veces; lo que se prueba acá es que deje rastro.
"""

from typing import Any

import pytest

from paper import mercado


@pytest.fixture(autouse=True)
def _sin_estado_previo() -> Any:
    """El contador es de módulo: un test no puede heredar el de otro."""
    mercado._FUENTES.clear()
    yield
    mercado._FUENTES.clear()


def _respuesta(fuente: str, fallos: list[str]) -> dict[str, Any]:
    return {"fuente": fuente, "velas": [{"time": 1, "close": 2.0}], "fallos": fallos}


def test_la_fuente_que_sirvio_y_las_que_fallaron_quedan_anotadas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mercado, "_node", lambda *a, **k: _respuesta("binance", ["mexc: HTTP 400"]))

    mercado.velas("BTCUSDT")

    estado = mercado.estado_fuentes()
    assert estado["binance"]["sirvio"] == 1 and estado["binance"]["fallo"] == 0
    # MEXC falló pero la cascada lo tapó: sin esto, invisible.
    assert estado["mexc"]["fallo"] == 1 and estado["mexc"]["sirvio"] == 0
    assert estado["mexc"]["ultimo_error"] == "HTTP 400"


def test_el_parte_va_en_orden_alfabetico_y_no_por_fiabilidad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Es un parte, no un ranking. Mismo criterio que los ejes y los brazos."""
    monkeypatch.setattr(
        mercado, "_node", lambda *a, **k: _respuesta("bybit", ["mexc: sin datos", "binance: 418"])
    )

    mercado.velas("BTCUSDT")

    assert list(mercado.estado_fuentes()) == ["binance", "bybit", "mexc"]


def test_varias_vueltas_acumulan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mercado, "_node", lambda *a, **k: _respuesta("binance", ["mexc: HTTP 400"]))

    for _ in range(3):
        mercado.velas("BTCUSDT")

    estado = mercado.estado_fuentes()
    assert estado["binance"]["sirvio"] == 3
    assert estado["mexc"]["fallo"] == 3


def test_si_no_responde_ninguna_tambien_queda_rastro(monkeypatch: pytest.MonkeyPatch) -> None:
    """El caso peor: la vuelta se pierde y hay que saber por qué."""
    monkeypatch.setattr(
        mercado,
        "_node",
        lambda *a, **k: {
            "error": "ninguna fuente respondió — mexc: HTTP 400; binance: timeout; bybit: sin datos"
        },
    )

    with pytest.raises(mercado.MercadoNoDisponible):
        mercado.velas("BTCUSDT")

    estado = mercado.estado_fuentes()
    assert estado["mexc"]["fallo"] == 1 and estado["binance"]["fallo"] == 1
    assert estado["bybit"]["fallo"] == 1
    # Ninguna sirvió: el contador de servidas se queda en cero.
    assert all(e["sirvio"] == 0 for e in estado.values())


def test_sin_llamadas_el_parte_esta_vacio_y_no_inventa_fuentes() -> None:
    assert mercado.estado_fuentes() == {}
