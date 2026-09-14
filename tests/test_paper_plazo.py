"""El plazo de una predicción no puede superar el de su marco (paper/TRAMPAS.md, trampa 7).

Sesión 8 del 2026-09-14: un «15m» con `horas_vigencia=96` pasó como marco
distinto de la 4h viva —mismo nivel, misma dirección, misma probabilidad,
mismo vencimiento—. La separación por marco existe porque «tocará X en 6 h» y
«tocará X en 96 h» son preguntas distintas; con el plazo copiado, el marco era
una etiqueta.
"""

from typing import Any

import pytest

from paper.registro import Contexto, Registro


@pytest.fixture
def registro(tmp_path: Any) -> Registro:
    return Registro(str(tmp_path / "op.db"))


def _predecir(registro: Registro, marco: str, horas: float) -> int:
    ctx = Contexto(
        precio=78894.39,
        timestamp="2026-09-14T17:00:00+00:00",
        dia_semana=0,
        hora_utc=17,
        extra={"indicadores": {"atr": 222.0}},
    )
    return registro.predecir(
        simbolo="BTCUSDT",
        contexto=ctx,
        nivel=76077.62,
        hacia="abajo",
        probabilidad=0.7,
        razonamiento="x",
        temporalidad=marco,
        horas_vigencia=horas,
    )


def test_un_15m_a_96_horas_se_rechaza_diciendo_el_plazo(registro: Registro) -> None:
    with pytest.raises(ValueError, match="en 15m el plazo máximo son 6 h, no 96"):
        _predecir(registro, "15m", 96)


def test_acortar_el_plazo_si_vale(registro: Registro) -> None:
    assert _predecir(registro, "15m", 3) > 0


def test_el_plazo_de_su_marco_vale(registro: Registro) -> None:
    assert _predecir(registro, "4h", 96) > 0


def test_sin_marco_conocido_no_hay_tope(registro: Registro) -> None:
    """Sin marco no hay plazo con el que comparar: se respeta lo dicho."""
    assert _predecir(registro, "", 50) > 0


def test_la_pista_pide_el_plazo_de_15m(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import tools.paper as herramientas
    from tools.paper import PredecirArgs
    from tools.paper import _predecir as predecir_tool

    def velas(_s: str, marco: str, _n: int) -> dict[str, Any]:
        from datetime import UTC, datetime

        base = int(datetime(2026, 9, 14, 17, 0, tzinfo=UTC).timestamp())
        return {
            "fuente": "prueba",
            "velas": [
                {
                    "time": base + i * 900,
                    "open": 78894.39,
                    "high": 78944.0,
                    "low": 78844.0,
                    "close": 78894.39,
                    "volume": 1.0,
                }
                for i in range(200)
            ],
        }

    monkeypatch.setattr(herramientas, "velas", velas)
    monkeypatch.setattr(
        herramientas, "indicadores", lambda v, cuales: {"atr": 651.0 if len(cuales) > 1 else 222.0}
    )
    r = Registro(str(tmp_path / "op.db"))
    _predecir(r, "4h", 96)  # la viva que bloquea 4h

    res = predecir_tool(
        r,
        PredecirArgs(
            nivel=76077.62,
            hacia="abajo",
            probabilidad=0.7,
            temporalidad="4h",
            razonamiento="x",
            regimen="RANGE",
            horas_vigencia=0,
        ),
        4000,
    )

    assert res.ok is False
    assert "horas_vigencia=0" in res.content
    assert "vence en 6 h, que es OTRA pregunta" in res.content
