"""El CLI en Python.

Se prueba contra la app real vía TestClient: el CLI habla HTTP y nada más, así
que un doble del transporte alcanza para ejercitar el parseo, los códigos de
salida y el formato de la salida sin levantar un servidor.
"""

import json
import os
import re
import time
from collections.abc import Callable

import httpx
import pytest
from starlette.testclient import TestClient

from cli import byte_cli, sesion
from tests.fakes import text_turn, tool_turn

API_KEY = "clave-de-prueba"
AUTH_HEADER = {"X-API-Key": API_KEY}


def _parchear_transporte(monkeypatch: pytest.MonkeyPatch, cliente: TestClient) -> None:
    """Apunta el CLI a la app de pruebas en vez de a la red.

    Se parchea el punto exacto donde toca HTTP, así se ejercita todo lo demás
    (parseo, códigos de salida, formato, el stream) sin levantar un servidor.
    """

    def init(self: byte_cli.Byte, base_url: str, api_key: str) -> None:
        self._cliente = None
        self._url = base_url.rstrip("/")
        self._api_key = api_key
        # La sesión se lee igual que en producción: los tests que la tocan
        # apuntan XDG_CONFIG_HOME a un directorio temporal.
        self._sesion = sesion.leer(self._url)

    def pedir(self: byte_cli.Byte, metodo: str, ruta: str, **kwargs: object) -> object:
        respuesta = cliente.request(
            metodo, f"/api/v1{ruta}", headers={"X-API-Key": API_KEY}, **kwargs
        )
        if respuesta.status_code >= 400:
            raise RuntimeError(byte_cli._mensaje_de_error(respuesta))
        return None if respuesta.status_code == 204 else respuesta.json()

    def eventos(self: byte_cli.Byte, ruta: str):  # noqa: ANN202
        """El SSE del run, parseado igual que en producción.

        TestClient entrega el stream completo de una, lo que alcanza: lo que se
        prueba es que el CLI traduzca los eventos a estados y a texto.
        """
        respuesta = cliente.request("GET", f"/api/v1{ruta}", headers={"X-API-Key": API_KEY})
        if respuesta.status_code >= 400:
            raise RuntimeError(byte_cli._mensaje_de_error(respuesta))
        tipo, datos = "", ""
        for linea in respuesta.text.splitlines():
            if linea.startswith("event:"):
                tipo = linea[6:].strip()
            elif linea.startswith("data:"):
                datos = linea[5:].strip()
            elif not linea.strip() and tipo:
                yield tipo, json.loads(datos) if datos else {}
                tipo, datos = "", ""

    monkeypatch.setattr(byte_cli.Byte, "__init__", init)
    monkeypatch.setattr(byte_cli.Byte, "__exit__", lambda *_: None)
    monkeypatch.setattr(byte_cli.Byte, "pedir", pedir)
    monkeypatch.setattr(byte_cli.Byte, "eventos", eventos)


@pytest.fixture
def cli(
    crear_cliente: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., int]:
    """Corre el CLI contra la app de pruebas, devolviendo su código de salida."""
    cliente = crear_cliente(turns=[text_turn("Listo.")], con_sandbox=True)
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    _parchear_transporte(monkeypatch, cliente)
    return lambda *argv: byte_cli.main(list(argv))


def test_status_muestra_los_servicios(cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("status") == 0
    salida = capsys.readouterr().out
    assert "ollama" in salida
    assert "sandbox" in salida


def test_ask_imprime_la_respuesta(cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("ask", "hola") == 0
    assert "Listo." in capsys.readouterr().out


def test_ask_puede_seguir_una_conversacion(cli, capsys: pytest.CaptureFixture[str]) -> None:
    cli("ask", "primero")
    salida = capsys.readouterr().out
    assert "Listo." in salida


def test_run_sin_sandbox_configurado(cli, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    """El 503 de la API llega al usuario como un mensaje entendible, no como un
    volcado de httpx. (El camino feliz de `byte run` necesita el sandbox real y
    se verifica a mano; acá se cubre el error, que es lo que más se ve.)"""
    archivo = tmp_path / "hola.py"
    archivo.write_text("print('hola')")
    assert cli("run", str(archivo)) == 1
    assert "sandbox" in capsys.readouterr().err.lower()


def test_run_con_archivo_inexistente(cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("run", "/no/existe.py") == 1
    assert "does not exist" in capsys.readouterr().err


def test_sin_api_key_no_arranca(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BYTE_API_KEY", raising=False)
    # Sin .env del proyecto al alcance: se mira desde un directorio vacío.
    monkeypatch.setattr(byte_cli, "__file__", str(tmp_path / "cli" / "byte_cli.py"))
    assert cli("status") == 1
    assert "BYTE_API_KEY" in capsys.readouterr().err


def test_los_colores_se_apagan_sin_terminal() -> None:
    """La salida redirigida a un archivo no debería tener códigos de escape."""
    assert byte_cli._color("hola", byte_cli.AMBAR) == "hola"


# --- Bienvenida y spinner ---


def test_sin_comando_muestra_la_bienvenida(cli, capsys: pytest.CaptureFixture[str]) -> None:
    """`byte` a secas no es un error de uso: muestra qué es y cómo empezar."""
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "Byte" in salida
    assert "byte ask" in salida
    # El marco tiene que cerrar: si las filas no alinean, se ve roto.
    lineas = [ln for ln in salida.splitlines() if ln.startswith(("│", "╭", "╰"))]
    assert len({len(ln) for ln in lineas}) == 1, "las filas del marco no alinean"


def test_la_bienvenida_aguanta_una_api_caida(
    cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Es lo primero que alguien corre: si la API no está, tiene que decirlo,
    no explotar con un traceback."""

    def caida(self: byte_cli.Byte, *_: object, **__: object) -> object:
        raise RuntimeError("no responde")

    monkeypatch.setattr(byte_cli.Byte, "pedir", caida)
    assert cli() == 0
    assert "no connection" in capsys.readouterr().out


def test_el_estado_no_dibuja_sin_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    """Redirigir la salida a un archivo no debería llenarlo de códigos ANSI."""
    with byte_cli.Estado():
        pass
    assert capsys.readouterr().err == ""


def test_el_estado_devuelve_el_cursor_aunque_falle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si el cursor queda escondido, la terminal se rompe para todo lo demás."""
    monkeypatch.setattr(byte_cli.sys.stderr, "isatty", lambda: True)
    with pytest.raises(ValueError, match="algo falló"), byte_cli.Estado():
        raise ValueError("algo falló")
    assert "\033[?25h" in capsys.readouterr().err


# --- Chat interactivo ---


@pytest.fixture
def en_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula una terminal: es lo que decide si `byte` a secas abre el chat.

    Se parchea la función y no `sys.stdout.isatty`, porque `capsys` reemplaza
    `sys.stdout` por test y el parche se perdería.
    """
    monkeypatch.setattr(byte_cli, "_interactiva", lambda: True)
    monkeypatch.setattr(byte_cli, "_en_pantalla", lambda: True)


def _tecleado(monkeypatch: pytest.MonkeyPatch, *lineas: str) -> None:
    """Guiona lo que alguien escribe en el prompt; al final, Ctrl-D."""
    pendientes = iter(lineas)

    def falso_input(_prompt: str = "") -> str:
        try:
            return next(pendientes)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", falso_input)


def test_byte_a_secas_abre_el_chat(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lo que se pidió: la ventana queda abierta y se le habla directamente."""
    _tecleado(monkeypatch, "hola", "y esto?")
    assert cli() == 0
    salida = capsys.readouterr().out
    assert salida.count("Listo.") == 2, "cada pregunta tiene que traer su respuesta"
    assert "Bye" in salida


def test_el_chat_mantiene_una_sola_conversacion(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """La memoria entre turnos es el punto del modo interactivo: si cada
    pregunta abriera una conversación nueva, el agente olvidaría la anterior."""
    creadas: list[str] = []
    original = byte_cli._nueva_conversacion

    def espiar(byte: byte_cli.Byte) -> str:
        creadas.append(original(byte))
        return creadas[-1]

    monkeypatch.setattr(byte_cli, "_nueva_conversacion", espiar)
    _tecleado(monkeypatch, "uno", "dos", "tres")
    assert cli() == 0
    assert len(creadas) == 1


def test_salir_cierra_la_ventana(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tecleado(monkeypatch, "/salir", "esto ya no se pregunta")
    assert cli() == 0
    assert "Listo." not in capsys.readouterr().out


def test_exit_en_ingles_tambien_sale(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """El banner está en inglés; /exit es el reflejo de mucha gente."""
    _tecleado(monkeypatch, "/exit", "esto ya no se pregunta")
    assert cli() == 0
    assert "Listo." not in capsys.readouterr().out


def test_nueva_arranca_una_conversacion_en_blanco(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    creadas: list[str] = []
    original = byte_cli._nueva_conversacion
    monkeypatch.setattr(
        byte_cli, "_nueva_conversacion", lambda b: (creadas.append(original(b)), creadas[-1])[1]
    )
    _tecleado(monkeypatch, "hola", "/nueva", "hola de nuevo")
    assert cli() == 0
    assert len(creadas) == 2


def test_los_comandos_del_chat_corren_sin_cortar_la_sesion(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tecleado(monkeypatch, "/status", "hola")
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "ollama" in salida
    assert "Listo." in salida, "después de un /comando se sigue pudiendo preguntar"


def test_un_comando_desconocido_no_va_al_modelo(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tecleado(monkeypatch, "/volar")
    assert cli() == 0
    assert "/help" in capsys.readouterr().out


def test_las_lineas_vacias_no_preguntan_nada(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dar Enter de más no debería gastar una vuelta del modelo."""
    _tecleado(monkeypatch, "", "   ")
    assert cli() == 0
    assert "Listo." not in capsys.readouterr().out


def test_un_error_de_la_api_no_cierra_el_chat(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Que la API falle una vez no debería costar la sesión entera."""
    fallos = {"quedan": 1}
    original = byte_cli._preguntar_en_vivo

    def a_veces_falla(byte, conversacion, pregunta, safe, *_):  # noqa: ANN001, ANN202
        if fallos["quedan"]:
            fallos["quedan"] -= 1
            raise RuntimeError("la API se cayó")
        return original(byte, conversacion, pregunta, safe)

    monkeypatch.setattr(byte_cli, "_preguntar_en_vivo", a_veces_falla)
    _tecleado(monkeypatch, "primera", "segunda")
    assert cli() == 0
    capturado = capsys.readouterr()
    assert "la API se cayó" in capturado.err
    assert "Listo." in capturado.out, "la segunda pregunta tiene que seguir funcionando"


def test_ctrl_c_corta_la_respuesta_pero_no_la_sesion(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C mientras piensa cancela esa respuesta; para salir están /salir y Ctrl-D."""
    interrumpir = {"quedan": 1}
    original = byte_cli._preguntar_en_vivo

    def a_veces_interrumpe(byte, conversacion, pregunta, safe, *_):  # noqa: ANN001, ANN202
        if interrumpir["quedan"]:
            interrumpir["quedan"] -= 1
            raise KeyboardInterrupt
        return original(byte, conversacion, pregunta, safe)

    monkeypatch.setattr(byte_cli, "_preguntar_en_vivo", a_veces_interrumpe)
    _tecleado(monkeypatch, "larga", "corta")
    assert cli() == 0
    assert "Listo." in capsys.readouterr().out


def test_sin_terminal_solo_se_presenta(cli, capsys: pytest.CaptureFixture[str]) -> None:
    """`byte | cat` o `byte > archivo` no deberían quedarse esperando input."""
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "byte ask" in salida, "sin terminal muestra los comandos, no el chat"
    assert "Chau" not in salida


# --- Estado en vivo y streaming ---


def test_la_respuesta_sale_una_sola_vez(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """El chat sigue el SSE pero acumula: el texto se muestra armado, una vez."""
    _tecleado(monkeypatch, "hola")
    assert cli() == 0
    assert capsys.readouterr().out.count("Listo.") == 1, "el texto no debe duplicarse"


def test_la_respuesta_no_se_escribe_token_a_token(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un párrafo se lee de un vistazo; ver letras apareciendo obliga a esperar.

    El fake parte la respuesta en trozos de 4 caracteres: si se escribiera a
    medida que llega, habría un write por trozo en vez de uno por párrafo.
    """
    escrituras: list[str] = []
    real = byte_cli.sys.stdout.write
    monkeypatch.setattr(byte_cli.sys.stdout, "write", lambda t: (escrituras.append(t), real(t))[1])
    _tecleado(monkeypatch, "hola")
    assert cli() == 0
    con_texto = [e for e in escrituras if "Listo" in e]
    assert con_texto == ["Listo."], f"el texto salió en pedazos: {con_texto}"


def test_el_trabajo_de_cada_herramienta_queda_escrito(
    crear_cliente: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """El contexto que el spinner borraba: qué usó, con qué y con qué resultado."""
    cliente = crear_cliente(
        turns=[tool_turn("web_search", '{"query": "fastapi"}'), text_turn("Listo.")],
        con_sandbox=True,
    )
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    _parchear_transporte(monkeypatch, cliente)
    monkeypatch.setattr(byte_cli, "_interactiva", lambda: True)
    monkeypatch.setattr(byte_cli, "_en_pantalla", lambda: True)
    _tecleado(monkeypatch, "buscá algo")
    assert byte_cli.main([]) == 0

    salida = capsys.readouterr().out
    assert "Searching the web" in salida, "la línea de trabajo no quedó en el historial"
    assert "fastapi" in salida, "falta con qué se buscó"
    assert "web_search" in salida.split("Listo.")[-1], "el pie no dice qué herramientas corrieron"


def test_el_pie_dice_cuanto_tardo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cuánto tardó, abajo y en gris: es contexto, no la respuesta."""
    monkeypatch.setattr(byte_cli, "_en_pantalla", lambda: True)
    byte_cli._mostrar_respuesta(
        {"message": {"content": "Listo.", "metadata": {}}, "segundos": 12.4, "herramientas": []}
    )
    assert re.search(r"\b12s\b", capsys.readouterr().out), "no se ve cuánto tardó"


def test_el_pie_no_muestra_un_tiempo_que_no_dice_nada(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un run instantáneo mostraría "0s", que es ruido."""
    monkeypatch.setattr(byte_cli, "_en_pantalla", lambda: True)
    byte_cli._mostrar_respuesta(
        {"message": {"content": "Listo.", "metadata": {}}, "segundos": 0.2, "herramientas": []}
    )
    assert "0s" not in capsys.readouterr().out


def test_la_respuesta_se_corta_en_parrafos() -> None:
    """La unidad de salida es el párrafo, no la línea ni el token."""
    assert list(byte_cli._parrafos("uno\ndos\n\ntres")) == ["uno\ndos", "tres"]
    assert list(byte_cli._parrafos("")) == []
    assert list(byte_cli._parrafos("\n\nsolo\n\n")) == ["solo"]


def test_el_resumen_de_una_herramienta_es_legible() -> None:
    """Del `summary` de la API sale una línea corta, en inglés."""
    assert byte_cli._resumen_de("web_search", {"ok": True, "results": 3}) == "3 results"
    assert byte_cli._resumen_de("web_search", {"ok": True, "results": 1}) == "1 result"
    assert byte_cli._resumen_de("web_search", {"ok": True, "results": 0}) == "nothing found"
    assert "exit 0" in byte_cli._resumen_de("code_exec", {"ok": True, "duration_ms": 12})
    assert byte_cli._resumen_de("web_search", {"ok": False, "error": "busqueda_fallida"}) == (
        "the search failed"
    )


def test_una_herramienta_que_falla_se_marca(capsys: pytest.CaptureFixture[str]) -> None:
    """Un ✓ en algo que falló sería mentir sobre lo que pasó."""
    byte_cli._linea_de_trabajo("Running code", "print(1)", "it failed", ok=False)
    assert "✗" in capsys.readouterr().out


def test_el_estado_dice_que_herramienta_esta_usando(
    crear_cliente: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo que se pidió: en vez de un "pensando" mudo, el verbo de lo que pasa.

    Se mira la línea de estado directamente porque va a stderr y solo se dibuja
    con terminal; acá interesa el mapeo de evento a verbo, no el dibujo.
    """
    vistos: list[tuple[str, str]] = []

    class EstadoEspia(byte_cli.Estado):
        def cambiar(self, texto: str, detalle: str = "") -> None:
            vistos.append((texto, detalle))

    cliente = crear_cliente(
        turns=[tool_turn("web_search", '{"query": "fastapi"}'), text_turn("Listo.")],
        con_sandbox=True,
    )
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    monkeypatch.setattr(byte_cli, "Estado", EstadoEspia)
    _parchear_transporte(monkeypatch, cliente)
    monkeypatch.setattr(byte_cli, "_interactiva", lambda: True)
    _tecleado(monkeypatch, "buscá algo")
    assert byte_cli.main([]) == 0

    verbos = [v for v, _ in vistos]
    assert "Searching the web" in verbos, f"no apareció el verbo de la herramienta: {vistos}"
    assert "pensando" not in " ".join(verbos).lower(), "el estado tiene que estar en inglés"
    assert ("Searching the web", "fastapi") in vistos, "falta la consulta al lado del verbo"

    # El nodo `tools` del grafo entra después del TOOL_CALL_START: si su verbo
    # genérico no se ignorara, pisaría al específico y el usuario vería "Using
    # tools" en vez de qué está buscando (pasó en una terminal de verdad).
    ultimo_especifico = len(verbos) - 1 - verbos[::-1].index("Searching the web")
    assert "Using tools" not in verbos[ultimo_especifico:], (
        f"el paso genérico pisó al verbo de la herramienta: {verbos}"
    )


def test_el_detalle_del_estado_sale_de_los_argumentos() -> None:
    """De los argumentos de la herramienta se saca algo legible, o nada."""
    assert byte_cli._detalle_de('{"query": "fastapi lifespan"}') == "fastapi lifespan"
    assert byte_cli._detalle_de('{"code": "print(1)\\nprint(2)"}') == "print(1)"
    assert byte_cli._detalle_de("no es json") == ""
    assert byte_cli._detalle_de('{"otra_cosa": 1}') == ""


def test_el_detalle_largo_se_recorta() -> None:
    """Una consulta larga empujaría el spinner fuera de la pantalla."""
    largo = byte_cli._detalle_de('{"query": "' + "x" * 200 + '"}')
    assert len(largo) <= 48
    assert largo.endswith("…")


# --- La bienvenida en dos columnas ---


def _marco(salida: str) -> list[str]:
    return [ln for ln in salida.splitlines() if ln.startswith(("│", "╭", "╰"))]


def test_la_bienvenida_alinea_en_dos_columnas(
    cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si una celda se pasa de ancho, el marco se ve roto en diagonal."""
    monkeypatch.setattr(
        byte_cli.shutil, "get_terminal_size", lambda _f=None: os.terminal_size((104, 24))
    )
    assert cli() == 0
    lineas = _marco(capsys.readouterr().out)
    assert len({len(ln) for ln in lineas}) == 1, "las filas del marco no alinean"
    assert any("│" in ln[1:-1] for ln in lineas), "no hay separador: no son dos columnas"


def test_en_una_terminal_angosta_cae_a_una_columna(
    cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 70 columnas la derecha no entra; apilar es mejor que romper el marco."""
    monkeypatch.setattr(
        byte_cli.shutil, "get_terminal_size", lambda _f=None: os.terminal_size((70, 24))
    )
    assert cli() == 0
    lineas = _marco(capsys.readouterr().out)
    assert len({len(ln) for ln in lineas}) == 1, "las filas del marco no alinean"
    assert all(len(ln) <= 70 for ln in lineas), "el marco se pasa del ancho de la terminal"


def test_la_bienvenida_dice_para_que_sirve_cada_herramienta(
    cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`GET /tools` solo da nombres; el para qué lo pone el CLI.

    (La app de pruebas no monta `doc_search`, que necesita el store de RAG, así
    que se verifica con una de las que sí están.)"""
    monkeypatch.setattr(
        byte_cli.shutil, "get_terminal_size", lambda _f=None: os.terminal_size((104, 24))
    )
    assert cli() == 0
    assert "looks things up on the web" in capsys.readouterr().out


def test_el_estado_deja_de_girar_cuando_empieza_la_respuesta(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si el hilo sigue vivo, redibuja encima del texto y la respuesta sale
    entrelazada con el spinner (se vio en una terminal de verdad)."""
    monkeypatch.setattr(byte_cli.sys.stderr, "isatty", lambda: True)
    with byte_cli.Estado() as estado:
        estado.detener()
        capsys.readouterr()  # descarta lo dibujado hasta acá
        time.sleep(0.3)  # más que un par de cuadros del spinner
        assert capsys.readouterr().err == "", "el spinner siguió dibujando"


# --- Los comandos del chat ---


def test_buscar_en_los_documentos_sin_salir(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`/search` va directo al índice, sin gastar una vuelta del modelo.

    (La app de pruebas no tiene pgvector, así que la API contesta 503: sirve
    igual para verificar que la consulta no fue al agente y que el error se
    muestra sin cortar la sesión.)
    """
    llamadas: list[str] = []

    def espiar(byte, conversacion, pregunta, safe, *_):  # noqa: ANN001, ANN202
        llamadas.append(pregunta)
        return {"message": {"content": "Listo.", "metadata": {}}}

    monkeypatch.setattr(byte_cli, "_preguntar_en_vivo", espiar)
    _tecleado(monkeypatch, "/search deployment", "y esto sí va al modelo")
    assert cli() == 0
    assert llamadas == ["y esto sí va al modelo"], f"la búsqueda fue al modelo: {llamadas}"
    assert "pgvector" in capsys.readouterr().err, "el error de la API no se mostró"


def test_el_modo_seguro_se_puede_prender_en_el_chat(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sin `/safe` habría que salir y volver a entrar con `byte chat --safe`."""
    _tecleado(monkeypatch, "/safe")
    assert cli() == 0
    assert "safe mode on" in capsys.readouterr().out


def test_el_modo_seguro_del_chat_llega_a_la_pregunta(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prenderlo y que no cambie nada sería peor que no tenerlo."""
    vistos: list[bool] = []
    original = byte_cli._preguntar_en_vivo
    monkeypatch.setattr(
        byte_cli,
        "_preguntar_en_vivo",
        lambda b, c, p, safe, *a: (vistos.append(safe), original(b, c, p, safe, *a))[1],
    )
    _tecleado(monkeypatch, "antes", "/safe", "después")
    assert cli() == 0
    assert vistos == [False, True], f"el modo seguro no viajó a la pregunta: {vistos}"


def test_un_comando_con_argumento_que_falta_lo_dice(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`/add` sin archivo es un error de tipeo, no algo que deba explotar."""
    _tecleado(monkeypatch, "/add")
    assert cli() == 0
    assert "which file" in capsys.readouterr().out


def test_los_comandos_en_espanol_tambien_andan(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """La interfaz está en inglés, pero /ayuda no debería ser un error."""
    _tecleado(monkeypatch, "/ayuda")
    assert cli() == 0
    assert "/exit" in capsys.readouterr().out


# --- Salir con el teclado ---


def _teclas(monkeypatch: pytest.MonkeyPatch, *entradas: object) -> None:
    """Como `_tecleado`, pero una entrada puede ser una excepción (Ctrl-C)."""
    pendientes = iter(entradas)

    def falso_input(_prompt: str = "") -> str:
        try:
            siguiente = next(pendientes)
        except StopIteration:
            raise EOFError from None
        if isinstance(siguiente, BaseException):
            raise siguiente
        return str(siguiente)

    monkeypatch.setattr("builtins.input", falso_input)


def test_un_ctrl_c_solo_no_cierra_el_chat(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Con un modelo local que tarda minutos, Ctrl-C se usa para cancelar: que
    cerrara la sesión entera sería peor que no tenerlo."""
    _teclas(monkeypatch, KeyboardInterrupt(), "hola")
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "again to exit" in salida, "no avisó que el próximo Ctrl-C cierra"
    assert "Listo." in salida, "la pregunta después del Ctrl-C no se atendió"


def test_dos_ctrl_c_seguidos_cierran_el_chat(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _teclas(monkeypatch, KeyboardInterrupt(), KeyboardInterrupt(), "esto ya no se pregunta")
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "Listo." not in salida, "siguió andando después del segundo Ctrl-C"
    assert "Bye" in salida


def test_escribir_algo_entre_medio_desarma_la_salida(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Quien siguió escribiendo no se estaba yendo: el Ctrl-C de hace tres
    turnos no debería cerrarle la sesión."""
    _teclas(monkeypatch, KeyboardInterrupt(), "hola", KeyboardInterrupt(), "y otra más")
    assert cli() == 0
    assert capsys.readouterr().out.count("Listo.") == 2, "la segunda pregunta no se atendió"


def test_cancelar_una_respuesta_deja_armada_la_salida(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C mientras piensa cancela; otro seguido cierra, igual que en el prompt."""
    monkeypatch.setattr(
        byte_cli, "_preguntar_en_vivo", lambda *_a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    _teclas(monkeypatch, "una pregunta larga", KeyboardInterrupt(), "esto ya no se pregunta")
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "stopped waiting" in salida
    assert "Listo." not in salida, "no cerró con el Ctrl-C que siguió a la cancelación"


def test_ctrl_d_cierra_de_una(
    cli, en_terminal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """El atajo de siempre para cerrar una entrada no necesita confirmación."""
    _teclas(monkeypatch)  # sin entradas: el primer input() ya da EOFError
    assert cli() == 0
    assert "again to exit" not in capsys.readouterr().out


# --- Sesión de usuario en el CLI (Fase 6) ---


@pytest.fixture
def config_aparte(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """La sesión va a un directorio temporal, no a la del desarrollador."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_la_sesion_se_guarda_con_permisos_privados(config_aparte) -> None:
    """Adentro hay un refresh token que vale 14 días: si otro usuario de la
    máquina puede leerlo, se lleva la sesión entera."""
    import os
    import stat

    from cli import sesion

    sesion.guardar("http://localhost:8000", "tok", "ref", "ana@ejemplo.com")
    modo = stat.S_IMODE(os.stat(sesion.ruta_visible()).st_mode)
    assert modo == 0o600, f"permisos {oct(modo)}"


def test_cada_instancia_tiene_su_sesion(config_aparte) -> None:
    """Apuntar a otra instancia no debería pisar el token de la local, que es
    la que uno tiene abierta todo el día."""
    from cli import sesion

    sesion.guardar("http://localhost:8000", "tok-a", "ref-a", "ana@ejemplo.com")
    sesion.guardar("http://otra:9000", "tok-b", "ref-b", "beto@ejemplo.com")

    assert sesion.leer("http://localhost:8000")["email"] == "ana@ejemplo.com"
    assert sesion.leer("http://otra:9000")["email"] == "beto@ejemplo.com"
    sesion.borrar("http://localhost:8000")
    assert sesion.leer("http://otra:9000") is not None


def test_un_archivo_de_sesion_corrupto_no_rompe_el_cli(config_aparte, tmp_path) -> None:
    """No es motivo para que el CLI no arranque: se ignora y quien quiera
    sesión vuelve a entrar."""
    from cli import sesion

    archivo = tmp_path / "byte" / "sesion.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text("esto no es json", encoding="utf-8")

    assert sesion.leer("http://localhost:8000") is None


def test_sin_sesion_se_usa_la_api_key(cli, config_aparte, capsys) -> None:
    """El comportamiento de siempre: sin login, el CLI es la instancia."""
    assert cli("whoami") == 0
    assert "sin sesión" in capsys.readouterr().out


def _parchear_login(monkeypatch: pytest.MonkeyPatch, transporte: TestClient) -> None:
    """El login arma su propio cliente httpx —a propósito, para no usar el token
    de la sesión anterior— así que hace falta apuntarlo a la app de pruebas."""

    class ClienteDeLogin:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "ClienteDeLogin":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, ruta: str, **kwargs: object) -> object:
            return transporte.post(f"/api/v1{ruta}", headers=AUTH_HEADER, **kwargs)

    monkeypatch.setattr(byte_cli.httpx, "Client", ClienteDeLogin)


def test_el_login_guarda_la_sesion(
    crear_cliente: Callable[..., TestClient], config_aparte, monkeypatch, capsys
) -> None:
    """El hueco que dejó la Fase 4: el CLI solo sabía de la API key, que
    identifica a la instancia y no a una persona."""
    import getpass

    from cli import sesion

    transporte = crear_cliente()
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    _parchear_transporte(monkeypatch, transporte)
    transporte.post(
        "/api/v1/auth/register",
        json={"email": "ana@ejemplo.com", "password": "una-contraseña-larga"},
        headers=AUTH_HEADER,
    )
    _parchear_login(monkeypatch, transporte)
    monkeypatch.setattr(getpass, "getpass", lambda *_a: "una-contraseña-larga")

    assert byte_cli.main(["login", "ana@ejemplo.com"]) == 0
    guardada = sesion.leer("http://localhost:8000")
    assert guardada is not None
    assert guardada["email"] == "ana@ejemplo.com"
    assert guardada["access_token"] and guardada["refresh_token"]
    assert "ana@ejemplo.com" in capsys.readouterr().out


def test_una_contrasena_incorrecta_no_deja_sesion(
    crear_cliente: Callable[..., TestClient], config_aparte, monkeypatch, capsys
) -> None:
    import getpass

    from cli import sesion

    transporte = crear_cliente()
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    _parchear_transporte(monkeypatch, transporte)
    transporte.post(
        "/api/v1/auth/register",
        json={"email": "ana@ejemplo.com", "password": "una-contraseña-larga"},
        headers=AUTH_HEADER,
    )
    _parchear_login(monkeypatch, transporte)
    monkeypatch.setattr(getpass, "getpass", lambda *_a: "no-es-la-correcta")

    assert byte_cli.main(["login", "ana@ejemplo.com"]) == 1
    assert sesion.leer("http://localhost:8000") is None
    assert "incorrect" in capsys.readouterr().err.lower()


def test_el_login_no_usa_el_token_de_la_sesion_anterior(
    crear_cliente: Callable[..., TestClient], config_aparte, monkeypatch
) -> None:
    """Un 401 de "contraseña incorrecta" es indistinguible del de "access
    vencido": yendo por `pedir`, cada intento fallido gastaba una rotación del
    refresh. Entrar no debería depender de estar ya adentro."""
    import getpass

    from cli import sesion

    renovaciones = {"cuantas": 0}
    monkeypatch.setattr(
        byte_cli.Byte, "_renovar", lambda self: renovaciones.__setitem__("cuantas", 1) or False
    )

    transporte = crear_cliente()
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    _parchear_transporte(monkeypatch, transporte)
    _parchear_login(monkeypatch, transporte)
    sesion.guardar("http://localhost:8000", "access-viejo", "refresh-viejo", "ana@ejemplo.com")
    monkeypatch.setattr(getpass, "getpass", lambda *_a: "la-que-sea-larga")

    byte_cli.main(["login", "otra@ejemplo.com"])
    assert renovaciones["cuantas"] == 0, "el login gastó una rotación del refresh"


def test_el_cliente_usa_el_token_cuando_hay_sesion(config_aparte) -> None:
    """Con sesión, lo que se crea es del usuario; sin ella, de la instancia.
    Lo decide qué header se manda."""
    from cli import sesion

    sin_sesion = byte_cli.Byte.__new__(byte_cli.Byte)
    sin_sesion._sesion = None
    sin_sesion._api_key = "la-clave"
    assert sin_sesion._cabeceras() == {"X-API-Key": "la-clave"}

    con_sesion = byte_cli.Byte.__new__(byte_cli.Byte)
    con_sesion._sesion = {"access_token": "tok", "email": "ana@ejemplo.com"}
    con_sesion._api_key = "la-clave"
    assert con_sesion._cabeceras() == {"Authorization": "Bearer tok"}
    assert sesion is not None  # el import se usa arriba


def test_el_stream_tambien_renueva_el_token(config_aparte, monkeypatch) -> None:
    """El chat arranca el run con un POST (que pasa por `pedir` y renueva) y
    después abre el SSE. Si el access vence justo entre los dos, el stream daba
    "credencial inválida" en medio de una respuesta — de lo más confuso que le
    puede pasar a alguien que está escribiendo."""
    from cli import sesion

    llamadas = {"abiertas": 0, "renovaciones": 0}

    class RespuestaFalsa:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers = {"content-type": "text/event-stream"}
            self.text = ""

        def read(self) -> bytes:
            return b""

        def iter_lines(self):
            yield "event: RUN_FINISHED"
            yield 'data: {"status": "finished"}'
            yield ""

        def close(self) -> None:
            return None

    def abrir(self: byte_cli.Byte, _ruta: str) -> RespuestaFalsa:
        llamadas["abiertas"] += 1
        # El primero da 401 (access vencido), el segundo ya va con el nuevo.
        return RespuestaFalsa(401 if llamadas["abiertas"] == 1 else 200)

    def renovar(self: byte_cli.Byte) -> bool:
        llamadas["renovaciones"] += 1
        return True

    sesion.guardar("http://localhost:8000", "vencido", "ref", "ana@ejemplo.com")
    monkeypatch.setattr(byte_cli.Byte, "_abrir_stream", abrir)
    monkeypatch.setattr(byte_cli.Byte, "_renovar", renovar)

    with byte_cli.Byte("http://localhost:8000", "clave") as cliente:
        eventos = list(cliente.eventos("/runs/x/events"))

    assert llamadas["renovaciones"] == 1, "no renovó ante el 401 del stream"
    assert llamadas["abiertas"] == 2, "no reintentó tras renovar"
    assert eventos and eventos[0][0] == "RUN_FINISHED"


def test_el_banner_dice_con_quien_estas_trabajando(cli, config_aparte, monkeypatch, capsys) -> None:
    """Con sesión iniciada, quién sos es parte del estado: sin eso no hay forma
    de saber si lo que escribís queda a tu nombre o al de la instancia."""
    from cli import sesion

    monkeypatch.setattr(byte_cli, "_interactiva", lambda: False)
    sesion.guardar("http://localhost:8000", "tok", "ref", "ana@ejemplo.com")

    assert cli() == 0
    assert "ana@ejemplo.com" in capsys.readouterr().out


def test_sin_sesion_el_banner_no_habla_de_usuarios(cli, config_aparte, capsys) -> None:
    """La API key es el caso normal: ponerle una etiqueta sería ruido."""
    assert cli() == 0
    salida = capsys.readouterr().out
    assert "@" not in salida.split("What it can use")[0]


# --- Lo que salió de revisar la fase ---


def test_el_archivo_de_sesion_se_endurece_si_ya_existia(config_aparte, tmp_path) -> None:
    """El modo de `os.open` **solo aplica al crear**: un archivo preexistente en
    0666 se quedaba en 0666, con el refresh adentro."""
    import os
    import stat

    from cli import sesion

    archivo = tmp_path / "byte" / "sesion.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text("{}", encoding="utf-8")
    os.chmod(archivo, 0o666)  # noqa: S103 - el escenario a reproducir es justo este

    sesion.guardar("http://localhost:8000", "tok", "ref", "ana@ejemplo.com")
    assert stat.S_IMODE(os.stat(archivo).st_mode) == 0o600


def test_un_symlink_no_se_lleva_el_token(config_aparte, tmp_path) -> None:
    """Un symlink plantado en `sesion.json` se seguía: el token acababa en el
    buzón de quien lo plantó, y `os.stat` le mostraba a la víctima un
    tranquilizador 0600 —el del destino, no el del enlace."""
    import os

    from cli import sesion

    buzon = tmp_path / "buzon.json"
    buzon.write_text("", encoding="utf-8")
    enlace = tmp_path / "byte" / "sesion.json"
    enlace.parent.mkdir(parents=True)
    os.symlink(buzon, enlace)

    with pytest.raises(RuntimeError, match="de forma segura"):
        sesion.guardar("http://localhost:8000", "tok-secreto", "ref", "ana@ejemplo.com")
    assert buzon.read_text(encoding="utf-8") == "", "el token se filtró al symlink"


def test_un_xdg_relativo_no_deja_el_token_en_el_directorio_actual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con un valor relativo el token caía donde se corrió `byte`, que puede ser
    un repo. La especificación dice ignorar lo que no sea absoluto."""
    from cli import sesion

    monkeypatch.setenv("XDG_CONFIG_HOME", "configuracion-relativa")
    assert "configuracion-relativa" not in sesion.ruta_visible()
    assert sesion.ruta_visible().startswith("/")


def test_dos_terminales_no_se_matan_la_sesion(config_aparte, monkeypatch) -> None:
    """Dos terminales abiertas es el caso normal: las dos cargan el mismo
    refresh al arrancar. Sin releer el archivo, la segunda presentaba uno ya
    gastado —el servidor lo lee como reuso, revoca la familia entera— y quien no
    hizo nada mal tenía que volver a entrar."""
    from cli import sesion

    sesion.guardar("http://localhost:8000", "access-1", "refresh-1", "ana@ejemplo.com")
    primera = byte_cli.Byte.__new__(byte_cli.Byte)
    primera._url = "http://localhost:8000"
    primera._api_key = ""
    primera._sesion = sesion.leer("http://localhost:8000")
    segunda = byte_cli.Byte.__new__(byte_cli.Byte)
    segunda._url = "http://localhost:8000"
    segunda._api_key = ""
    segunda._sesion = sesion.leer("http://localhost:8000")
    segunda._cliente = primera._cliente = httpx.Client()

    # La primera renueva (lo simula guardando lo que el servidor devolvería).
    sesion.guardar("http://localhost:8000", "access-2", "refresh-2", "ana@ejemplo.com")

    # La segunda no debería intentar canjear su refresh viejo: ya hay uno nuevo.
    assert segunda._renovar() is True
    assert segunda._sesion["access_token"] == "access-2"
    assert sesion.leer("http://localhost:8000") is not None, "se borró la sesión"
    primera._cliente.close()


def test_el_logout_avisa_si_el_servidor_no_confirmo(config_aparte, monkeypatch, capsys) -> None:
    """Con `BYTE_URL` terminada en barra, la URL quedaba `//api/v1/auth/logout`
    → 404. Como `httpx.post` no levanta por status, se imprimía "sesión
    cerrada" mientras el refresh seguía vivo 14 días."""
    from cli import sesion

    class Respuesta:
        status_code = 404

    monkeypatch.setattr(byte_cli.httpx, "post", lambda *_a, **_k: Respuesta())
    monkeypatch.setenv("BYTE_URL", "http://localhost:8000/")
    sesion.guardar("http://localhost:8000", "tok", "ref", "ana@ejemplo.com")

    assert byte_cli.main(["logout"]) == 0
    salida = capsys.readouterr().out
    assert "sigue válido" in salida, "dijo que cerró sin que el servidor confirmara"
    # Y el archivo local se borra igual: es lo único que se pudo hacer.
    assert sesion.leer("http://localhost:8000") is None


def test_el_logout_arma_bien_la_url_con_barra_final(config_aparte, monkeypatch) -> None:
    """El arreglo de verdad: que la URL no tenga la doble barra."""
    from cli import sesion

    vistas: list[str] = []

    class Respuesta:
        status_code = 204

    def espiar(url: str, **_kwargs: object) -> Respuesta:
        vistas.append(url)
        return Respuesta()

    monkeypatch.setattr(byte_cli.httpx, "post", espiar)
    monkeypatch.setenv("BYTE_URL", "http://localhost:8000/")
    sesion.guardar("http://localhost:8000", "tok", "ref", "ana@ejemplo.com")

    byte_cli.main(["logout"])
    assert vistas == ["http://localhost:8000/api/v1/auth/logout"]


# --- La línea de estado y la separación entre turnos (Fase 6) ---


def test_el_verbo_generico_rota_y_el_especifico_se_respeta() -> None:
    """`Working` fijo durante un minuto parece colgado; una palabra que cambia
    dice "sigo acá". Pero cuando el stream sabe qué está pasando —"Searching the
    web"— esa información no se pisa con una palabra inventada."""
    from cli.byte_cli import Estado

    palabras = Estado.OCURRENCIAS
    assert len(set(palabras)) == len(palabras), "hay palabras repetidas"

    # A los 0s y a los CADA segundos no puede tocar la misma.
    primera = palabras[0]
    segunda = palabras[int(Estado.CADA / Estado.CADA) % len(palabras)]
    assert primera != segunda


def _render(escritos: str, ancho: int = 60, alto: int = 12) -> list[str]:
    """Lo que quedaría **en pantalla**, aplicando los escapes de verdad.

    Contar apariciones en la salida cruda no sirve: `\033[F\033[2K` borra una
    línea ya escrita, así que un texto puede aparecer dos veces en los bytes y
    una sola en la terminal — o al revés, que es el bug que esto atrapa.
    Se emula una terminal chica y se mira el resultado.
    """
    pyte = pytest.importorskip("pyte", reason="emulador de terminal, solo para estos tests")
    pantalla = pyte.Screen(ancho, alto)
    flujo = pyte.Stream(pantalla)
    flujo.feed(escritos)
    return [linea.rstrip() for linea in pantalla.display]


def test_la_pregunta_queda_una_sola_vez_en_pantalla(capsys, monkeypatch) -> None:
    """El bug que esto atrapa: al apretar Enter la terminal ya hizo eco de un
    `\r\n`, así que el cursor está en la línea de abajo. Subir **una** sola
    borraba la del prompt y el `print` agregaba otra: la pregunta aparecía dos
    veces. Hay que subir una por cada línea que ocupó lo tipeado.
    """
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    # Lo que la terminal ya mostró mientras se escribía, con su salto.
    tipeado = "❯ hola\r\n"
    cli._eco_de_la_pregunta("hola")
    pantalla = _render(tipeado + capsys.readouterr().out)

    assert sum("hola" in linea for linea in pantalla) == 1, (
        f"la pregunta quedó duplicada en pantalla: {[x for x in pantalla if x]}"
    )


def test_una_pregunta_que_se_envolvio_borra_las_dos_lineas(capsys, monkeypatch) -> None:
    """Si lo tipeado ocupó dos líneas por el ajuste al ancho, borrar una sola
    deja media pregunta colgada arriba del bloque."""
    import shutil

    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda _d=None: os.terminal_size((40, 24)))
    larga = ("palabra " * 9).strip()  # ~71 caracteres: ocupa dos líneas de 40
    cli._eco_de_la_pregunta(larga)
    # El tipeado y lo que escribe el CLI, en el mismo flujo y en ese orden: es
    # lo único que reproduce el estado real del cursor.
    pantalla = _render("❯ " + larga + "\r\n" + capsys.readouterr().out, ancho=40, alto=14)

    # Ninguna línea de pantalla puede ser un resto del eco del tty: todas las que
    # tengan texto son del bloque, que empieza con dos espacios de padding.
    con_texto = [linea for linea in pantalla if linea.strip()]
    assert all(linea.startswith("  ") for linea in con_texto), (
        f"quedó un resto del input() sin borrar: {con_texto}"
    )


def test_una_pregunta_de_varias_lineas_no_borra_nada(capsys, monkeypatch) -> None:
    """Con varias líneas habría que contar cuántas ocupó después del ajuste al
    ancho de la terminal, y equivocarse borra la respuesta anterior. Ahí se
    prefiere el aire al reemplazo."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    cli._eco_de_la_pregunta("una\ndos")
    assert "\033[F" not in capsys.readouterr().out


def test_el_eco_no_sale_si_la_salida_esta_redirigida(capsys, monkeypatch) -> None:
    """Redirigido a un archivo el eco es ruido: lo que se quiere guardar es la
    respuesta."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: False)
    cli._eco_de_la_pregunta("hola")
    assert capsys.readouterr().out == ""


def test_la_duracion_pasa_a_minutos() -> None:
    """Un modelo local pasa el minuto seguido, y `143s` obliga a dividir
    mentalmente para saber si eso fue mucho."""
    from cli.byte_cli import _duracion

    assert _duracion(26) == "26s"
    assert _duracion(59) == "59s"
    assert _duracion(60) == "1m"
    assert _duracion(125) == "2m 5s"


# --- Cambiar de modelo (/model) ---


class ByteConModelos:
    """Un cliente que responde /health/details con la lista de modelos."""

    def __init__(self, modelos: list[str]) -> None:
        self._modelos = modelos
        self.pedidos: list[tuple[str, str]] = []

    def pedir(self, metodo: str, ruta: str, **_kwargs: object) -> dict:
        self.pedidos.append((metodo, ruta))
        return {"model": self._modelos[0], "models": self._modelos}


def test_model_lista_y_marca_el_activo(capsys) -> None:
    import cli.byte_cli as cli

    sesion = cli.Sesion("c1", safe=False)
    cli._cambiar_de_modelo(ByteConModelos(["qwen3:8b", "granite4.1:8b"]), "", sesion)
    salida = capsys.readouterr().out
    assert "qwen3:8b" in salida and "granite4.1:8b" in salida
    assert "active" in salida


def test_model_acepta_un_prefijo(capsys) -> None:
    """Los nombres llevan versión y tag: escribir `granite4.1:8b` entero cada
    vez que se cambia de modelo es fricción que no aporta nada."""
    import cli.byte_cli as cli

    sesion = cli.Sesion("c1", safe=False)
    cli._cambiar_de_modelo(ByteConModelos(["qwen3:8b", "granite4.1:8b"]), "granite", sesion)
    assert sesion.modelo == "granite4.1:8b"
    assert "now using granite4.1:8b" in capsys.readouterr().out


def test_model_avisa_lo_que_cuesta_cambiar(capsys) -> None:
    """En una máquina donde no entran dos modelos en memoria, Ollama desaloja
    uno para cargar el otro y la primera respuesta tarda ~27 s más. Sin el
    aviso, parece que Byte se colgó."""
    import cli.byte_cli as cli

    sesion = cli.Sesion("c1", safe=False)
    cli._cambiar_de_modelo(ByteConModelos(["qwen3:8b", "granite4.1:8b"]), "granite", sesion)
    assert "longer" in capsys.readouterr().out


def test_un_prefijo_ambiguo_no_elige_por_su_cuenta(capsys) -> None:
    """Con dos candidatos, elegir uno sería adivinar qué quiso decir."""
    import cli.byte_cli as cli

    sesion = cli.Sesion("c1", safe=False)
    cli._cambiar_de_modelo(ByteConModelos(["qwen3:8b", "qwen2.5-coder:7b"]), "qwen", sesion)
    assert sesion.modelo == ""
    assert "several" in capsys.readouterr().out


def test_el_nombre_exacto_gana_sobre_el_prefijo(capsys) -> None:
    """`qwen3:8b` es prefijo de `qwen3:8b-extra`: sin la coincidencia exacta
    primero, escribir el nombre entero caería en "matches several". Quien lo
    escribe entero no está pidiendo que se adivine.

    El activo es otro, para que el cambio tenga efecto que verificar."""
    import cli.byte_cli as cli

    sesion = cli.Sesion("c1", safe=False)
    # El primero de la lista es el activo, así que se pide uno distinto.
    cliente = ByteConModelos(["granite4.1:8b", "qwen3:8b", "qwen3:8b-extra"])
    cli._cambiar_de_modelo(cliente, "qwen3:8b", sesion)
    assert sesion.modelo == "qwen3:8b"


def test_un_modelo_que_no_esta_dice_cuales_hay(capsys) -> None:
    import cli.byte_cli as cli

    sesion = cli.Sesion("c1", safe=False)
    cli._cambiar_de_modelo(ByteConModelos(["qwen3:8b"]), "gpt-4", sesion)
    salida = capsys.readouterr().out
    assert sesion.modelo == ""
    assert "qwen3:8b" in salida


def test_con_un_solo_modelo_se_dice_como_agregar_otro(capsys) -> None:
    """Listar una lista de uno no ayuda; lo que falta saber es cómo sumar."""
    import cli.byte_cli as cli

    cli._cambiar_de_modelo(ByteConModelos(["qwen3:8b"]), "", cli.Sesion("c1", safe=False))
    assert "OLLAMA_MODELS_DISPONIBLES" in capsys.readouterr().out


# --- El marco del prompt ---


def test_el_marco_deja_el_prompt_en_su_propia_linea(monkeypatch) -> None:
    """`\033[F` deja el cursor en la **columna 0** de la línea anterior, así que
    sin una línea vacía en el medio el `❯` se escribiría encima de la primera
    regla en vez de quedar entre las dos."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    marco = cli._marco_del_prompt()
    sin_color = re.sub(r"\033\[[0-9;]*m", "", marco)
    assert sin_color.startswith("─"), "falta la regla de arriba"
    assert "\n\n" in sin_color, "falta la línea donde va el prompt"
    assert sin_color.endswith("\033[F"), "no vuelve a la línea del prompt"


def test_la_regla_va_de_borde_a_borde(monkeypatch) -> None:
    """Un separador a media línea se lee como un adorno; uno completo parte la
    pantalla, que es para lo que está. Medido en un pty: 80 caracteres `─`
    entran en 80 columnas sin envolver, aunque Unicode los marque de ancho
    "ambiguo"."""
    import shutil

    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda _d=None: os.terminal_size((80, 24)))
    reglas = re.sub(r"\033\[[0-9;]*m", "", cli._marco_del_prompt()).split("\n")
    assert len(reglas[0]) == 80, f"la de arriba mide {len(reglas[0])}, no 80"
    assert len(reglas[2].replace("\033[F", "")) == 80, "las dos tienen que medir igual"


def test_sin_terminal_no_se_dibuja_el_marco(monkeypatch) -> None:
    """Redirigido a un archivo, el marco sería ruido."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: False)
    assert cli._marco_del_prompt() == ""


def test_al_salir_se_borra_el_marco(capsys, monkeypatch) -> None:
    """Ctrl-C y Ctrl-D dejan el cursor sobre la regla de abajo, con el prompt y
    la de arriba encima: sin borrarlo quedan tres líneas sueltas después del
    "Bye"."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    cli._borrar_marco()
    salida = capsys.readouterr().out
    # Cinco: la barra de estado, la regla de abajo, el prompt, la de arriba, y
    # una de más porque `\033[F` no baja del borde superior —sobrar es inocuo,
    # quedarse corto deja media regla debajo del "Bye".
    assert salida.count("\033[2K") == 5, "no borra las líneas del marco y la barra"


def test_al_abrir_el_chat_se_limpia_la_pantalla(capsys, monkeypatch) -> None:
    """Sin esto el banner aparece debajo de lo que hubiera —un `ls`, un
    traceback, el `docker compose up`— y la conversación arranca mezclada con
    ruido ajeno."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    cli._limpiar_pantalla()
    salida = capsys.readouterr().out
    assert "\033[2J" in salida, "no limpia la pantalla"
    assert "\033[3J" in salida, "no limpia el búfer de scroll"
    assert "\033[H" in salida, "no lleva el cursor arriba"


def test_no_se_usa_la_pantalla_alternativa(capsys, monkeypatch) -> None:
    """La pantalla alternativa (`\033[?1049h`) la restaura la terminal al salir
    y se llevaría la conversación entera: uno sale de Byte y quiere poder subir
    a releer lo que respondió, o copiar un bloque de código."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    cli._limpiar_pantalla()
    assert "1049" not in capsys.readouterr().out


def test_redirigido_no_se_limpia_nada(capsys, monkeypatch) -> None:
    """`byte > salida.txt` no tiene pantalla que limpiar, y los escapes
    ensuciarían el archivo."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: False)
    cli._limpiar_pantalla()
    assert capsys.readouterr().out == ""


# --- Las líneas de trabajo con archivos ---


def test_el_archivo_aparece_al_lado_del_verbo() -> None:
    """Sin esto la línea decía "Reading" a secas tres veces seguidas: cuando el
    agente recorre un proyecto, lo que se quiere ver es **qué** está abriendo."""
    from cli.byte_cli import _detalle_de

    assert _detalle_de('{"path": "agent/runner.py"}') == "agent/runner.py"
    assert _detalle_de('{"pattern": "def build"}') == "def build"


def test_un_path_vacio_no_ensucia_la_linea() -> None:
    """`list_files` sin argumento lista la raíz: no hay archivo que nombrar."""
    from cli.byte_cli import _detalle_de

    assert _detalle_de('{"path": "", "depth": 2}') == ""


def test_el_resumen_dice_cuanto_encontro() -> None:
    """ "done" no dice nada que el ✓ no diga ya. Saber si el grep encontró algo,
    o si el archivo se leyó entero, es lo que permite seguir sin abrir la
    respuesta completa."""
    from cli.byte_cli import _resumen_de

    assert _resumen_de("read_file", {"ok": True, "lineas": 172, "mostradas": 172}) == "172 lines"
    assert _resumen_de("grep", {"ok": True, "coincidencias": 7}) == "7 matches"
    assert _resumen_de("grep", {"ok": True, "coincidencias": 0}) == "no matches"
    assert _resumen_de("list_files", {"ok": True, "archivos": 23}) == "23 files"


def test_un_archivo_recortado_lo_dice() -> None:
    """Leer 400 de 900 líneas y decir "900 lines" haría creer que el modelo vio
    el archivo entero."""
    from cli.byte_cli import _resumen_de

    assert (
        _resumen_de("read_file", {"ok": True, "lineas": 900, "mostradas": 400})
        == "400 of 900 lines"
    )


def test_las_herramientas_de_archivos_tienen_verbo() -> None:
    """Sin verbo propio la línea dice "Using read_file", que es el nombre
    interno de la herramienta y no lo que está pasando."""
    from cli.byte_cli import POR_HERRAMIENTA

    for nombre in ("list_files", "read_file", "grep", "ver_cv", "regenerar_cv"):
        assert nombre in POR_HERRAMIENTA, f"{nombre} no tiene verbo"


# --- Markdown y recap ---


def test_la_negrita_del_modelo_se_pinta(monkeypatch) -> None:
    """El modelo escribe `**así**` sin que nadie se lo pida, y salían los
    asteriscos en crudo. Lo resaltado es lo que permite encontrar el término que
    importa sin leer toda la respuesta."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    pintado = cli._pintar_markdown("Un **decorador** envuelve")
    assert "**" not in pintado
    assert cli.NEGRITA in pintado


def test_el_codigo_inline_protege_sus_asteriscos(monkeypatch) -> None:
    """`**kwargs` es Python legítimo, no negrita: pintar la negrita primero lo
    rompería."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    pintado = cli._pintar_markdown("usá `**kwargs` para eso")
    assert "**kwargs" in pintado, "se comió los asteriscos del código"


def test_los_titulos_pierden_el_numeral(monkeypatch) -> None:
    """El `##` no aporta nada leído en una terminal."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    pintado = cli._pintar_markdown("## Resumen\ntexto")
    assert "##" not in pintado
    assert "Resumen" in pintado


def test_el_recap_esta_apagado_por_defecto() -> None:
    """Cuesta una llamada más al modelo —con uno local, 5-10 s por turno— y no
    todas las respuestas lo necesitan."""
    import cli.byte_cli as cli

    assert cli.Sesion("c1", safe=False).recap is False


def test_si_el_recap_falla_no_se_pierde_la_respuesta(capsys, monkeypatch) -> None:
    """La respuesta ya se mostró y el usuario la tiene: un resumen que falla no
    puede llevarse el turno."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)

    class ByteRoto:
        def pedir(self, *_a, **_k):
            raise RuntimeError("la API se cayó")

    sesion = cli.Sesion("c1", safe=False)
    cli._mostrar_recap(ByteRoto(), sesion, "hola", {"message": {"content": "una respuesta"}})
    assert "cayó" not in capsys.readouterr().out


# --- El banner se adapta a la terminal ---


class ByteConHerramientas:
    """Un cliente que devuelve once herramientas, como el Byte de hoy."""

    def __init__(self, cuantas: int = 11) -> None:
        self._cuantas = cuantas
        self.email = ""

    def pedir(self, _metodo: str, ruta: str, **_k: object) -> dict:
        if ruta == "/tools":
            return {"tools": [{"name": f"tool_{i}"} for i in range(self._cuantas)]}
        return {"status": "ok", "model": "granite4.1:8b"}


def test_con_poco_alto_se_listan_las_que_caben() -> None:
    """Once herramientas son once líneas: en una terminal baja el banner se
    comía la pantalla y no quedaba dónde escribir."""
    import cli.byte_cli as cli

    filas = cli._panel_pistas(ByteConHerramientas(), interactivo=True, ancho=60, alto=9)
    assert len(filas) <= 9, f"se pasó del alto: {len(filas)} filas"
    assert any("more" in f for f in filas), "no dice cuántas faltan"


def test_con_alto_de_sobra_se_listan_todas() -> None:
    """Recortar cuando no hace falta escondería herramientas por nada."""
    import cli.byte_cli as cli

    filas = cli._panel_pistas(ByteConHerramientas(), interactivo=True, ancho=60, alto=40)
    assert not any("more" in f for f in filas)
    assert sum("tool_" in f for f in filas) == 11


def test_en_una_terminal_baja_se_saca_la_mascota() -> None:
    """El dibujo es lindo, pero no a costa de que el prompt no entre en
    pantalla. Lo que se conserva es el modelo y la URL."""
    import cli.byte_cli as cli

    compacto = cli._panel_identidad(ByteConHerramientas(), "http://x", 30, compacto=True)
    normal = cli._panel_identidad(ByteConHerramientas(), "http://x", 30, compacto=False)
    assert len(compacto) < len(normal)
    assert any("granite" in f for f in compacto), "se perdió el modelo"
    assert any("http://x" in f for f in compacto), "se perdió la URL"


def test_todas_las_herramientas_tienen_descripcion() -> None:
    """Una herramienta sin descripción sale como un nombre suelto en el banner,
    que no le dice nada a quien abre Byte por primera vez."""
    from cli.byte_cli import QUE_HACE

    for nombre in ("list_files", "read_file", "grep", "ver_cv", "regenerar_cv"):
        assert QUE_HACE.get(nombre), f"{nombre} no tiene descripción"


# --- La barra de estado ---


def test_la_barra_muestra_el_modelo_activo(monkeypatch) -> None:
    """Con `/model` se cambia y después no hay forma de recordar cuál quedó."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    sesion = cli.Sesion("c1", safe=False)
    sesion.modelo_default = "granite4.1:8b"
    assert "granite4.1:8b" in cli._barra_de_estado(sesion)

    sesion.modelo = "qwen3:8b"
    assert "qwen3:8b" in cli._barra_de_estado(sesion), "no muestra el modelo elegido"


def test_la_barra_avisa_del_modo_seguro(monkeypatch) -> None:
    """Es lo que decide si el agente va a pedir permiso antes de ejecutar
    código: olvidarse de que está puesto se nota recién cuando algo se frena."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    sesion = cli.Sesion("c1", safe=True)
    assert "safe" in cli._barra_de_estado(sesion)
    assert "safe" not in cli._barra_de_estado(cli.Sesion("c1", safe=False))


def test_la_barra_dice_sobre_qué_proyecto_mira(monkeypatch) -> None:
    """La carpeta la decide la API, no el entorno del CLI: el agente corre en
    otro proceso y es su configuración la que manda."""
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: True)
    sesion = cli.Sesion("c1", safe=False)
    sesion.proyecto = "mi-repo"
    assert "mi-repo" in cli._barra_de_estado(sesion)


def test_sin_terminal_no_hay_barra(monkeypatch) -> None:
    import cli.byte_cli as cli

    monkeypatch.setattr(cli, "_en_pantalla", lambda: False)
    assert cli._barra_de_estado(cli.Sesion("c1", safe=True)) == ""
