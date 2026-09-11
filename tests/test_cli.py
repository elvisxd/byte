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

import pytest
from starlette.testclient import TestClient

from cli import byte_cli
from tests.fakes import text_turn, tool_turn

API_KEY = "clave-de-prueba"


def _parchear_transporte(monkeypatch: pytest.MonkeyPatch, cliente: TestClient) -> None:
    """Apunta el CLI a la app de pruebas en vez de a la red.

    Se parchea el punto exacto donde toca HTTP, así se ejercita todo lo demás
    (parseo, códigos de salida, formato, el stream) sin levantar un servidor.
    """

    def init(self: byte_cli.Byte, base_url: str, api_key: str) -> None:  # noqa: ARG001
        self._cliente = None

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

    def a_veces_falla(byte, conversacion, pregunta, safe):  # noqa: ANN001, ANN202
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

    def a_veces_interrumpe(byte, conversacion, pregunta, safe):  # noqa: ANN001, ANN202
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

    def espiar(byte, conversacion, pregunta, safe):  # noqa: ANN001, ANN202
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
        lambda b, c, p, safe: (vistos.append(safe), original(b, c, p, safe))[1],
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
