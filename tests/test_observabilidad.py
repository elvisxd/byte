"""Trazas del agente y tracking de errores (Fase 4).

Las dos son opcionales y mandan datos fuera de tu máquina, así que lo que se
prueba es sobre todo lo que **no** tiene que pasar: que apagadas no salga nada,
que encendidas no puedan tirar abajo un run, y que lo que sale vaya redactado.
"""

import contextlib
from collections.abc import Callable
from typing import Any

import pytest
from starlette.testclient import TestClient

from api.observabilidad import Trazas, build_trazas, init_errores
from tests.conftest import AUTH
from tests.fakes import text_turn


class ClienteFalso:
    """Un Langfuse que anota lo que le mandan, sin red de por medio."""

    def __init__(self, explota: bool = False) -> None:
        self.observaciones: list[dict[str, Any]] = []
        self.flushes = 0
        self._explota = explota

    def start_as_current_observation(self, **kwargs: Any) -> Any:
        # Explota **al abrir**, que es como falla de verdad: claves mal, red
        # caída, SDK que no arranca. Por eso no es un contextmanager: si lo
        # fuera, la excepción saldría dentro del `with` y estaríamos probando
        # otra cosa.
        if self._explota:
            raise RuntimeError("langfuse caído")
        self.observaciones.append(kwargs)
        return contextlib.nullcontext()

    def get_current_trace_id(self) -> str:
        return "traza-de-prueba"

    def flush(self) -> None:
        self.flushes += 1


class Ajustes:
    """Lo mínimo que `build_trazas` e `init_errores` miran."""

    def __init__(self, **valores: Any) -> None:
        self.langfuse_public_key = valores.get("langfuse_public_key", "")
        self.langfuse_secret_key = valores.get("langfuse_secret_key", "")
        self.langfuse_host = valores.get("langfuse_host", "https://cloud.langfuse.com")
        self.bugsink_dsn = valores.get("bugsink_dsn", "")
        self.env = "dev"
        self.version = "0.1.0"


# --- Apagadas por defecto ---


def test_sin_claves_no_se_manda_nada() -> None:
    """Byte corre local: encender Langfuse manda tus conversaciones a un
    tercero, así que tiene que ser una decisión explícita."""
    assert build_trazas(Ajustes()).activas is False


def test_con_una_sola_clave_tampoco() -> None:
    """Media configuración es un error de tipeo, no una intención."""
    assert build_trazas(Ajustes(langfuse_public_key="pk-lf-algo")).activas is False


def test_sin_dsn_no_hay_tracking_de_errores() -> None:
    assert init_errores(Ajustes()) is False


def test_una_traza_apagada_no_rompe_nada() -> None:
    """El objeto apagado es un no-op: el runner lo llama igual y no se llena de
    `if trazas is not None`."""
    apagadas = Trazas()
    with apagadas.run("run", conversation_id="c1"):
        pass
    assert apagadas.trace_id() is None
    apagadas.flush()  # no explota


# --- Lo que sale, sale redactado ---


def test_lo_que_va_a_la_traza_se_redacta() -> None:
    """La redacción va en el `mask` del cliente, pero el input del run se
    redacta también acá: es lo que el usuario escribió, y el `mask` es la red de
    atrás, no la única."""
    falso = ClienteFalso()
    with Trazas(falso).run("run", content="mi clave es sk-proj-AbCdEfGh1234567890"):
        pass
    assert falso.observaciones[0]["input"]["content"] == "mi clave es [API_KEY]"


def test_el_cliente_de_langfuse_lleva_el_mask(monkeypatch) -> None:
    """Sin el `mask` en el constructor, el SDK manda todo en claro: es el peor
    fallo posible porque es silencioso."""
    capturado: dict[str, Any] = {}

    class LangfuseFalso:
        def __init__(self, **kwargs: Any) -> None:
            capturado.update(kwargs)

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", LangfuseFalso)
    build_trazas(Ajustes(langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-y"))

    assert "mask" in capturado, "el cliente se armó sin redacción"
    assert capturado["mask"](data="clave sk-proj-AbCdEfGh1234567890") == "clave [API_KEY]"


# --- Observar no puede romper el run ---


def test_si_langfuse_explota_el_run_sigue() -> None:
    """Observar no es una funcionalidad por la que valga la pena fallar: si el
    SDK se cae, Byte sigue sin trazas."""
    ejecutado = False
    with Trazas(ClienteFalso(explota=True)).run("run"):
        ejecutado = True
    assert ejecutado, "el run no corrió porque la traza falló"


def test_un_error_del_run_llega_entero_aunque_haya_traza() -> None:
    """El `try` cubre solo abrir la observación, nunca el cuerpo.

    Envolver el `yield` hacía que cualquier excepción del run entrara al except
    —se registraba como `langfuse_traza_fallo`, culpando a Langfuse por un bug
    ajeno— y el segundo `yield` la convertía en `RuntimeError: generator didn't
    stop after throw()`, perdiendo la causa. Peor: lo que escapara del `try` de
    `_correr` (el `finally` que hace `run.done.set()`) dejaba de correr, y quien
    esperaba ese run se colgaba para siempre.
    """
    with pytest.raises(ValueError, match="el grafo explotó"), Trazas(ClienteFalso()).run("run"):
        raise ValueError("el grafo explotó")

    # Y lo mismo con Langfuse apagado, que es el camino normal.
    with pytest.raises(ValueError, match="el grafo explotó"), Trazas().run("run"):
        raise ValueError("el grafo explotó")


def test_si_langfuse_no_esta_instalado_se_avisa_y_se_sigue(monkeypatch) -> None:
    """Con claves configuradas pero sin el paquete, hay que decirlo: si no,
    alguien cree que está viendo trazas y no hay ninguna."""
    import builtins

    real = builtins.__import__

    def sin_langfuse(nombre: str, *args: Any, **kwargs: Any) -> Any:
        if nombre == "langfuse":
            raise ImportError("no está")
        return real(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sin_langfuse)
    trazas = build_trazas(Ajustes(langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-y"))
    assert trazas.activas is False


# --- El puente entre un mensaje y su traza ---


def test_el_trace_id_queda_guardado_en_el_mensaje(
    crear_cliente: Callable[..., TestClient], monkeypatch
) -> None:
    """Sin esto, las trazas y los mensajes son dos listas que no se cruzan y
    depurar obliga a adivinar cuál corresponde a cuál."""
    import api.main as main

    falso = ClienteFalso()
    monkeypatch.setattr(main, "build_trazas", lambda _s: Trazas(falso))

    cliente = crear_cliente(turns=[text_turn("Listo.")])
    conversacion = cliente.post("/api/v1/conversations", json={}, headers=AUTH).json()
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion['id']}/messages?wait=true",
        json={"content": "hola"},
        headers=AUTH,
    )

    assert respuesta.json()["message"]["langfuse_trace_id"] == "traza-de-prueba"
    assert len(falso.observaciones) == 1, "el run tiene que quedar en una sola traza"


def test_sin_langfuse_el_trace_id_es_nulo(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """El caso normal: Byte corre sin trazas y el campo queda vacío."""
    cliente = crear_cliente(turns=[text_turn("Listo.")])
    conversacion = cliente.post("/api/v1/conversations", json={}, headers=AUTH).json()
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion['id']}/messages?wait=true",
        json={"content": "hola"},
        headers=AUTH,
    )
    assert respuesta.json()["message"]["langfuse_trace_id"] is None


# --- Bugsink (errores) ---


@pytest.fixture(autouse=True)
def _sentry_limpio():
    """Apaga Sentry al terminar cada test.

    `sentry_sdk.init` es global: sin esto queda inicializado para el resto de la
    suite y cualquier error posterior intenta mandarse a un puerto muerto,
    llenando la salida de reintentos.
    """
    yield
    import sentry_sdk

    sentry_sdk.init(dsn="")


def test_los_errores_salen_redactados() -> None:
    """El mensaje de un error de un agente trae el texto que lo causó: el
    prompt, el resultado de una herramienta, a veces una clave pegada."""
    import sentry_sdk

    assert init_errores(Ajustes(bugsink_dsn="http://clave@127.0.0.1:9999/1")) is True
    antes_de_enviar = sentry_sdk.get_client().options["before_send"]

    evento = {
        "message": "falló con la clave sk-proj-AbCdEfGh1234567890",
        "extra": {"email": "ana@ejemplo.com", "intentos": 2},
    }
    redactado = antes_de_enviar(evento, {})

    assert redactado["message"] == "falló con la clave [API_KEY]"
    assert redactado["extra"]["email"] == "[EMAIL]"
    assert redactado["extra"]["intentos"] == 2


def test_el_sdk_no_manda_pii_por_su_cuenta() -> None:
    """`send_default_pii` hace que el SDK agregue headers, cookies e IP — y eso
    incluye la credencial con la que alguien llamó. La redacción no lo cubre:
    para cuando el SDK los arma, ya son estructuras suyas."""
    import sentry_sdk

    init_errores(Ajustes(bugsink_dsn="http://clave@127.0.0.1:9999/1"))
    assert sentry_sdk.get_client().options["send_default_pii"] is False
