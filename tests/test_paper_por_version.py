"""Cada prompt es otra muestra: el resumen que va al panel la parte por versión
con su propia puerta (paper/comparar.py), y el brazo ve su propia calibración
solo con el prompt de hoy y solo desde las 50 (paper/registro.py)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from paper.comparar import PREDICCIONES_PARA_DECIDIR, resumen
from paper.prompt import VERSION_PROMPT
from paper.registro import Contexto, Registro


def _contexto(version: str, analista: dict[str, Any] | None = None) -> Contexto:
    extra: dict[str, Any] = {"prompt": version}
    if analista is not None:
        extra["analista"] = analista
    return Contexto(
        precio=100.0, timestamp="2026-09-23T12:00:00+00:00", dia_semana=2, hora_utc=12, extra=extra
    )


def _resueltas(
    registro: Registro,
    version: str,
    cuantas: int,
    *,
    tocan: bool,
    p: float = 0.6,
    analista_media: float | None = None,
) -> None:
    """`cuantas` predicciones con el prompt `version`, resueltas todas del mismo lado."""
    for i in range(cuantas):
        nivel = 100.0 + 10 * (i + 1)
        analista = (
            {
                "arriba": {"nivel": nivel, "media": analista_media, "muestras": [analista_media]},
                "abajo": {"nivel": None, "media": None},
            }
            if analista_media is not None
            else None
        )
        registro.predecir(
            simbolo="BTCUSDT",
            contexto=_contexto(version, analista),
            nivel=nivel,
            hacia="arriba",
            probabilidad=p,
            razonamiento=f"p{i}",
            temporalidad="4h",
            horas_vigencia=1.0,
        )
    hecha = datetime.now(UTC)
    vela = {
        "time": int((hecha + timedelta(minutes=1)).timestamp()),
        "high": 10_000.0 if tocan else 100.0,
        "low": 100.0,
        "close": 100.0,
    }
    if not tocan:
        # Que venzan: una que no tocó ni venció sigue viva.
        registro._con.execute(
            "UPDATE predicciones SET vence_en=? WHERE resuelta_en IS NULL",
            ((hecha - timedelta(hours=2)).isoformat(),),
        )
        registro._con.commit()
    registro.resolver_predicciones([vela])


def test_el_resumen_parte_por_version_y_cada_una_tiene_su_puerta(tmp_path: Path) -> None:
    ruta = tmp_path / "p.db"
    r = Registro(ruta)
    _resueltas(r, "4", PREDICCIONES_PARA_DECIDIR, tocan=True, p=0.6)
    _resueltas(r, "5", 3, tocan=False, p=0.2, analista_media=0.1)
    r.cerrar_conexion()

    foto = resumen(ruta)
    assert foto["prompt_actual"] == VERSION_PROMPT
    v4, v5 = foto["por_version"]["4"], foto["por_version"]["5"]

    assert v4["resueltas"] == 50 and v4["faltan"] == 0
    assert v4["brier"] == round((0.6 - 1) ** 2, 4) and v4["ingenuo"] == 0.0
    assert v4["tramos"] == [
        {"tramo": "60-80%", "n": 50, "brier": 0.16, "dijo": 0.6, "ocurrio": 1.0}
    ]
    assert v4["por_marco"]["4h"]["n"] == 50
    assert v4["analista"] is None, "v4 no tiene lectura del analista"

    assert v5["resueltas"] == 3 and v5["faltan"] == 47
    assert v5["brier"] is None and v5["tramos"] == [] and v5["analista"] is None, (
        "bajo la puerta, solo la cuenta"
    )


def test_trader_contra_analista_solo_donde_apostaron_al_mismo_nivel(tmp_path: Path) -> None:
    ruta = tmp_path / "p.db"
    r = Registro(ruta)
    _resueltas(r, "5", PREDICCIONES_PARA_DECIDIR, tocan=False, p=0.3, analista_media=0.1)
    r.cerrar_conexion()

    v5 = resumen(ruta)["por_version"]["5"]
    assert v5["analista"] == {
        "comparables": 50,
        "brier_trader": round(0.3**2, 4),
        "brier_analista": round(0.1**2, 4),
    }


def test_la_calibracion_propia_es_por_version_y_calla_bajo_las_50(tmp_path: Path) -> None:
    r = Registro(tmp_path / "p.db")
    _resueltas(r, "4", PREDICCIONES_PARA_DECIDIR, tocan=True, p=0.6)
    _resueltas(r, "5", 2, tocan=False, p=0.2)

    assert r.calibracion_propia("5") == {"version": "5", "resueltas": 2, "faltan": 48, "tramos": []}
    c4 = r.calibracion_propia("4")
    assert c4["resueltas"] == 50 and c4["tasa_base"] == 1.0
    assert c4["tramos"] == [{"tramo": "60-80%", "n": 50, "dijo": 0.6, "ocurrio": 1.0}]
