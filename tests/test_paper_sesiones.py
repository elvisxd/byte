"""La sesión como hecho: nombre, horas UTC y cuánto falta para el cierre de 4h.

Lo que protege es que sea un HECHO y no un veredicto —nada de «Asia es
tranquila»— y que se calcule en UTC, igual en cualquier máquina.
"""

from datetime import UTC, datetime, timedelta, timezone

from paper.sesiones import describir, minutos_al_cierre_4h, sesion_de


def _utc(h: int, m: int = 0, dia: int = 15) -> datetime:
    return datetime(2026, 9, dia, h, m, tzinfo=UTC)


def test_las_sesiones_por_hora_utc() -> None:
    assert sesion_de(_utc(3)) == "Asia"
    assert sesion_de(_utc(9)) == "Londres"
    assert sesion_de(_utc(14)) == "Londres y Nueva York"
    assert sesion_de(_utc(18)) == "Nueva York"
    assert sesion_de(_utc(22)) == "entre Nueva York y Asia"


def test_se_pasa_a_utc_desde_cualquier_zona() -> None:
    # 08:00 en Nueva York (UTC-4 en septiembre) son las 12:00 UTC: Londres.
    nueva_york = datetime(2026, 9, 15, 8, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert sesion_de(nueva_york) == "Londres"


def test_minutos_al_cierre_de_4h() -> None:
    assert minutos_al_cierre_4h(_utc(12, 0)) == 240
    assert minutos_al_cierre_4h(_utc(13, 20)) == 160
    assert minutos_al_cierre_4h(_utc(15, 59)) == 1


def test_la_linea_es_un_hecho() -> None:
    linea = describir(_utc(14, 20))
    assert linea == (
        "sesión: Londres y Nueva York (13:30–16:30 UTC) · la vela de 4h cierra en 1 h 40"
    )
    # Sábado 19 de septiembre de 2026.
    assert describir(_utc(14, 20, dia=19)).endswith("· fin de semana")
    for veredicto in ("tranquila", "volátil", "tendencia", "rango", "espera", "entrá"):
        assert veredicto not in linea
