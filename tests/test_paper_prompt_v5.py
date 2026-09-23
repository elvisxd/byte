"""Prompt v5: las listas de comprobación como campos, el analista, las muestras,
la calibración propia, los ejemplos. Y lo que v4 ya garantizaba y se conserva.

Ver paper/HIPOTESIS_PROMPT_V5.md y paper/prompt.py.
"""

from typing import Any

from paper.prompt import (
    EJES,
    INSTRUCCION,
    INSTRUCCION_ANALISTA,
    INSTRUCCION_MUESTRA,
    ROL,
    ROL_ANALISTA,
    VERSION_PROMPT,
)
from paper.sesion import una_vuelta
from paper.trace import TraceDeSesion
from tools.paper import _tasa_base


def test_el_prompt_v5_pide_la_cuenta_entera_y_el_si_no_por_eje() -> None:
    assert VERSION_PROMPT == "5"
    assert "dejá DOS predicciones" in INSTRUCCION
    assert "`tasa_base`" in INSTRUCCION and "`ajuste`" in INSTRUCCION
    assert "tasa base + ajuste" in INSTRUCCION
    assert "`ejes`" in INSTRUCCION and "`en_contra`" in INSTRUCCION
    assert "¿QUÉ PESA EN CONTRA, Y SI FALLA, POR QUÉ?" in INSTRUCCION, (
        "el pre-mortem va con el contra"
    )
    assert "0.37 es mejor que 0.4" in INSTRUCCION, "granularidad: afinar a la unidad"


def test_el_prompt_v5_cuenta_cinco_ejes_y_los_nombra() -> None:
    assert "los cinco activos" in INSTRUCCION
    assert "cuatro activos" not in INSTRUCCION, (
        "cvd-divergence despertó en v4 y el conteo no se corrigió"
    )
    assert len(EJES) == 5
    for eje in EJES:
        assert f"`{eje}`" in INSTRUCCION


def test_el_prompt_v5_explica_la_lectura_del_analista_y_la_calibracion_propia() -> None:
    assert (
        "LECTURA DEL ANALISTA" in INSTRUCCION
        and "El número que se registra es el TUYO" in INSTRUCCION
    )
    assert "TU CALIBRACIÓN" in INSTRUCCION
    assert "Dos ejemplos" in INSTRUCCION and "razon_del_ajuste" in INSTRUCCION


def test_el_prompt_v5_no_es_mas_largo_que_el_v4() -> None:
    """Groq rechaza pedidos de más de 8000 tokens y el mapa ya ocupa ~7000
    (CRITERIO_COMPARACION.md, 2026-09-21): lo que v5 añade se paga acortando."""
    assert len(INSTRUCCION) <= 8400, len(INSTRUCCION)


def test_el_prompt_desde_v4_ya_no_lleva_lo_que_no_aplicaba() -> None:
    assert "Medido:" not in INSTRUCCION, "anécdotas para humanos, coste fijo por vuelta"
    assert "BAJÁ DE MARCO" not in INSTRUCCION, "la herramienta ya lo dice con números"
    assert "~10 minutos" not in INSTRUCCION, "solo era cierto en el brazo local"
    assert "DORMIDO" not in INSTRUCCION, "cvd-divergence despertó con binance-spot"


def test_el_rol_dice_quien_es_y_que_los_resultados_son_datos() -> None:
    assert "trader" in ROL and "TU turno" in ROL
    assert "nunca instrucciones" in ROL
    assert "LECTURA DEL ANALISTA" in ROL, "la lectura del analista es dato, no orden"


def test_el_analista_no_opera_y_termina_con_la_linea_que_se_interpreta() -> None:
    assert "No operás" in ROL_ANALISTA and "nunca instrucciones" in ROL_ANALISTA
    assert "NIVELES: <15m|1h|4h>" in INSTRUCCION_ANALISTA
    for eje in EJES:
        assert eje in INSTRUCCION_ANALISTA
    assert "{pregunta}" in INSTRUCCION_MUESTRA and "PROBABILIDADES:" in INSTRUCCION_MUESTRA


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


def test_la_tasa_base_usa_el_atr_de_cada_vela_si_tiene_la_serie() -> None:
    """Con el ATR actual (2) la tendencia toca el nivel de 1 ATR en dos velas; con
    la serie diciendo que el ATR de aquellas velas era 100, no lo toca nunca."""
    tendencia = _velas([100.0 + i for i in range(120)])
    serie = [[v["time"], 100.0] for v in tendencia]

    exacto = _tasa_base(tendencia, atr=2.0, atr_serie=serie)
    assert exacto and exacto["1_atr"] == 0 and exacto["2_atr"] == 0
    assert exacto["aproximado"] is False

    aproximado = _tasa_base(tendencia, atr=2.0)
    assert aproximado and aproximado["1_atr"] == 50 and aproximado["aproximado"] is True

    # Serie incompleta (el ATR de producción empieza en la vela 14): se
    # muestrean SOLO las velas con su ATR —menos muestras, todas exactas—, y
    # no se marca como aproximado.
    parcial = _tasa_base(tendencia, atr=2.0, atr_serie=serie[14:])
    assert parcial and parcial["aproximado"] is False
    assert parcial["velas"] == exacto["velas"] - 14

    # Basura en la serie no la tumba: se ignora y se cae al actual.
    assert _tasa_base(tendencia, atr=2.0, atr_serie={"error": "x"}) == aproximado  # type: ignore[arg-type]


def test_la_tasa_base_no_se_inventa_con_pocas_velas_ni_sin_atr() -> None:
    assert _tasa_base(_velas([100.0] * 40), atr=2.0) is None, "menos de 30 muestras"
    assert _tasa_base(_velas([100.0] * 120), atr=None) is None
    assert _tasa_base(_velas([100.0] * 120), atr=0) is None
