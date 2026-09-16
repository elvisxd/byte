"""Lo que el mapa dice desde el 2026-09-16 y lo que cada escritura sella de la vuelta.

Tres huecos entre lo que el prompt exige y lo que el modelo recibía (ver
paper/EVALUACION_ENTORNO_2026-09-16.md): la frescura del extremo, el volumen
medido sobre la vela en curso, y el CVD dormido. Y dos variables del
experimento que no se sellaban: por qué despertó el modelo y cómo piensa.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import tools.paper as herramientas
from paper.registro import Registro
from tools.paper import MirarArgs, _contexto_de, _mapa, _mirar, fijar_vuelta

PISO, TECHO = 76000.0, 79800.0


def _velas(
    *, techo_hace: int = 5, piso_hace: int = 12, vol_en_curso: float = 3.0
) -> dict[str, Any]:
    # 200 velas de 15m; la última es la en curso. Un solo techo y un solo piso
    # dentro de las últimas 20, a las distancias pedidas. ⚠ Empiezan días
    # atrás: 200 velas de 15m son 50 h, y con base el día 14 la «en curso»
    # caía en el FUTURO y el volumen prorrateado salía a minuto 0.
    base = int(datetime(2026, 9, 10, 12, 0, tzinfo=UTC).timestamp())
    velas = [
        {
            "time": base + i * 900,
            "open": 78000.0,
            "high": 78100.0,
            "low": 77900.0,
            "close": 78000.0,
            "volume": 10.0,
        }
        for i in range(200)
    ]
    velas[-1 - techo_hace]["high"] = TECHO
    velas[-1 - piso_hace]["low"] = PISO
    velas[-1]["volume"] = vol_en_curso * 10.0
    return {"fuente": "prueba", "velas": velas}


IND: dict[str, Any] = {"atr": 300.0, "ema": 78000.0, "rsi": 50.0, "adx": {"adx": 20.0}, "macd": {}}


def test_el_mapa_dice_hace_cuantas_velas_se_hizo_cada_extremo() -> None:
    ctx = _contexto_de(_velas(techo_hace=5, piso_hace=12), IND)
    assert ctx.extra["techo_hace_velas"] == 5 and ctx.extra["piso_hace_velas"] == 12
    assert ctx.extra["rango_techo"] == TECHO and ctx.extra["rango_piso"] == PISO


def test_el_volumen_relativo_es_el_de_la_ultima_cerrada_y_la_en_curso_va_aparte() -> None:
    """Medido: una vela de 4h con 78 min de vida daba «0,13x» contra 49 cerradas y
    siempre parecía baja; es lo que frenó al 27B cuatro vueltas seguidas."""
    ctx = _contexto_de(_velas(vol_en_curso=3.0), IND)
    # La última cerrada vale 10 contra una media de 10: 1.0x, no 3.0x.
    assert ctx.volumen_relativo == 1.0
    vc = ctx.extra["volumen_en_curso"]
    # Las velas de prueba son de 2026-09-14: la «en curso» lleva más de un
    # período, así que se muestra completa (minuto 15 de 15) y sin prorratear más.
    assert vc["x"] == 3.0 and vc["de"] == 15 and vc["minuto"] == 15


def test_la_linea_del_mapa_lleva_frescura_volumen_y_cvd(monkeypatch: pytest.MonkeyPatch) -> None:
    ind = {
        **IND,
        "cvd": {
            "cvd_20": -12.5,
            "ratio_comprador": 0.41,
            "divergencia": {"tipo": "bajista", "hace_velas": 1},
        },
    }
    monkeypatch.setattr(herramientas, "velas", lambda s, marco, n: _velas())
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: dict(ind))

    # El mapa de los tres marcos: es lo que el modelo lee en cada vuelta.
    texto = _mapa("BTCUSDT", 8000).content

    assert "techo hace 5 velas, piso hace 12" in texto
    assert "volumen 1.0x de la media (última cerrada)" in texto and "en curso 3.0x" in texto
    assert "CVD 20 velas: -12.5" in texto and "41% del volumen fue comprador" in texto
    assert "divergencia bajista hace 1 velas" in texto
    # Con el indicador presente, el aviso de «sin CVD» no puede aparecer.
    assert "sin CVD" not in texto


def test_sin_cvd_el_mapa_lo_dice_y_no_inventa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(herramientas, "velas", lambda s, marco, n: _velas())
    monkeypatch.setattr(herramientas, "indicadores", lambda v, cuales: {**IND, "cvd": None})

    mapa = _mapa("BTCUSDT", 8000).content
    detalle = _mirar(MirarArgs(simbolo="BTCUSDT", intervalo="15m"), 4000).content

    assert "CVD 20 velas" not in mapa and "sin CVD" in mapa
    assert "sin CVD" in detalle


def test_cada_escritura_sella_el_motivo_del_despertar_y_como_piensa(tmp_path: Path) -> None:
    """Sin esto, CRITERIO_HORARIOS.md no se puede revisar con datos."""
    fijar_vuelta(motivo="cerró la vela de 4h de las 12:00", pensamiento="razona")
    try:
        ctx = _contexto_de(_velas(), IND)
        assert ctx.extra["motivo"] == "cerró la vela de 4h de las 12:00"
        assert ctx.extra["pensamiento"] == "razona"

        registro = Registro(tmp_path / "r.db", modelo="qwen3:14b+razona")
        i = registro.abrir(
            eje="range-sweep",
            simbolo="BTCUSDT",
            direccion="long",
            contexto=ctx,
            razon="prueba",
            stop_loss=77000.0,
            take_profit=79000.0,
            temporalidad="1h",
        )
        fila = next(o for o in registro.abiertas() if o["id"] == i)
        import json

        extra = json.loads(fila["contexto"])["extra"]
        assert (
            extra["motivo"].startswith("cerró la vela de 4h") and extra["pensamiento"] == "razona"
        )
        # Y el sello sigue cuadrando: el motivo forma parte de lo sellado.
        assert registro.verificar_sellos() == []
    finally:
        fijar_vuelta(motivo="", pensamiento="")


def test_sin_fijar_nada_el_sello_no_rompe_las_filas_de_antes() -> None:
    fijar_vuelta(motivo="", pensamiento="")
    ctx = _contexto_de(_velas(), IND)
    assert ctx.extra["motivo"] == "" and ctx.extra["pensamiento"] == ""
