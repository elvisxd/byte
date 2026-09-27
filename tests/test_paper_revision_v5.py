"""Las listas de comprobación como CAMPOS (prompt v5, tools/paper.py): el sí/no
por eje, el contra y la cuenta entera de la predicción se validan y se sellan.

Medido con gemini v4: obedece lo que la herramienta exige (reacciona a los
rechazos 12 contra 2) y se salta lo que el texto pide (8 de 53 pensamientos
recorren ≥3 ejes). Lo que aquí se prueba es que la herramienta lo exija."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

import tools.paper as herramientas
from paper.prompt import VERSION_PROMPT
from paper.registro import Registro
from tools.paper import (
    AbrirArgs,
    OrdenArgs,
    PredecirArgs,
    _abrir,
    _dejar_orden,
    _predecir,
    calibracion_propia,
    fijar_vuelta,
    revisar_ejes,
)

PRECIO = 100_000.0
EJES_OK = (
    "range-sweep: sí (99500, invalida 99300); zone-reclaim: no; cvd-divergence: no; "
    "dip-trap: no; anti-smc: no"
)


def _velas(precio: float) -> dict[str, Any]:
    base = int(datetime(2026, 9, 23, 12, 0, tzinfo=UTC).timestamp())
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
    monkeypatch.setattr(herramientas, "velas", lambda simbolo, marco, n: _velas(PRECIO))
    monkeypatch.setattr(herramientas, "indicadores", lambda velas, cuales: {"atr": 400.0})
    fijar_vuelta(
        analista={"marco": "1h", "arriba": {"nivel": 101000.0, "media": 0.45}},
        calibracion_vista=False,
    )
    return Registro(tmp_path / "p.db")


def _prediccion(**cambios: Any) -> PredecirArgs:
    base: dict[str, Any] = {
        "nivel": 101000.0,
        "hacia": "arriba",
        "probabilidad": 0.47,
        "razonamiento": "pool",
        "temporalidad": "1h",
        "tasa_base": 38,
        "ajuste": 9,
        "razon_del_ajuste": "atrapados bajo 99500",
        "ejes": EJES_OK,
        "en_contra": "el 4h sigue bajista; si falla, era un rebote de 15m",
    }
    base.update(cambios)
    return PredecirArgs(**base)


def test_los_cinco_ejes_con_si_o_no_y_nada_menos() -> None:
    assert revisar_ejes(EJES_OK) == {
        "range-sweep": True,
        "zone-reclaim": False,
        "cvd-divergence": False,
        "dip-trap": False,
        "anti-smc": False,
    }
    assert (
        revisar_ejes(
            "range-sweep: si, zone-reclaim: NO, cvd-divergence: no, dip-trap: yes, anti-smc: no"
        )["dip-trap"]
        is True
    )
    with pytest.raises(ValueError, match="Faltan o no dicen sí/no: cvd-divergence, anti-smc"):
        revisar_ejes("range-sweep: sí; zone-reclaim: no; dip-trap: no; anti-smc: quizás")


def test_la_prediccion_exige_que_la_cuenta_cuadre(registro: Registro) -> None:
    res = _predecir(registro, _prediccion(probabilidad=0.6), 4000)
    assert res.ok is False and res.summary == {"error": "cuenta"}
    assert "38 + ajuste +9 = 47%, y dijiste probabilidad 60%" in res.content
    assert registro.predicciones_vivas() == []

    res = _predecir(registro, _prediccion(tasa_base=0.38, ajuste=0.09, probabilidad=0.47), 4000)
    assert res.ok is False, "la tasa base va en porcentaje, no en fracción"

    res = _predecir(registro, _prediccion(razon_del_ajuste="  "), 4000)
    assert res.ok is False and "razon_del_ajuste" in res.content

    res = _predecir(registro, _prediccion(ejes="range-sweep: sí"), 4000)
    assert res.ok is False and res.summary == {"error": "sin revisión"}
    assert "zone-reclaim, cvd-divergence, dip-trap, anti-smc" in res.content

    res = _predecir(registro, _prediccion(en_contra=""), 4000)
    assert res.ok is False and "en_contra" in res.content


def test_la_prediccion_que_cuadra_sella_la_revision_y_la_lectura_del_analista(
    registro: Registro,
) -> None:
    # ±1 punto de redondeo se tolera: 38 + 9 = 47 y 0.47 cuadra; 0.475 también.
    res = _predecir(registro, _prediccion(probabilidad=0.475), 4000)
    assert res.ok is True, res.content

    extra = json.loads(registro.predicciones_vivas()[0]["contexto"])["extra"]
    assert extra["prompt"] == VERSION_PROMPT
    assert extra["revision"] == {
        "ejes": {
            "range-sweep": True,
            "zone-reclaim": False,
            "cvd-divergence": False,
            "dip-trap": False,
            "anti-smc": False,
        },
        "en_contra": "el 4h sigue bajista; si falla, era un rebote de 15m",
        "tasa_base": 38.0,
        "ajuste": 9.0,
        "razon_del_ajuste": "atrapados bajo 99500",
    }
    assert extra["analista"]["arriba"]["media"] == 0.45
    assert extra["calibracion_vista"] is False


def test_abrir_y_dejar_orden_exigen_la_revision_y_la_sellan(registro: Registro) -> None:
    sin = _abrir(
        registro,
        AbrirArgs(
            eje="range-sweep",
            direccion="long",
            stop_loss=99300.0,
            take_profit=101000.0,
            razon="mecha",
            ejes="range-sweep: sí",
            en_contra="x",
        ),
    )
    assert sin.ok is False and sin.summary == {"error": "sin revisión"}
    assert registro.abiertas() == []

    con = _abrir(
        registro,
        AbrirArgs(
            eje="range-sweep",
            direccion="long",
            stop_loss=99300.0,
            take_profit=101000.0,
            razon="mecha",
            ejes=EJES_OK,
            en_contra="el 4h",
        ),
    )
    assert con.ok is True, con.content
    extra = json.loads(registro.abiertas()[0]["contexto"])["extra"]
    assert (
        extra["revision"]["ejes"]["range-sweep"] is True
        and extra["revision"]["en_contra"] == "el 4h"
    )

    orden = _dejar_orden(
        registro,
        OrdenArgs(
            eje="range-sweep",
            direccion="long",
            precio_limite=99400.0,
            stop_loss=99000.0,
            take_profit=101000.0,
            razon="si vuelve",
            ejes=EJES_OK,
            en_contra="",
        ),
        4000,
    )
    assert orden.ok is False and "en_contra" in orden.content


def test_tu_calibracion_calla_bajo_las_50_y_lo_deja_sellado(registro: Registro) -> None:
    texto = calibracion_propia(registro)
    assert texto.startswith(f"═══ TU CALIBRACIÓN (prompt v{VERSION_PROMPT})")
    assert "0 resueltas con este prompt: faltan 50" in texto
    assert herramientas._VUELTA["calibracion_vista"] is False


# ── prompt v7: los niveles de la gráfica y la orden con rechazo ─────────────


def test_la_orden_sella_como_entra_y_de_que_niveles_salen_sus_precios(
    registro: Registro,
) -> None:
    orden = _dejar_orden(
        registro,
        OrdenArgs(
            eje="range-sweep",
            direccion="long",
            precio_limite=99400.0,
            stop_loss=99000.0,
            take_profit=101000.0,
            razon="OB alcista 99.300-99.450 y golden pocket; objetivo en la resistencia",
            ejes=EJES_OK,
            en_contra="el 4h bajista",
            confirmacion="rechazo",
            marco_confirmacion="1h",
            origen_entrada="ob",
            origen_tp="tendencia",
        ),
        4000,
    )
    assert orden.ok is True, orden.content
    assert "Con RECHAZO en 1h" in orden.content
    viva = registro.ordenes_vivas()[0]
    assert (viva["confirmacion"], viva["marco_confirmacion"]) == ("rechazo", "1h")
    extra = json.loads(viva["contexto"])["extra"]
    assert extra["orden"] == {
        "confirmacion": "rechazo",
        "marco_confirmacion": "1h",
        "origen_entrada": "ob",
        "origen_tp": "tendencia",
    }


def test_la_linea_de_niveles_del_mapa_lleva_precios_y_evidencia() -> None:
    from tools.paper import _niveles

    n = {
        "marco": "1h",
        "order_blocks": [
            {
                "lado": "alcista",
                "piso": 83281.07,
                "techo": 84250.76,
                "distancia_atr": 8.63,
                "toques": 3,
                "reacciones": 3,
            },
        ],
        "golden_pocket": {
            "tramo": "caída",
            "desde": 88508.75,
            "hasta": 83411.57,
            "piso": 86561.63,
            "techo": 86724.74,
            "medio": 85960.16,
            "retroceso": 0.87,
            "toques": 0,
            "reacciones": 0,
        },
        "tendencias": [
            {
                "lado": "resistencia",
                "precio": 88211.37,
                "pendiente": -6.61,
                "toques": 3,
                "reacciones": 2,
            }
        ],
        "tridente": {
            "mediana": 88948.61,
            "superior": 92351.05,
            "inferior": 85546.18,
            "ancho_atr": 10.94,
            "mediana_ev": {"toques": 0, "reacciones": 0},
        },
    }
    linea = _niveles(n)
    assert "OB ↑ 83281.07–84250.76 (3t·3r, a 8.63 ATR)" in linea
    assert "golden pocket 86561.63–86724.74 (caída 88508.75→83411.57" in linea
    assert "tendencia resistencia ↘ 88211.37 (3t·2r)" in linea
    assert "tridente mediana 88948.61 entre 85546.18 y 92351.05" in linea
    assert _niveles(None) == "" and _niveles({"error": "x"}) == ""
