"""El contexto externo de la mesa (paper/contexto_externo.py, fase 1b del criterio)."""

from datetime import UTC, datetime

from paper.contexto_externo import (
    TOPE_CHARS,
    combinar_titulares,
    formatear,
    leer_calendario,
    leer_deribit_opciones,
    leer_dominancia,
    leer_farside,
    leer_finnhub,
    leer_fng,
    leer_okx_funding,
    leer_rss,
    leer_stablecoins,
    leer_telegram,
    leer_yahoo,
    reunir,
    sesion,
)

AHORA = datetime(2026, 9, 25, 16, 50, tzinfo=UTC)  # viernes, 12:50 en Nueva York
TS = AHORA.timestamp()


def _chart(precios: list[float | None], paso_h: float = 1.0) -> dict:
    n = len(precios)
    ts = [int(TS - (n - 1 - i) * paso_h * 3600) for i in range(n)]
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"close": precios}]}}]}}


def test_yahoo_compara_con_la_vela_de_hace_24_horas():
    """El cambio es de 24 h, el mismo reloj que BTC: contra el cierre de ayer a la
    misma hora, no contra el del día bursátil anterior."""
    precios = [100.0] * 5 + [110.0] * 20 + [None, 121.0]
    d = leer_yahoo(_chart(precios), TS)
    assert d is not None and d["precio"] == 121.0 and d["hace24h"] == 100.0


def test_yahoo_sin_datos_no_inventa():
    assert leer_yahoo({"chart": {"result": None}}, TS) is None
    assert leer_yahoo(_chart([None, None]), TS) is None


def test_farside_toma_el_ultimo_dia_con_total_y_los_negativos_entre_parentesis():
    md = """| Date | IBIT | FBTC | Total |
|---|---|---|---|
| 24 Sep 2026 | 120.5 | (30.0) | 90.5 |
| 25 Sep 2026 | (200.1) | 10.0 | (190.1) |
| 26 Sep 2026 | - | - | - |"""
    assert leer_farside(md) == {"dia": "25 Sep 2026", "neto_musd": -190.1}


def test_stablecoins_y_su_cambio_semanal():
    serie = [{"totalCirculatingUSD": {"peggedUSD": 300e9 + i * 1e9}} for i in range(10)]
    d = leer_stablecoins(serie)
    assert d is not None and d["total"] == 309e9
    assert round(d["cambio7d"], 2) == round((309 - 302) / 302 * 100, 2)


def test_funding_de_okx_en_porcentaje():
    d = leer_okx_funding({"data": [{"fundingRate": "0.0001"}, {"fundingRate": "0.0003"}]})
    assert d is not None and round(d["ultimo"], 6) == 0.01 and round(d["media7d"], 6) == 0.02


def test_opciones_put_call_y_el_vencimiento_grande_proximo():
    """Solo cuentan los vencimientos de los próximos 8 días: el de diciembre, con
    más OI, no es «el vencimiento que viene»."""
    filas = [
        {"instrument_name": "BTC-26SEP26-60000-C", "open_interest": 1000},
        {"instrument_name": "BTC-26SEP26-50000-P", "open_interest": 500},
        {"instrument_name": "BTC-25DEC26-80000-C", "open_interest": 9000},
        {"instrument_name": "BTC-PERPETUAL", "open_interest": 99999},
    ]
    d = leer_deribit_opciones({"result": filas}, spot=80000.0, ahora=AHORA)
    assert d is not None
    assert round(d["put_call"], 3) == round(500 / 10000, 3)
    assert d["vto"] == "26-Sep" and d["vto_musd"] == 1500 * 80000 / 1e6


def test_fear_and_greed():
    j = {
        "data": [
            {"value": "34", "value_classification": "Fear"},
            {"value": "38", "value_classification": "Fear"},
        ]
    }
    assert leer_fng(j) == {"valor": 34, "clase": "Fear", "ayer": 38}


def test_calendario_solo_eeuu_de_impacto_alto_hoy_y_manana():
    eventos = [
        {
            "title": "CPI y/y",
            "country": "USD",
            "date": "2026-09-25T08:30:00-04:00",
            "impact": "High",
        },
        {
            "title": "FOMC Statement",
            "country": "USD",
            "date": "2026-09-26T14:00:00-04:00",
            "impact": "High",
        },
        {"title": "ECB", "country": "EUR", "date": "2026-09-25T08:00:00-04:00", "impact": "High"},
        {
            "title": "Crude Inventories",
            "country": "USD",
            "date": "2026-09-25T10:30:00-04:00",
            "impact": "Medium",
        },
        {"title": "NFP", "country": "USD", "date": "2026-10-02T08:30:00-04:00", "impact": "High"},
    ]
    assert leer_calendario(eventos, AHORA) == ["hoy 08:30 CPI y/y", "mañana 14:00 FOMC Statement"]


def test_sesion_y_fin_de_mes():
    assert sesion(AHORA) == "sesión EE. UU. · 6 d a fin de mes"
    assert "fin de semana" in sesion(datetime(2026, 9, 27, 3, 0, tzinfo=UTC))


def test_titulares_de_varias_fuentes_recientes_limpios_y_sin_repetir():
    """Los escribe un tercero: sin saltos de línea ni comillas que parezcan del
    bloque, y la misma noticia de dos portales cuenta una vez."""
    finnhub = leer_finnhub(
        [
            {"headline": "Viejo", "datetime": TS - 20 * 3600},
            {"headline": "BTC «sube»\nfuerte", "datetime": TS - 3600},
        ]
    )
    rss = leer_rss(
        "<rss><channel>"
        "<item><title>ETF récord &amp; más</title>"
        "<pubDate>Fri, 25 Sep 2026 16:20:00 +0000</pubDate></item>"
        "<item><title><![CDATA[BTC sube fuerte]]></title>"
        "<pubDate>Fri, 25 Sep 2026 16:40:00 +0000</pubDate></item>"
        "</channel></rss>",
        "CoinDesk",
    )
    tg = leer_telegram(
        '<div class="tgme_widget_message_text js-message_text" dir="auto">'
        "JUST IN: <b>Fed</b> habla hoy</div>"
        '<a class="tgme_widget_message_date">'
        '<time datetime="2026-09-25T16:45:00+00:00">16:45</time></a>',
        "WatcherGuru",
    )
    assert tg == [
        (
            datetime(2026, 9, 25, 16, 45, tzinfo=UTC).timestamp(),
            "JUST IN: Fed habla hoy",
            "WatcherGuru",
        )
    ]
    assert combinar_titulares([finnhub, rss, tg], TS) == [
        "JUST IN: Fed habla hoy (WatcherGuru)",
        "BTC sube fuerte (CoinDesk)",
        "ETF récord & más (CoinDesk)",
    ]


def test_un_titular_largo_se_corta_en_una_palabra():
    largo = "palabra " * 20
    (t,) = combinar_titulares([[(TS - 60, largo.strip(), "X")]], TS)
    assert t.endswith("palabra… (X)") and len(t) <= 96


def test_rss_roto_no_levanta():
    assert leer_rss("<rss><channel><item>", "X") == []


def test_dominancia_de_btc_y_de_usdt():
    j = {"data": {"market_cap_percentage": {"btc": 58.21, "eth": 11.0, "usdt": 5.13}}}
    assert leer_dominancia(j) == {"btc": 58.21, "usdt": 5.13}


def _datos_llenos() -> dict:
    y = {"precio": 100.0, "hace24h": 99.0}
    d = {
        k: dict(y)
        for k in (
            "ndx",
            "spx",
            "dxy",
            "oro",
            "plata",
            "wti",
            "brent",
            "usdjpy",
            "eurusd",
            "usdcny",
            "mstr",
            "coin",
            "ibit",
            "mara",
            "nvda",
        )
    }
    d["vix"] = {"precio": 22.1, "hace24h": 19.0}
    d["us10a"] = {"precio": 4.21, "hace24h": 4.16}
    d.update(
        {
            "etf": {"dia": "25 Sep 2026", "neto_musd": 312.0},
            "stablecoins": {"total": 312e9, "cambio7d": 0.8},
            "premium_cb": 0.04,
            "funding": {"ultimo": 0.01, "media7d": 0.006},
            "oi4h": 1.2,
            "long_short": 1.35,
            "base_cme": 0.6,
            "dvol": {"dvol": 48, "hace24h": 50},
            "opciones": {"put_call": 0.62, "vto": "27-Sep", "vto_musd": 4100.0},
            "ethbtc": {"precio": 0.0412, "cambio": -1.1},
            "solbtc": {"precio": 0.002, "cambio": -0.7},
            "dominancia": {"btc": 58.2, "usdt": 5.13},
            "hashrate_eh": 890.0,
            "fees": 12,
            "fng": {"valor": 34, "clase": "Fear", "ayer": 38},
            "calendario": ["hoy 08:30 CPI y/y"],
            "sesion": "sesión EE. UU.",
            "titulares": ["Titular número uno de prueba", "Titular dos", "Titular tres"],
        }
    )
    return d


def test_el_bloque_lleno_cabe_en_el_tope():
    """La condición de Elvis: no saturar el mensaje. Con TODAS las fuentes, el
    bloque entra en el tope y lleva una línea por familia de ejes."""
    b = formatear(_datos_llenos())
    assert len(b) <= TOPE_CHARS
    for titulo in (
        "Macro 24h",
        "Refugio/energía",
        "Divisas",
        "Acciones BTC",
        "Flujos",
        "Derivados",
        "Opciones",
        "Cripto",
        "On-chain/sentimiento",
        "Calendario",
    ):
        assert f"\n{titulo}: " in b, titulo
    assert (
        "VIX 22.1 (+3.1)" in b and "10A 4.21% (+5pb)" in b and "ETF spot 25 Sep 2026 +312 M$" in b
    )


def test_con_tope_chico_se_cortan_primero_los_titulares():
    b_lleno = formatear(_datos_llenos())
    sin_titulares = b_lleno.split("\nTitular:")[0]
    b = formatear(_datos_llenos(), tope=len(sin_titulares) + 5)
    assert "Titular" not in b and b.endswith(sin_titulares[-20:])


def test_un_cambio_que_redondea_a_cero_no_lleva_signo_negativo():
    d = {"dvol": {"dvol": 35.0, "hace24h": 35.3}}
    assert "DVOL 35 (±0)" in formatear(d)


def test_una_familia_sin_datos_no_deja_linea_vacia():
    d = {"sesion": "sesión Asia"}
    b = formatear(d)
    assert b.count("\n") == 1 and "Calendario: sesión Asia" in b


def test_reunir_con_la_red_caida_no_levanta_y_anota_los_fallos():
    """Sin red, la mesa sigue: el bloque queda con lo que el código sabe solo."""

    def caida(_url: str):
        raise OSError("sin red")

    c = reunir(AHORA, pedir=caida, firecrawl="", finnhub="", texto=caida)
    assert "sesión EE. UU." in c.bloque
    assert "dxy" in c.fallos and "funding" in c.fallos
    assert "token=" not in "".join(c.fallos.values())
