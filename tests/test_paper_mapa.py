"""El mapa: los tres gráficos del mismo instante en una llamada, como hechos.

Medido el 2026-09-14: el modelo pedía 4h, luego 15m, y leía el rango de 4h
dentro del gráfico de 15m (trampa 6). Cada llamada eran ~7 minutos y una de
seis iteraciones.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

import tools.paper as herramientas
from paper.mercado import MercadoNoDisponible
from tools.paper import MirarArgs, _mirar, build_paper_tools

PRECIO = {"15m": 79109.14, "1h": 79100.0, "4h": 79136.58}
PISO, TECHO = 76077.62, 79827.4


def _velas(marco: str, *, mecha_arriba: float = 0.0) -> dict[str, Any]:
    base = int(datetime(2026, 9, 14, 12, 0, tzinfo=UTC).timestamp())
    cierre = PRECIO[marco]
    velas = [
        {
            "time": base + i * 900,
            "open": cierre,
            "high": TECHO,
            "low": PISO,
            "close": cierre,
            "volume": 10.0,
        }
        for i in range(199)
    ]
    velas.append(
        {
            "time": base + 199 * 900,
            "open": cierre,
            "high": TECHO + mecha_arriba,
            "low": cierre - 100,
            "close": cierre,
            "volume": 25.0,
        }
    )
    return {"fuente": "prueba", "velas": velas}


INDICADORES = {
    "atr": 650.0,
    "ema": 77698.89,
    "rsi": 66.7,
    "adx": {"adx": 17.1, "plusDI": 20.0, "minusDI": 15.0},
    "macd": {"macd": 90.8, "signal": -163.9},
    "regime": {"regimen": "RANGE", "chop": 40.4, "bbw_percentil": 30},
    "liquidity": [
        {"precio": 79867.31, "lado": "encima", "fuerza": 2, "swings": 2},
        {"precio": 75503.62, "lado": "debajo", "fuerza": 1},
    ],
    "fvg": [
        {"piso": 75766.89, "techo": 75950.75, "tipo": "alcista"},
        {"piso": 78100.0, "techo": 78210.0, "tipo": "bajista", "invertido": True},
    ],
    "divergencias": [{"precio": 79250.0, "tipo": "alcista", "hace_velas": 3}],
}


@pytest.fixture
def mercado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(herramientas, "velas", lambda s, marco, n: _velas(marco))
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: dict(INDICADORES))


async def _mapa_por_la_herramienta(tmp_path: Any) -> str:
    tools = {
        t.name: t for t in build_paper_tools(str(tmp_path / "op.db"), 8000, "qwen3:14b+razona")
    }
    res = await tools["mirar_mercado"].run(MirarArgs())
    assert res.ok
    return res.content


async def test_sin_intervalo_vienen_los_tres_marcos(mercado: None, tmp_path: Any) -> None:
    texto = await _mapa_por_la_herramienta(tmp_path)

    for marco in ("── 15m ──", "── 1h ──", "── 4h ──"):
        assert marco in texto
    assert "no mezcles los de uno con los de otro" in texto
    assert "precio 79136.58 · al 82% del rango" in texto  # el 4h, con su porcentaje
    assert "por ENCIMA de la EMA20" in texto
    assert "régimen medido: RANGE" in texto
    assert "pools sin barrer: 79867.31 (encima, f2, 2 swings)" in texto
    assert (
        "FVG sin rellenar: 75766.89–75950.75 (alcista), 78100.0–78210.0 (bajista, INVERTIDO)"
        in texto
    )


async def test_la_vela_en_curso_se_describe_como_hecho(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Una mecha por encima del techo de las 19 previas con cierre dentro es lo
    que range-sweep mira. Se dice cuánto; si es un barrido lo decide el modelo."""
    monkeypatch.setattr(
        herramientas, "velas", lambda s, marco, n: _velas(marco, mecha_arriba=123.0)
    )
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: dict(INDICADORES))

    texto = await _mapa_por_la_herramienta(tmp_path)

    assert "mecha 123 por ENCIMA del techo de las 19 previas, cierre dentro" in texto
    assert "range-sweep: SÍ" not in texto and "range-sweep" not in texto


async def test_con_intervalo_sigue_el_detalle_de_un_solo_marco(
    mercado: None, tmp_path: Any
) -> None:
    tools = {t.name: t for t in build_paper_tools(str(tmp_path / "op.db"), 8000, "x")}
    res = await tools["mirar_mercado"].run(MirarArgs(intervalo="4h"))

    assert res.ok
    assert "BTCUSDT 4h — prueba" in res.content
    assert "── 15m ──" not in res.content
    assert "pools de liquidez sin barrer — ahí están los stops:" in res.content


async def test_un_marco_caido_no_tumba_el_mapa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    def velas(s: str, marco: str, n: int) -> dict[str, Any]:
        if marco == "1h":
            raise MercadoNoDisponible("1h no responde")
        return _velas(marco)

    monkeypatch.setattr(herramientas, "velas", velas)
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: dict(INDICADORES))

    texto = await _mapa_por_la_herramienta(tmp_path)

    assert "── 15m ──" in texto and "── 4h ──" in texto and "── 1h ──" not in texto
    assert "(sin 1h: 1h no responde)" in texto


async def test_el_mapa_cabe_en_el_tope_de_papel(mercado: None, tmp_path: Any) -> None:
    """Tres marcos con pools, FVGs y agotamientos: tiene que caber sin recorte."""
    texto = await _mapa_por_la_herramienta(tmp_path)

    assert "recortado" not in texto
    assert len(texto) < 8000


def test_mirar_directo_sigue_funcionando(mercado: None) -> None:
    assert "al 82% del rango" in _mirar(MirarArgs(simbolo="BTCUSDT", intervalo="4h"), 4000).content
