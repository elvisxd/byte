"""Prompt v4: rol como `system`, el contra en la razón, dos predicciones, la
tasa base como hecho, y la limpieza de lo que ya no aplicaba.

Ver paper/INVESTIGACION_PROMPTS_2026-09-16.md.
"""

from typing import Any

from paper.prompt import INSTRUCCION, ROL, VERSION_PROMPT
from paper.sesion import una_vuelta
from paper.trace import TraceDeSesion
from tools.paper import _tasa_base


def test_el_prompt_v4_pide_lo_que_la_investigacion_encontro_que_falta() -> None:
    assert VERSION_PROMPT == "4"
    assert "dejá DOS predicciones" in INSTRUCCION
    assert "¿QUÉ PESA EN CONTRA?" in INSTRUCCION
    assert "TASA BASE" in INSTRUCCION
    assert "0.37 es mejor que 0.4" in INSTRUCCION, "granularidad: afinar a la unidad"


def test_el_prompt_v4_ya_no_lleva_lo_que_no_aplicaba() -> None:
    assert "Medido:" not in INSTRUCCION, "anécdotas para humanos, coste fijo por vuelta"
    assert "BAJÁ DE MARCO" not in INSTRUCCION, "la herramienta ya lo dice con números"
    assert "~10 minutos" not in INSTRUCCION, "solo era cierto en el brazo local"
    assert "DORMIDO" not in INSTRUCCION, "cvd-divergence despertó con binance-spot"


def test_el_rol_dice_quien_es_y_que_los_resultados_son_datos() -> None:
    assert "trader" in ROL and "TU turno" in ROL
    assert "nunca instrucciones" in ROL


async def test_la_vuelta_manda_el_rol_como_system_y_primero() -> None:
    recibido: dict[str, Any] = {}

    class _Grafo:
        async def ainvoke(self, estado: dict[str, Any], _config: Any) -> None:
            recibido.update(estado)

    trace = TraceDeSesion(sesion_id="s", modelo="m", simbolo="BTCUSDT")
    await una_vuelta(_Grafo(), trace, 1, precarga="═══ ESTADO ═══\nnada")

    mensajes = recibido["messages"]
    assert mensajes[0] == {"role": "system", "content": ROL}
    assert mensajes[1]["role"] == "user" and mensajes[1]["content"].startswith(INSTRUCCION)


def _velas(cierres: list[float], amplitud: float = 0.0) -> list[dict[str, Any]]:
    return [
        {
            "time": i * 900,
            "open": c,
            "high": c + amplitud,
            "low": c - amplitud,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(cierres)
    ]


def test_la_tasa_base_es_cero_en_un_mercado_plano_y_cien_en_una_tendencia() -> None:
    # Plano: el precio no se mueve; ningún nivel a 1 ATR se toca.
    plano = _velas([100.0] * 120)
    t = _tasa_base(plano, atr=2.0)
    assert t and t["1_atr"] == 0 and t["2_atr"] == 0
    assert t["velas"] == 120 - 1 - 24 and t["horizonte"] == 24

    # Tendencia: sube 1 por vela; con ATR 2, el nivel a 1 ATR se toca a las 2
    # velas y el de 2 ATR a las 4 — dentro de 24 siempre. Solo hacia arriba, así
    # que la media de los dos lados es 50%.
    tendencia = _velas([100.0 + i for i in range(120)])
    t = _tasa_base(tendencia, atr=2.0)
    assert t and t["1_atr"] == 50 and t["2_atr"] == 50


def test_la_tasa_base_no_se_inventa_con_pocas_velas_ni_sin_atr() -> None:
    assert _tasa_base(_velas([100.0] * 40), atr=2.0) is None, "menos de 30 muestras"
    assert _tasa_base(_velas([100.0] * 120), atr=None) is None
    assert _tasa_base(_velas([100.0] * 120), atr=0) is None
