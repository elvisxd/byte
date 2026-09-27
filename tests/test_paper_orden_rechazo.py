"""La orden con RECHAZO (prompt v7, paper/registro.py `_rechazo`).

Elvis, el 2026-09-27: que el trader use sus niveles para las órdenes límite
«evaluando el rechazo». Una orden de toque entra con solo tocar el límite; una
de rechazo espera a que una vela CERRADA de su marco toque el límite y cierre
de vuelta con mecha, y entra a ese cierre.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from paper.registro import MECHA_RECHAZO, Contexto, Registro


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
        "razon": "OB alcista en 76.500: entro si rechaza.",
        "precio_limite": 76500.0,
        "stop_loss": 76200.0,
        "take_profit": 77500.0,
        "confirmacion": "rechazo",
    }
    args.update(kw)
    return registro.dejar_orden(**args)  # type: ignore[arg-type]


def _vela(i: int, o: float, h: float, lo: float, c: float, paso: int = 900) -> dict:
    base = int(datetime.now(UTC).timestamp()) + 60
    return {"time": base + i * paso, "open": o, "high": h, "low": lo, "close": c}


def _despues(horas: float = 5) -> datetime:
    return datetime.now(UTC) + timedelta(hours=horas)


def test_un_rechazo_con_mecha_entra_al_cierre_de_la_vela(registro, contexto):
    """Tocó 76.500 con la mecha (hasta 76.400) y cerró arriba, en 76.700: la mecha
    inferior (76.600 − 76.400 = 200) es la mitad del rango (400)."""
    oid = _orden(registro, contexto)
    velas = [
        _vela(0, 76900, 77000, 76800, 76850),  # no toca
        _vela(1, 76700, 76800, 76400, 76600),  # rechazo: mecha 200 de 400
    ]
    r = registro.evaluar_ordenes(velas, ahora=_despues())
    assert r[0]["resultado"] == "disparada" and r[0]["precio"] == 76600
    op = registro.abiertas()[0]
    assert op["precio_entrada"] == 76600, "entra al CIERRE que confirmó, no al límite"
    assert op["stop_loss"] == 76200 and op["take_profit"] == 77500
    assert not [o for o in registro.ordenes_vivas() if o["id"] == oid]


def test_una_mecha_chica_no_es_rechazo_y_la_orden_sigue_esperando(registro, contexto):
    _orden(registro, contexto)
    # Tocó y cerró arriba, pero con cuerpo grande: mecha inferior 20 de 400.
    velas = [_vela(0, 76520, 76900, 76500, 76880)]
    assert (76520 - 76500) / 400 < MECHA_RECHAZO
    assert registro.evaluar_ordenes(velas, ahora=_despues()) == []
    assert len(registro.ordenes_vivas()) == 1


def test_si_la_mecha_llega_al_stop_se_cancela_sola(registro, contexto):
    _orden(registro, contexto)
    velas = [_vela(0, 76700, 76800, 76150, 76600)]
    r = registro.evaluar_ordenes(velas, ahora=_despues())
    assert r[0]["resultado"] == "cancelada" and "stop" in r[0]["nota"]
    assert registro.abiertas() == []


def test_si_cierra_del_otro_lado_el_nivel_se_rompio(registro, contexto):
    _orden(registro, contexto)
    velas = [_vela(0, 76700, 76750, 76300, 76350)]
    r = registro.evaluar_ordenes(velas, ahora=_despues())
    assert r[0]["resultado"] == "cancelada" and "rompió" in r[0]["nota"]


def test_la_vela_en_curso_no_confirma_nada(registro, contexto):
    """Una vela de 15m que todavía no cerró puede terminar en cualquier lado."""
    _orden(registro, contexto)
    velas = [_vela(0, 76700, 76800, 76400, 76600)]
    ahora = datetime.fromtimestamp(velas[0]["time"], UTC) + timedelta(minutes=10)
    assert registro.evaluar_ordenes(velas, ahora=ahora) == []
    assert len(registro.ordenes_vivas()) == 1


def test_el_rechazo_en_1h_pide_sus_velas_y_usa_su_duracion(registro, contexto):
    _orden(registro, contexto, marco_confirmacion="1h")
    pedidas: list[str] = []
    velas_1h = [_vela(0, 76700, 76800, 76400, 76600, paso=3600)]

    def velas_de(marco: str) -> list[dict]:
        pedidas.append(marco)
        return velas_1h

    # A los 30 min la vela de 1h no cerró todavía.
    ahora = datetime.fromtimestamp(velas_1h[0]["time"], UTC) + timedelta(minutes=30)
    assert registro.evaluar_ordenes([], velas_de=velas_de, ahora=ahora) == []
    r = registro.evaluar_ordenes([], velas_de=velas_de, ahora=_despues())
    assert pedidas == ["1h", "1h"] and r[0]["resultado"] == "disparada"


def test_un_rechazo_que_cierra_pasado_el_objetivo_no_entra(registro, contexto):
    _orden(registro, contexto, take_profit=76550.0)
    velas = [_vela(0, 76700, 76800, 76400, 76600)]
    r = registro.evaluar_ordenes(velas, ahora=_despues())
    assert r[0]["resultado"] == "cancelada" and "objetivo" in r[0]["nota"]


def test_el_short_es_el_espejo(registro, contexto):
    _orden(
        registro,
        contexto,
        direccion="short",
        precio_limite=77500.0,
        stop_loss=77800.0,
        take_profit=76500.0,
    )
    # Tocó 77.500 con la mecha superior (hasta 77.600) y cerró abajo, en 77.300.
    velas = [_vela(0, 77300, 77600, 77200, 77300)]
    r = registro.evaluar_ordenes(velas, ahora=_despues())
    assert r[0]["resultado"] == "disparada" and r[0]["precio"] == 77300


def test_vence_sin_rechazo(registro, contexto):
    _orden(registro, contexto, horas_vigencia=1)
    r = registro.evaluar_ordenes([], ahora=_despues(2))
    assert r[0]["resultado"] == "vencida"


def test_la_de_toque_sigue_igual_y_una_confirmacion_rara_se_rechaza(registro, contexto):
    _orden(registro, contexto, confirmacion="toque")
    velas = [{**_vela(0, 76700, 76800, 76400, 76700)}]
    r = registro.evaluar_ordenes(velas)
    assert r[0]["resultado"] == "disparada" and registro.abiertas()[0]["precio_entrada"] == 76500
    with pytest.raises(ValueError, match="confirmación"):
        _orden(registro, contexto, confirmacion="cierre", precio_limite=76000.0, stop_loss=75800.0)
    with pytest.raises(ValueError, match="marco"):
        _orden(
            registro, contexto, marco_confirmacion="5m", precio_limite=76000.0, stop_loss=75800.0
        )
