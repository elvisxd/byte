"""El CLI en Python.

Se prueba contra la app real vía TestClient: el CLI habla HTTP y nada más, así
que un doble del transporte alcanza para ejercitar el parseo, los códigos de
salida y el formato de la salida sin levantar un servidor.
"""

from collections.abc import Callable

import pytest
from starlette.testclient import TestClient

from cli import byte_cli
from tests.fakes import text_turn

API_KEY = "clave-de-prueba"


@pytest.fixture
def cli(
    crear_cliente: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., int]:
    """Corre el CLI contra la app de pruebas, devolviendo su código de salida."""
    cliente = crear_cliente(turns=[text_turn("Listo.")], con_sandbox=True)
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)

    # Se parchea el punto donde el CLI toca la red: así se ejercita todo lo
    # demás (parseo, códigos de salida, formato) sin levantar un servidor.
    def init(self: byte_cli.Byte, base_url: str, api_key: str) -> None:  # noqa: ARG001
        self._cliente = None

    def pedir(self: byte_cli.Byte, metodo: str, ruta: str, **kwargs: object) -> object:
        respuesta = cliente.request(
            metodo, f"/api/v1{ruta}", headers={"X-API-Key": API_KEY}, **kwargs
        )
        if respuesta.status_code >= 400:
            raise RuntimeError(byte_cli._mensaje_de_error(respuesta))
        return None if respuesta.status_code == 204 else respuesta.json()

    monkeypatch.setattr(byte_cli.Byte, "__init__", init)
    monkeypatch.setattr(byte_cli.Byte, "__exit__", lambda *_: None)
    monkeypatch.setattr(byte_cli.Byte, "pedir", pedir)
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
    assert "no existe" in capsys.readouterr().err


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


def test_el_spinner_no_dibuja_sin_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    """Redirigir la salida a un archivo no debería llenarlo de códigos ANSI."""
    with byte_cli.Pensando():
        pass
    assert capsys.readouterr().err == ""


def test_el_spinner_devuelve_el_cursor_aunque_falle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si el cursor queda escondido, la terminal se rompe para todo lo demás."""
    monkeypatch.setattr(byte_cli.sys.stderr, "isatty", lambda: True)
    with pytest.raises(ValueError, match="algo falló"), byte_cli.Pensando():
        raise ValueError("algo falló")
    assert "\033[?25h" in capsys.readouterr().err
