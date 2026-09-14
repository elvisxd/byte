"""Leer páginas y explorar APIs (Fase 6).

Lo que más se prueba es **lo que no se puede alcanzar**. El agente corre en la
máquina del usuario, y la URL la elige el modelo a partir de lo que leyó: una
página que diga "consultá http://localhost:8000/admin" es exactamente el camino
de un SSRF, porque el agente sí puede llegar ahí.
"""

import pytest

from tools.web_fetch import (
    LlamarApiArgs,
    UrlNoPermitida,
    _llamar_api,
    _texto_de,
    _verificar,
    build_web_fetch_tools,
)


@pytest.mark.parametrize(
    ("url", "por_que"),
    [
        ("http://localhost:8000/admin", "la propia API de Byte"),
        ("http://127.0.0.1/x", "loopback por IP"),
        ("http://169.254.169.254/latest/meta-data/", "los metadatos de la nube"),
        ("http://192.168.1.1/", "el router de la red local"),
        ("http://10.0.0.5/interno", "una red privada"),
    ],
)
def test_no_se_alcanza_la_red_interna(url: str, por_que: str) -> None:
    """Cada una de estas es algo que el agente puede alcanzar y no debería:
    `por_que` queda en el mensaje para que un fallo diga cuál se coló."""
    with pytest.raises(UrlNoPermitida):
        _verificar(url)


def test_solo_http_y_https() -> None:
    """`file://` leería el disco sin pasar por el confinamiento de archivos."""
    for url in ("file:///etc/passwd", "ftp://algo/x", "gopher://viejo"):
        with pytest.raises(UrlNoPermitida):
            _verificar(url)


def test_una_url_publica_pasa() -> None:
    assert _verificar("https://example.com/algo") == "https://example.com/algo"


def test_se_saca_el_ruido_de_la_pagina() -> None:
    """Lo que entra al prompt tiene que ser el contenido: menús, scripts y
    banners gastan contexto y tapan lo que importa."""
    html = """<html><body>
        <nav>Inicio Contacto</nav>
        <script>var x = 1;</script>
        <p>El texto que importa.</p>
        <footer>Copyright</footer>
    </body></html>"""
    texto = _texto_de(html)
    assert "El texto que importa." in texto
    assert "var x" not in texto
    assert "Copyright" not in texto


async def test_la_api_solo_acepta_get_y_post() -> None:
    """PUT, PATCH y DELETE modifican del otro lado, y un modelo probando
    endpoints no debería poder borrar nada de nadie."""
    for metodo in ("DELETE", "PUT", "PATCH"):
        resultado = await _llamar_api(LlamarApiArgs(url="https://example.com", metodo=metodo), 4000)
        assert resultado.ok is False
        assert "GET y POST" in resultado.content


async def test_un_cuerpo_que_no_es_json_se_rechaza_antes_de_pedir() -> None:
    """Mandar basura y ver qué contesta el otro lado gasta una llamada en algo
    que se sabe mal de antemano."""
    resultado = await _llamar_api(
        LlamarApiArgs(url="https://example.com", metodo="POST", cuerpo="{roto"), 4000
    )
    assert resultado.ok is False
    assert "JSON" in resultado.content


def test_las_dos_herramientas_se_arman() -> None:
    assert {h.name for h in build_web_fetch_tools(4000)} == {"leer_web", "llamar_api"}


async def test_se_respeta_el_robots_txt(monkeypatch) -> None:
    """Un sitio que pide no ser recorrido está diciendo algo, y respetarlo es la
    diferencia entre un agente que lee y uno que raspa."""
    import tools.web_fetch as wf

    class RespuestaFalsa:
        status_code = 200
        text = "User-agent: *\nDisallow: /privado\n"

    class ClienteFalso:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, _url):
            return RespuestaFalsa()

    monkeypatch.setattr(wf.httpx, "AsyncClient", lambda **_k: ClienteFalso())
    wf._robots.clear()
    assert await wf._robots_permite("https://ejemplo.com/publico") is True
    assert await wf._robots_permite("https://ejemplo.com/privado") is False


async def test_sin_robots_txt_se_permite(monkeypatch) -> None:
    """La ausencia de reglas no es una prohibición: la mayoría de los sitios no
    tiene robots.txt, y negarse a leerlos sería inútil."""
    import tools.web_fetch as wf

    class ClienteRoto:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, _url):
            raise wf.httpx.ConnectError("no hay")

    monkeypatch.setattr(wf.httpx, "AsyncClient", lambda **_k: ClienteRoto())
    wf._robots.clear()
    assert await wf._robots_permite("https://sin-robots.com/algo") is True


async def test_se_espacian_los_pedidos_al_mismo_dominio() -> None:
    """Cinco páginas del mismo sitio en dos segundos se ven como un scraper
    desde una IP doméstica: primero llegan los 429 y después el bloqueo."""
    import time as reloj

    import tools.web_fetch as wf

    wf._ultimo_pedido.clear()
    arranque = reloj.monotonic()
    await wf._esperar_turno("ejemplo.com")
    await wf._esperar_turno("ejemplo.com")
    assert reloj.monotonic() - arranque >= wf.ESPERA_POR_DOMINIO_S * 0.9


async def test_dominios_distintos_no_se_esperan_entre_si() -> None:
    """La espera es por sitio: hacer esperar a uno por lo que se pidió a otro
    sería lentitud sin motivo."""
    import time as reloj

    import tools.web_fetch as wf

    wf._ultimo_pedido.clear()
    arranque = reloj.monotonic()
    await wf._esperar_turno("uno.com")
    await wf._esperar_turno("otro.com")
    assert reloj.monotonic() - arranque < wf.ESPERA_POR_DOMINIO_S
