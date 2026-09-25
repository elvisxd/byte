"""La mesa de analistas (paper/mesa.py, paper/CRITERIO_MESA_ANALISTAS.md)."""

import asyncio
from datetime import UTC, datetime, time, timedelta

from paper.mesa import (
    CUATRO_HORAS_S,
    Mesa,
    Pregunta,
    Respuesta,
    cierre_pendiente,
    consenso,
    familias_de,
    interpretar,
    mensaje,
    pregunta,
    ronda,
)

T0 = datetime(2026, 9, 25, 16, 0, tzinfo=UTC)  # un cierre de 4h exacto


def _velas(
    n: int, precio: float = 100.0, rango: float = 2.0, desde: datetime = T0, paso_s: int = 3600
) -> list[dict]:
    return [
        {
            "time": int(desde.timestamp()) + i * paso_s,
            "open": precio,
            "high": precio + rango / 2,
            "low": precio - rango / 2,
            "close": precio,
        }
        for i in range(n)
    ]


def siempre(_: datetime) -> bool:
    return True


def test_la_pregunta_la_fija_el_codigo_a_un_atr_y_24_horas():
    """Si cada analista eligiera sus niveles, dos respuestas no se podrían comparar:
    es exactamente lo que la mesa viene a arreglar."""
    p = pregunta(0, T0, _velas(40))
    assert p is not None
    assert p.atr == 2.0
    assert (p.arriba, p.abajo) == (102.0, 98.0)
    assert p.vence_en - p.hecha_en == timedelta(hours=24)


def test_la_ronda_espera_detras_de_los_brazos():
    """Los brazos despiertan en el mismo cierre y su vuelta dura 25-40 min: la mesa
    antes de la espera les competiría por la cuota por minuto (Groq)."""
    cierre = int(T0.timestamp())
    assert cierre_pendiente(T0 + timedelta(minutes=20), set(), 50, siempre) is None
    assert cierre_pendiente(T0 + timedelta(minutes=55), set(), 50, siempre) == cierre


def test_un_cierre_ya_contestado_no_se_repite_tras_un_reinicio():
    """El contenedor se reinicia en cada despliegue: sin esto, dos avisos por cierre."""
    cierre = int(T0.timestamp())
    assert cierre_pendiente(T0 + timedelta(minutes=55), {cierre}, 50, siempre) is None


def test_un_cierre_perdido_no_se_contesta_horas_tarde():
    """Con el servicio caído, el mapa de tres horas después ya no es el de ese cierre."""
    assert cierre_pendiente(T0 + timedelta(hours=3), set(), 50, siempre) is None


def test_fuera_de_la_ventana_no_hay_ronda():
    """Las de las 00 y las 04 serían avisos de madrugada y cuota gastada sin nadie mirando."""

    def nunca(_: datetime) -> bool:
        return False

    assert cierre_pendiente(T0 + timedelta(minutes=55), set(), 50, nunca) is None


def test_la_respuesta_trae_probabilidades_y_veredicto():
    texto = (
        "Rango en 1h.\nPROBABILIDADES: arriba 62% | abajo 30%\n"
        "VEREDICTO: alcista — barrió el mínimo y recuperó"
    )
    r = interpretar("gemini", "gemini-x", texto)
    assert (r.p_arriba, r.p_abajo, r.veredicto) == (0.62, 0.30, "alcista")
    assert r.porque == "barrió el mínimo y recuperó"
    assert not r.fallo


def test_sin_la_linea_de_probabilidades_la_respuesta_no_cuenta():
    """Un número inventado a partir de prosa sería puntuar lo que el modelo no dijo."""
    r = interpretar("groq", "gpt", "Creo que sube bastante. VEREDICTO: alcista")
    assert r.p_arriba is None and r.fallo


def test_el_consenso_y_la_discrepancia():
    rs = [
        Respuesta("a", p_arriba=0.6, p_abajo=0.3),
        Respuesta("b", p_arriba=0.4, p_abajo=0.3),
        Respuesta("c", fallo="agotado"),
    ]
    ma, mb, dis = consenso(rs)  # type: ignore[misc]
    assert round(ma, 3) == 0.5 and round(mb, 3) == 0.3 and round(dis, 3) == 0.1


def test_openrouter_sin_free_no_entra_en_la_mesa():
    """El mismo id sin `:free` se cobra y contesta 200: no falla nada, llega la factura."""
    f = familias_de(["openrouter=openrouter/x/y,openrouter/x/z:free", "groq=groq/a", "vacia="])
    assert f == {"openrouter": ["openrouter/x/z:free"], "groq": ["groq/a"]}


def _pregunta_fija() -> Pregunta:
    return Pregunta(int(T0.timestamp()), T0, 100.0, 2.0, 102.0, 98.0, T0 + timedelta(hours=24))


def test_se_resuelve_solo_con_velas_posteriores_a_la_pregunta(tmp_path):
    """Las 200 velas de 15m traen ~50 h de pasado: sin el filtro, toda pregunta se
    resolvería contra lo que ya había pasado."""
    mesa = Mesa(str(tmp_path / "mesa.db"))
    mesa.guardar(_pregunta_fija(), [Respuesta("a", p_arriba=0.5, p_abajo=0.5)])
    antes = _velas(4, rango=20.0, desde=T0 - timedelta(hours=2), paso_s=900)  # tocaría los dos
    despues = _velas(4, rango=1.0, desde=T0, paso_s=900)
    assert mesa.resolver(antes + despues, T0 + timedelta(hours=1)) == 0
    fila = mesa._con.execute("SELECT toco_arriba, toco_abajo, resuelta_en FROM rondas").fetchone()
    assert tuple(fila) == (None, None, None)


def test_toca_uno_y_vence_el_otro(tmp_path):
    mesa = Mesa(str(tmp_path / "mesa.db"))
    mesa.guardar(_pregunta_fija(), [])
    sube = [
        {
            "time": int((T0 + timedelta(hours=2)).timestamp()),
            "open": 100,
            "high": 102.5,
            "low": 99.5,
            "close": 102,
        }
    ]
    assert mesa.resolver(sube, T0 + timedelta(hours=3)) == 0  # tocó arriba, abajo sigue vivo
    assert mesa.resolver(sube, T0 + timedelta(hours=25)) == 1
    fila = mesa._con.execute("SELECT toco_arriba, toco_abajo FROM rondas").fetchone()
    assert tuple(fila) == (1, 0)


def test_el_aviso_nombra_a_cada_familia_y_dice_que_es_en_sombra():
    texto = mensaje(
        _pregunta_fija(),
        [
            Respuesta("gemini", "gemini-3.8-flash", 0.62, 0.30, "alcista", "barrió el mínimo"),
            Respuesta("nvidia", "nvidia/mistral", fallo="Error code: 500"),
        ],
    )
    assert "gemini" in texto and "alcista" in texto and "62%" in texto
    assert "nvidia" in texto and "sin respuesta" in texto
    assert "el trader no ve la mesa" in texto


class _LlmFalso:
    def __init__(self, texto: str, actual: str) -> None:
        self.texto, self.actual = texto, actual

    async def ainvoke(self, _mensajes):
        class R:
            content = self.texto

        return R()


class _LlmRoto:
    actual = "roto"

    async def ainvoke(self, _mensajes):
        raise RuntimeError("429 agotado")


def test_una_ronda_entera_guarda_avisa_y_sobrevive_a_una_familia_caida(tmp_path):
    """Una clave agotada es lo normal en capa gratuita: le cuesta su línea a esa
    familia, no la ronda a las demás."""
    mesa = Mesa(str(tmp_path / "mesa.db"))
    avisos: list[str] = []
    llms = {
        "gemini": _LlmFalso(
            "PROBABILIDADES: arriba 55% | abajo 40%\nVEREDICTO: neutral — rango", "g"
        ),
        "groq": _LlmRoto(),
    }
    aviso = asyncio.run(
        ronda(
            int(T0.timestamp()),
            mesa,
            llms,
            velas=lambda _m, _n: _velas(40),
            mapa=lambda: "MAPA",
            avisar=lambda t: avisos.append(t) or True,
            ahora=T0 + timedelta(minutes=50),
        )
    )
    assert aviso and avisos == [aviso]
    assert mesa.hechas() == {int(T0.timestamp())}
    filas = mesa._con.execute(
        "SELECT familia, p_arriba, fallo FROM respuestas ORDER BY familia"
    ).fetchall()
    assert [tuple(f) for f in filas] == [("gemini", 0.55, ""), ("groq", None, "429 agotado")]


def test_sin_brier_antes_de_la_puerta(tmp_path):
    """La misma puerta de 50 que los brazos: enseñarlo antes invita a ajustar contra ruido."""
    mesa = Mesa(str(tmp_path / "mesa.db"))
    for k in range(3):
        p = Pregunta(int(T0.timestamp()) + k * CUATRO_HORAS_S, T0, 100.0, 2.0, 102.0, 98.0, T0)
        mesa.guardar(
            p,
            [Respuesta("a", p_arriba=0.9, p_abajo=0.1), Respuesta("b", p_arriba=0.5, p_abajo=0.5)],
        )
    mesa.resolver([], T0 + timedelta(hours=1))
    informe = mesa.informe()
    assert "resueltas 3" in informe and "Brier" not in informe.split("\n", 1)[1]
    assert "se aparta del resto 20 pts" in informe


def test_la_ventana_del_vigia_se_mira_en_hora_local_del_cierre():
    """El contenedor corre con TZ de Nueva York: los cierres de las 08, 12, 16 y 20
    locales son los de la ventana, aunque la ronda caiga a las 20:50."""
    from paper.vigia import en_ventana

    assert en_ventana(datetime(2026, 9, 25, 20, 0))
    assert not en_ventana(datetime.combine(datetime(2026, 9, 25).date(), time(0, 0)))
