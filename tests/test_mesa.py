"""La mesa de analistas (paper/mesa.py, paper/CRITERIO_MESA_ANALISTAS.md)."""

import asyncio
from datetime import UTC, datetime, time, timedelta

from paper.mesa import (
    CUATRO_HORAS_S,
    ESPERA_TANDA_S,
    FAMILIA_BASE,
    TOPE_PIE,
    VARIANTE_EXTERNO,
    VARIANTE_MAPA,
    Mesa,
    Pregunta,
    Respuesta,
    cierre_pendiente,
    consenso,
    familias_de,
    interpretar,
    lectura,
    mensaje,
    para_el_trader,
    parte_html,
    pregunta,
    ronda,
    sesgo_de,
    tarjeta,
    tasa_base_pct,
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


def test_la_pregunta_la_fija_el_codigo_a_un_porcentaje_y_24_horas():
    """Si cada analista eligiera sus niveles, dos respuestas no se podrían comparar:
    es exactamente lo que la mesa viene a arreglar. Desde el 27, a ±1.5%: ±1 ATR de
    1h eran ~160 dólares con el mercado quieto."""
    p = pregunta(0, T0, _velas(40, precio=84000.0, rango=160.0))
    assert p is not None
    assert p.atr == 160.0
    assert (p.arriba, p.abajo) == (85260.0, 82740.0)
    assert p.vence_en - p.hecha_en == timedelta(hours=24)
    assert p.base is None, "con 40 velas no hay 30 muestras para la tasa base"


def test_la_tasa_base_de_la_pregunta_la_mide_el_codigo_en_porcentaje():
    # Rango de ±2% en cada vela: todo nivel a ±1.5% se toca siempre.
    assert tasa_base_pct(_velas(80, precio=100.0, rango=4.0)) == (1.0, 55)
    # Rango de ±1%: nunca.
    assert tasa_base_pct(_velas(80, precio=100.0, rango=2.0)) == (0.0, 55)
    assert tasa_base_pct(_velas(40)) is None


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
    assert "el trader ve la mesa" in texto


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


# ── fase 1b: el contexto externo en A/B ─────────────────────────────────────


class _LlmQueMira:
    """Contesta distinto si el mensaje trae el bloque externo, y guarda lo que vio."""

    def __init__(self, actual: str = "m") -> None:
        self.actual, self.vistos = actual, []

    async def ainvoke(self, mensajes):
        texto = mensajes[-1].content
        self.vistos.append(texto)
        con = "CONTEXTO EXTERNO" in texto

        class R:
            content = (
                "PROBABILIDADES: arriba 40% | abajo 60%\nVEREDICTO: bajista — dólar fuerte"
                if con
                else "PROBABILIDADES: arriba 55% | abajo 45%\nVEREDICTO: neutral — rango"
            )

        return R()


class _Ctx:
    bloque = "═══ CONTEXTO EXTERNO (x) ═══\nMacro 24h: DXY 101 +0.5%\nCalendario: hoy 08:30 CPI"
    datos = {"dxy": {"precio": 101.0}}
    fallos = {"etf": "sin dato"}


def test_el_ab_pregunta_dos_veces_y_lo_unico_que_cambia_es_el_bloque(tmp_path):
    """La medida de la fase 1b es la diferencia entre variantes de la MISMA familia
    en la MISMA ronda: si cambiara algo más que el bloque, no se sabría a qué
    atribuirla. Y la segunda tanda espera a la primera (Groq cuenta por minuto)."""
    mesa = Mesa(str(tmp_path / "mesa.db"))
    llm = _LlmQueMira()
    esperas: list[float] = []

    async def dormir(s: float) -> None:
        esperas.append(s)

    aviso = asyncio.run(
        ronda(
            int(T0.timestamp()),
            mesa,
            {"gemini": llm},
            velas=lambda _m, _n: _velas(40),
            mapa=lambda: "MAPA",
            avisar=lambda _t: True,
            ahora=T0 + timedelta(minutes=50),
            contexto=lambda: _Ctx(),
            dormir=dormir,
        )
    )
    assert len(llm.vistos) == 2 and esperas == [ESPERA_TANDA_S]
    sin, con = llm.vistos
    assert con == sin + "\n\n" + _Ctx.bloque
    filas = mesa._con.execute(
        "SELECT variante, p_arriba, veredicto FROM respuestas ORDER BY id"
    ).fetchall()
    assert [tuple(f) for f in filas] == [
        (VARIANTE_MAPA, 0.55, "neutral"),
        (VARIANTE_EXTERNO, 0.40, "bajista"),
    ]
    guardado = mesa._con.execute("SELECT bloque, fallos FROM contextos").fetchone()
    assert guardado[0] == _Ctx.bloque and "etf" in guardado[1]
    assert aviso is not None
    assert "con contexto: bajista · ↑ 40% · ↓ 60%" in aviso
    assert "Macro 24h: DXY 101 +0.5%" in aviso and "Calendario: hoy 08:30 CPI" in aviso


def test_sin_contexto_la_ronda_es_la_de_la_fase_1(tmp_path):
    """Si las fuentes fallan todas, la mesa no pierde la ronda: queda la variante
    mapa, que es la serie de la fase 1 y no se corta."""
    mesa = Mesa(str(tmp_path / "mesa.db"))
    llm = _LlmQueMira()

    def roto():
        raise RuntimeError("sin red")

    asyncio.run(
        ronda(
            int(T0.timestamp()),
            mesa,
            {"gemini": llm},
            velas=lambda _m, _n: _velas(40),
            mapa=lambda: "MAPA",
            avisar=lambda _t: True,
            ahora=T0 + timedelta(minutes=50),
            contexto=roto,
        )
    )
    assert len(llm.vistos) == 1
    assert [f[0] for f in mesa._con.execute("SELECT variante FROM respuestas")] == [VARIANTE_MAPA]


def test_una_mesa_db_de_la_fase_1_se_migra_sin_perder_nada(tmp_path):
    """La del volumen ya tiene rondas del 25: sus respuestas son de la variante mapa."""
    import sqlite3

    ruta = str(tmp_path / "mesa.db")
    con = sqlite3.connect(ruta)
    con.executescript(
        """CREATE TABLE rondas (id INTEGER PRIMARY KEY, cierre_4h INTEGER NOT NULL UNIQUE,
             hecha_en TEXT NOT NULL, precio REAL NOT NULL, atr REAL NOT NULL, arriba REAL NOT NULL,
             abajo REAL NOT NULL, vence_en TEXT NOT NULL, toco_arriba INTEGER, toco_abajo INTEGER,
             resuelta_en TEXT);
           CREATE TABLE respuestas (id INTEGER PRIMARY KEY, ronda_id INTEGER NOT NULL,
             familia TEXT NOT NULL, modelo TEXT, p_arriba REAL, p_abajo REAL, veredicto TEXT,
             porque TEXT, texto TEXT, fallo TEXT);
           INSERT INTO rondas VALUES (1, 100, '2026-09-25T00:50:00+00:00', 1, 1, 2, 0,
             '2026-09-26T00:50:00+00:00', NULL, NULL, NULL);
           INSERT INTO respuestas (ronda_id, familia, p_arriba, p_abajo)
             VALUES (1, 'gemini', .5, .5);"""
    )
    con.commit()
    con.close()
    mesa = Mesa(ruta)
    assert [tuple(f) for f in mesa._con.execute("SELECT familia, variante FROM respuestas")] == [
        ("gemini", VARIANTE_MAPA)
    ]
    # Sus rondas eran de la pregunta a ±1 ATR: se guardan, pero aparte del informe.
    assert mesa._con.execute("SELECT pregunta FROM rondas").fetchone()[0] == "1h ±1 ATR 24h"
    assert "1 rondas de una pregunta anterior, aparte" in mesa.informe()


# ── la tasa base, como un analista más ──────────────────────────────────────


def test_la_ronda_guarda_la_vara_por_variante_sin_contarla_como_analista(tmp_path):
    mesa = Mesa(str(tmp_path / "mesa.db"))

    async def dormir(_s: float) -> None:
        return None

    aviso = asyncio.run(
        ronda(
            int(T0.timestamp()),
            mesa,
            {"gemini": _LlmQueMira()},
            velas=lambda _m, _n: _velas(80, rango=4.0),
            mapa=lambda: "MAPA",
            avisar=lambda _t: True,
            ahora=T0 + timedelta(minutes=50),
            contexto=lambda: _Ctx(),
            dormir=dormir,
        )
    )
    filas = mesa._con.execute(
        "SELECT familia, modelo, variante, p_arriba, p_abajo FROM respuestas"
        " WHERE familia = ? ORDER BY id",
        (FAMILIA_BASE,),
    ).fetchall()
    assert [tuple(f) for f in filas] == [
        (FAMILIA_BASE, "mapa", VARIANTE_MAPA, 1.0, 1.0),
        (FAMILIA_BASE, "mapa", VARIANTE_EXTERNO, 1.0, 1.0),
    ]
    assert aviso is not None
    assert "Tasa base (la vara): ↑ 100% · ↓ 100%" in aviso
    # Ni en el consenso ni como línea de familia.
    assert "Consenso: ↑ 55% · ↓ 45%" in aviso and "• tasa base" not in aviso


def test_sin_velas_para_la_tasa_base_la_ronda_sigue_sin_vara(tmp_path):
    mesa = Mesa(str(tmp_path / "mesa.db"))
    aviso = asyncio.run(
        ronda(
            int(T0.timestamp()),
            mesa,
            {"gemini": _LlmQueMira()},
            velas=lambda _m, _n: _velas(40),
            mapa=lambda: "MAPA",
            avisar=lambda _t: True,
            ahora=T0 + timedelta(minutes=50),
        )
    )
    assert aviso is not None and "Tasa base" not in aviso
    assert [f[0] for f in mesa._con.execute("SELECT familia FROM respuestas")] == ["gemini"]


def _rondas_con_vara(mesa: Mesa, n: int, familia: tuple[float, float], base: float) -> None:
    """n rondas resueltas: arriba toca, abajo no (ninguna vela baja de 98)."""
    for k in range(n):
        p = Pregunta(int(T0.timestamp()) + k * CUATRO_HORAS_S, T0, 100.0, 2.0, 101.0, 98.0, T0)
        mesa.guardar(
            p,
            [
                Respuesta("a", p_arriba=familia[0], p_abajo=familia[1]),
                Respuesta("b", p_arriba=0.5, p_abajo=0.5),
                Respuesta(FAMILIA_BASE, "mapa", base, base),
            ],
        )
    mesa.resolver(_velas(10, precio=100.0, rango=2.0, desde=T0), T0 + timedelta(days=40))


def test_la_vara_no_mueve_la_discrepancia_ni_da_skill_antes_de_la_puerta(tmp_path):
    mesa = Mesa(str(tmp_path / "mesa.db"))
    _rondas_con_vara(mesa, 3, (0.9, 0.1), base=0.1)
    informe = mesa.informe()
    # a y b: media 0.7/0.3 → 20 pts; si la vara contara, sería otra cifra.
    assert "a [mapa]: contestó 3/3 · resueltas 3 · se aparta del resto 20 pts" in informe
    assert "tasa base [mapa]: contestó 3/3 · resueltas 3" in informe
    assert "Brier" not in informe.split("\n", 1)[1] and "vs tasa base" not in informe


def test_desde_la_puerta_cada_familia_lleva_su_skill_contra_la_vara(tmp_path):
    """Arriba toca siempre y abajo nunca. a (0.9/0.1): Brier 0.01. La vara
    (0.5/0.5): 0.25. Skill de a = 1 − 0.01/0.25 = +0.960; b (0.5/0.5) empata: 0."""
    mesa = Mesa(str(tmp_path / "mesa.db"))
    _rondas_con_vara(mesa, 50, (0.9, 0.1), base=0.5)
    lineas = {ln.split(":")[0].strip(): ln for ln in mesa.informe().splitlines()[1:]}
    assert "Brier 0.0100 · vs tasa base +0.960 en 50" in lineas["a [mapa]"]
    assert "vs tasa base +0.000 en 50" in lineas["b [mapa]"]
    assert "Brier 0.2500" in lineas["tasa base [mapa]"]
    assert "vs tasa base" not in lineas["tasa base [mapa]"]
    assert "se aparta" not in lineas["tasa base [mapa]"]


# ── el aviso con tarjeta ────────────────────────────────────────────────────


def _ronda_de_ejemplo() -> list[Respuesta]:
    return [
        Respuesta("gemini", "g", 0.62, 0.31, "alcista", "barrió el mínimo de Asia"),
        Respuesta("openrouter", "o", 0.55, 0.38, "alcista", "CVD comprador <fuerte>"),
        Respuesta("groq", "q", 0.45, 0.42, "neutral", "rango de 4h"),
        Respuesta("nvidia", "n", fallo="Error code: 500"),
        Respuesta("gemini", "g", 0.58, 0.35, "alcista", "x", variante=VARIANTE_EXTERNO),
        Respuesta(FAMILIA_BASE, "mapa", 0.38, 0.38),
    ]


class _Etiquetas:
    """Cuenta que cada etiqueta que abre, cierra: Telegram rechaza el parte entero."""

    def __init__(self, texto: str) -> None:
        from html.parser import HTMLParser

        self.pila: list[str] = []
        self.mal = False
        yo = self

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                yo.pila.append(tag)

            def handle_endtag(self, tag):
                if not yo.pila or yo.pila.pop() != tag:
                    yo.mal = True

        P().feed(texto)

    @property
    def bien(self) -> bool:
        return not self.mal and not self.pila


def test_el_veredicto_de_la_mesa_es_la_mayoria_de_la_variante_mapa():
    assert sesgo_de(_ronda_de_ejemplo()) == ("alcista", "2 de 3")
    empate = [Respuesta("a", veredicto="alcista"), Respuesta("b", veredicto="bajista")]
    assert sesgo_de(empate) == ("dividida", "1 alcista · 1 bajista")
    assert sesgo_de([Respuesta("a", fallo="500")]) == ("sin veredicto", "")


def test_el_parte_abre_con_el_veredicto_y_los_numeros():
    """La primera línea es la que se lee en la notificación del teléfono."""
    parte = parte_html(
        _pregunta_fija(),
        _ronda_de_ejemplo(),
        "Macro 24h: NDX 30,608 +0.2%\nCalendario: mañana 08:30 PCE",
        base=0.38,
        eventos=["mañana 08:30 Core PCE"],
    )
    primera = parte.split("\n", 1)[0]
    # ↑ 54% contra una base de 38%: la mesa espera ese movimiento (por los números).
    assert primera == "<b>🟢 Mesa BTC · sesgo alcista</b>"
    assert "Ninguno de los dos: ≥9% · votos: 2 alcista · 1 neutral" in parte
    assert "→ <b>54%</b> · base 38%" in parte and "→ <b>37%</b> · base 38%" in parte
    # La clave: del que votó con la mesa, el más cerca del consenso (54/37).
    assert "<b>Clave:</b> CVD comprador &lt;fuerte&gt;" in parte
    assert "<b>Riesgo:</b> mañana 08:30 Core PCE" in parte
    assert "<pre>" in parte and "sin respuesta" in parte and "58/35" in parte
    assert "<blockquote expandable>" in parte and "<b>Macro 24h</b> NDX 30,608 +0.2%" in parte
    assert "tasa base" not in parte.split("<pre>")[1].split("</pre>")[0]
    assert "<fuerte>" not in parte, "lo que escribe un modelo va escapado"
    assert _Etiquetas(parte).bien


def test_un_parte_largo_se_recorta_por_lo_plegado_y_cabe_en_el_pie():
    largas = [
        Respuesta(f"f{i}", "m", 0.5, 0.4, "neutral", "razón muy larga " * 10) for i in range(6)
    ]
    parte = parte_html(_pregunta_fija(), largas, "Macro 24h: " + "x " * 90, base=0.3)
    from html import unescape
    from re import sub

    assert len(unescape(sub(r"<[^>]+>", "", parte))) <= TOPE_PIE
    # 50% y 40% contra una base de 30%: los dos lados sobre la base.
    assert parte.startswith("<b>⚡ Mesa BTC · volátil, sin lado</b>") and _Etiquetas(parte).bien


def test_sin_respuestas_el_parte_lo_dice():
    parte = parte_html(_pregunta_fija(), [Respuesta("a", fallo="500")])
    assert "nadie contestó" in parte and _Etiquetas(parte).bien


def test_la_tarjeta_lleva_los_numeros_y_no_a_la_vara():
    velas = _velas(60)
    t = tarjeta(_pregunta_fija(), _ronda_de_ejemplo(), 0.38, velas)
    assert t is not None
    assert (t["p_arriba"], t["p_abajo"], t["base"]) == (0.54, 0.37, 0.38)
    assert (t["sesgo"], t["lectura"], t["votos"]) == (
        "alcista",
        "sesgo alcista",
        "2 alcista · 1 neutral",
    )
    assert [f["nombre"] for f in t["familias"]] == ["gemini", "openrouter", "groq", "nvidia"]
    assert t["con_contexto"] == {"p_arriba": 0.58, "p_abajo": 0.35}
    assert len(t["velas"]) == 48 and t["velas"][-1][0] == velas[-1]["time"]
    assert tarjeta(_pregunta_fija(), [Respuesta("a", fallo="x")], None, velas) is None


def _ronda_con_aviso(tmp_path, avisar_mesa):
    avisos: list[str] = []
    asyncio.run(
        ronda(
            int(T0.timestamp()),
            Mesa(str(tmp_path / "mesa.db")),
            {"gemini": _LlmQueMira()},
            velas=lambda _m, _n: _velas(80, rango=4.0),
            mapa=lambda: "MAPA",
            avisar=lambda t: avisos.append(t) or True,
            ahora=T0 + timedelta(minutes=50),
            avisar_mesa=avisar_mesa,
        )
    )
    return avisos


def test_con_el_aviso_de_la_mesa_no_se_manda_el_de_siempre(tmp_path):
    cuerpos: list[dict] = []
    avisos = _ronda_con_aviso(tmp_path, lambda c: cuerpos.append(c) or True)
    assert avisos == [] and len(cuerpos) == 1
    c = cuerpos[0]
    # 55/45 con una base de 100% (velas que siempre tocan): ninguno la pasa.
    assert c["html"].startswith("<b>⚪ Mesa BTC · lateral, inclinación alcista</b>")
    assert c["texto"].startswith("🧑‍💼 Mesa de analistas") and c["tarjeta"]["base"] == 1.0


def test_si_el_aviso_de_la_mesa_no_sale_va_el_de_siempre(tmp_path):
    """Un panel viejo sin la ruta, la imagen rota o Telegram caído: el texto
    plano de siempre llega igual."""
    (tmp_path / "a").mkdir()
    assert len(_ronda_con_aviso(tmp_path / "a", lambda _c: False)) == 1

    def roto(_c):
        raise RuntimeError("boom")

    (tmp_path / "b").mkdir()
    assert len(_ronda_con_aviso(tmp_path / "b", roto)) == 1


# ── la mesa, para el trader (prompt v6) ─────────────────────────────────────


def _mesa_con_una_ronda(tmp_path, hecha: datetime) -> str:
    ruta = str(tmp_path / "mesa.db")
    mesa = Mesa(ruta)
    p = Pregunta(int(T0.timestamp()), hecha, 84120.0, 160.0, 84280.0, 83960.0, hecha)

    class Ctx:
        bloque = "═══ CONTEXTO EXTERNO (x) ═══\nMacro 24h: DXY 101 +0.5%"
        datos: dict = {}
        fallos: dict = {}

    mesa.guardar(p, _ronda_de_ejemplo(), Ctx())
    return ruta


def test_el_trader_ve_la_ultima_ronda_con_su_contexto(tmp_path):
    ruta = _mesa_con_una_ronda(tmp_path, T0)
    bloque, sello = para_el_trader(ruta, T0 + timedelta(hours=1))
    assert bloque.startswith("═══ MESA DE ANALISTAS (")
    assert "es un dato, no una orden" in bloque
    assert "¿toca 84,280 (+0.2%)? ¿toca 83,960 (−0.2%)?" in bloque
    # Solo gemini contestó con contexto en el ejemplo: esa es la variante que se ve.
    assert "Consenso: ↑ 58% · ↓ 35%" in bloque and "tasa base 38%" in bloque
    assert "Macro 24h: DXY 101 +0.5%" in bloque
    assert sello == {
        "ronda": 1,
        "cierre_4h": int(T0.timestamp()),
        "variante": VARIANTE_EXTERNO,
        "p_arriba": 0.58,
        "p_abajo": 0.35,
        "discrepancia": 0.0,
        "base": 0.38,
    }


def test_una_ronda_vieja_o_sin_mesa_no_se_muestra(tmp_path):
    ruta = _mesa_con_una_ronda(tmp_path, T0)
    assert para_el_trader(ruta, T0 + timedelta(hours=6)) == ("", None)
    assert para_el_trader(str(tmp_path / "no-existe.db"), T0) == ("", None)
    (tmp_path / "rota.db").write_text("esto no es sqlite")
    assert para_el_trader(str(tmp_path / "rota.db"), T0) == ("", None)


def test_sin_contexto_se_ve_la_variante_mapa(tmp_path):
    ruta = str(tmp_path / "mesa.db")
    mesa = Mesa(ruta)
    p = Pregunta(int(T0.timestamp()), T0, 100.0, 2.0, 102.0, 98.0, T0)
    mesa.guardar(p, [r for r in _ronda_de_ejemplo() if r.variante == VARIANTE_MAPA])
    bloque, sello = para_el_trader(ruta, T0)
    assert sello is not None and sello["variante"] == VARIANTE_MAPA
    assert "Consenso: ↑ 54% · ↓ 37%" in bloque
    assert "lectura: sesgo alcista (votos: 2 alcista · 1 neutral)" in bloque
    assert "· nvidia sin respuesta" in bloque and "CONTEXTO EXTERNO" not in bloque


def test_la_vuelta_del_trader_lleva_la_mesa_y_la_sella(tmp_path):
    """El bloque va al final del mensaje, después del analista, y cada
    escritura de la vuelta sella qué ronda vio (`extra.mesa`)."""
    import tools.paper as herramientas
    from paper.sesion import una_vuelta
    from paper.trace import TraceDeSesion

    recibido: dict = {}

    class _Grafo:
        async def ainvoke(self, estado, _config):
            recibido.update(estado)

    trace = TraceDeSesion(sesion_id="s", modelo="m", simbolo="BTCUSDT")
    asyncio.run(
        una_vuelta(
            _Grafo(),
            trace,
            1,
            precarga="═══ ESTADO ═══\nnada",
            mesa=lambda: ("═══ MESA DE ANALISTAS (x) ═══", {"ronda": 7}),
        )
    )
    assert recibido["messages"][1]["content"].endswith("═══ MESA DE ANALISTAS (x) ═══")
    assert herramientas._VUELTA["mesa"] == {"ronda": 7}

    def rota():
        raise RuntimeError("boom")

    asyncio.run(una_vuelta(_Grafo(), trace, 2, precarga="═══ ESTADO ═══\nnada", mesa=rota))
    assert "═══ MESA DE ANALISTAS" not in recibido["messages"][1]["content"]
    assert herramientas._VUELTA["mesa"] is None, "la mesa rota no cuesta la vuelta ni arrastra"


def test_la_lectura_sale_de_los_numeros_contra_la_base_no_de_los_votos():
    """El caso de Elvis del 27: 3 de 4 votaron bajista, pero ↑ 24% · ↓ 32% con base
    31% no es un sesgo: ninguno pasa la base. Es lateral, apenas inclinado abajo."""
    assert lectura(0.24, 0.32, 0.31) == ("lateral", "lateral, inclinación bajista")
    assert lectura(0.30, 0.33, 0.31) == ("lateral", "lateral, sin sesgo")
    assert lectura(0.25, 0.45, 0.31) == ("bajista", "sesgo bajista")
    assert lectura(0.52, 0.30, 0.31) == ("alcista", "sesgo alcista")
    assert lectura(0.45, 0.48, 0.31) == ("volatil", "volátil, sin lado")
    assert lectura(0.40, 0.20, None)[0] == "alcista", "sin base, contra el lado más bajo"


def test_vigilar_deja_el_informe_en_el_log_una_vez_por_dia(tmp_path, capsys):
    """El informe sale al arrancar y no se repite en el mismo día (2026-10-02)."""
    from paper.mesa import vigilar

    mesa = Mesa(str(tmp_path / "mesa.db"))
    parar = asyncio.Event()
    vueltas = {"n": 0}

    async def dormir(_s):
        vueltas["n"] += 1
        if vueltas["n"] >= 3:
            parar.set()

    asyncio.run(
        vigilar(
            mesa,
            {},
            50,
            lambda _m: False,
            lambda _marco, _n: [],
            lambda: "",
            lambda _t: True,
            parar,
            dormir=dormir,
        )
    )
    lineas = [
        linea
        for linea in capsys.readouterr().out.splitlines()
        if linea.startswith("[mesa-informe] Mesa de analistas")
    ]
    assert len(lineas) == 1
