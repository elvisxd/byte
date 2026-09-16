"""El estado y el mapa van precargados en el mensaje de la vuelta (prompt v3).

Medido sobre 31 vueltas de los tres brazos: 28 empezaban con `estado_paper` +
`mirar_mercado`, dos llamadas al modelo para cargar lo que el vigía ya tenía.
Lo que se prueba: que la precarga sea byte a byte lo que darían las
herramientas, que vaya DESPUÉS de la instrucción (prefijo cacheable), que un
mercado caído no la rompa, y que la traza siga enseñando qué recibió el modelo.
"""

from pathlib import Path
from typing import Any

import pytest

import tools.paper as herramientas
from paper.prompt import INSTRUCCION, VERSION_PROMPT
from paper.registro import Registro
from paper.sesion import una_vuelta
from paper.trace import TraceDeSesion
from tools.paper import precarga


def _velas(marco: str) -> dict[str, Any]:
    velas = [
        {
            "time": 1_700_000_000 + i * 900,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1.0,
        }
        for i in range(200)
    ]
    return {"fuente": "prueba", "velas": velas}


INDICADORES = {
    "atr": 2.0,
    "adx": {"adx": 20.0, "plusDI": 10.0, "minusDI": 10.0},
    "ema": 100.0,
    "rsi": 50.0,
    "macd": {"macd": 0.0, "signal": 0.0, "histogram": 0.0},
    "liquidity": [],
    "fvgs": [],
}


@pytest.fixture
def mercado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(herramientas, "velas", lambda s, marco, n: _velas(marco))
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: dict(INDICADORES))


def test_la_precarga_es_lo_mismo_que_darian_las_herramientas(mercado: None, tmp_path: Path) -> None:
    registro = Registro(tmp_path / "r.db")
    texto = precarga(registro, 4000)

    assert herramientas._estado(registro).content in texto
    assert herramientas._mapa("BTCUSDT", 4000).content in texto
    assert "no llames a `estado_paper`" in texto and "no llames a `mirar_mercado`" in texto


def test_un_mercado_caido_no_rompe_la_precarga(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from paper.mercado import MercadoNoDisponible

    def sin_mercado(*_: Any) -> dict[str, Any]:
        raise MercadoNoDisponible("ninguna fuente respondió")

    monkeypatch.setattr(herramientas, "velas", sin_mercado)
    registro = Registro(tmp_path / "r.db")

    texto = precarga(registro, 4000)

    assert "SIN MERCADO" in texto and "ninguna fuente respondió" in texto
    # El estado sí llega: es lo que el modelo necesita para gestionar lo abierto.
    assert herramientas._estado(registro).content in texto


async def test_la_precarga_va_despues_de_la_instruccion_y_queda_en_la_traza() -> None:
    recibido: dict[str, Any] = {}

    class _Grafo:
        async def ainvoke(self, estado: dict[str, Any], _config: Any) -> None:
            recibido.update(estado)

    trace = TraceDeSesion(sesion_id="s", modelo="m", simbolo="BTCUSDT")
    assert await una_vuelta(_Grafo(), trace, 1, precarga="═══ ESTADO ═══\nnada abierto") is None

    # El mensaje 0 es el rol (`system`, prompt v4); el 1, la instrucción.
    contenido = recibido["messages"][1]["content"]
    # Prefijo fijo primero —es lo que el proveedor puede cachear—, luego lo que cambia.
    assert contenido.startswith(INSTRUCCION)
    assert contenido.endswith("nada abierto")
    tipos = [(p["tipo"], p.get("nombre")) for p in trace.instantanea()["pasos"]]
    assert ("herramienta", "precarga") in tipos
    assert any(
        p["tipo"] == "resultado" and "precargado" in p["texto"]
        for p in trace.instantanea()["pasos"]
    )


async def test_sin_precarga_la_vuelta_es_la_de_siempre() -> None:
    recibido: dict[str, Any] = {}

    class _Grafo:
        async def ainvoke(self, estado: dict[str, Any], _config: Any) -> None:
            recibido.update(estado)

    trace = TraceDeSesion(sesion_id="s", modelo="m", simbolo="BTCUSDT")
    await una_vuelta(_Grafo(), trace, 1)

    assert recibido["messages"][1]["content"] == INSTRUCCION
    assert trace.instantanea()["pasos"] == []


def test_el_prompt_desde_v3_no_manda_pedir_lo_que_ya_viene() -> None:
    # La versión concreta la fija test_paper_prompt_v4; acá, lo que la precarga garantiza.
    assert int(VERSION_PROMPT) >= 3
    assert "vienen YA CARGADOS" in INSTRUCCION
    assert "Mirá el estado del registro con `estado_paper`" not in INSTRUCCION
