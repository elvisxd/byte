"""El endpoint compatible con OpenAI.

Lo que se prueba acá es el contrato con clientes que ya existen: si la forma de
la respuesta cambia, Open WebUI o Continue.dev dejan de andar sin que nada falle
del lado de Byte. Por eso los tests miran las claves del JSON y no solo el 200.

El camino con el SDK oficial de OpenAI se verificó a mano (está anotado en
docs/api-contrato-byte.md): acá no se instala, porque no es dependencia de Byte.
"""

import json
from collections.abc import Callable

import pytest
from starlette.testclient import TestClient

from agent import runner as byte_runner
from tests.fakes import text_turn, tool_turn

AUTH = {"X-API-Key": "clave-de-prueba"}
BEARER = {"Authorization": "Bearer clave-de-prueba"}


@pytest.fixture
def cliente(crear_cliente: Callable[..., TestClient]) -> TestClient:
    return crear_cliente(turns=[text_turn("Listo.")])


def _pedir(cliente: TestClient, **cambios: object) -> dict:
    cuerpo = {"model": "byte", "messages": [{"role": "user", "content": "hola"}]}
    cuerpo.update(cambios)
    return cliente.post("/v1/chat/completions", json=cuerpo, headers=BEARER)


# --- Autenticación ---


def test_los_clientes_de_openai_autentican_con_bearer(cliente: TestClient) -> None:
    """Es el único header que mandan: sin esto el endpoint sería inalcanzable
    para ellos por más que la ruta exista."""
    assert cliente.get("/v1/models", headers=BEARER).status_code == 200


def test_la_api_key_de_byte_tambien_sirve(cliente: TestClient) -> None:
    """El mismo endpoint desde curl o desde el CLI, sin cambiar de header."""
    assert cliente.get("/v1/models", headers=AUTH).status_code == 200


def test_sin_credencial_no_se_responde(cliente: TestClient) -> None:
    assert cliente.get("/v1/models").status_code == 401
    assert cliente.post("/v1/chat/completions", json={}).status_code == 401


def test_un_bearer_que_no_es_la_clave_no_entra(cliente: TestClient) -> None:
    respuesta = cliente.get("/v1/models", headers={"Authorization": "Bearer no-es-la-clave"})
    assert respuesta.status_code == 401


# --- Lista blanca de modelos ---


def test_un_modelo_arbitrario_nunca_llega_a_ollama(cliente: TestClient) -> None:
    """Sin la lista blanca, el `model` de un pedido decidiría qué modelo baja y
    corre Ollama (docs/seguridad-byte.md)."""
    respuesta = _pedir(cliente, model="../../etc/passwd")
    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["code"] == "model_not_found"


def test_el_modelo_de_otro_proveedor_se_rechaza(cliente: TestClient) -> None:
    """Un cliente configurado para GPT-4 tiene que enterarse, no recibir otra
    cosa en silencio."""
    assert _pedir(cliente, model="gpt-4-turbo").status_code == 404


def test_los_modelos_se_listan_para_el_selector_del_cliente(cliente: TestClient) -> None:
    cuerpo = cliente.get("/v1/models", headers=BEARER).json()
    assert cuerpo["object"] == "list"
    assert {m["id"] for m in cuerpo["data"]} == {"byte", "qwen3:8b"}
    assert all(m["object"] == "model" for m in cuerpo["data"])


def test_el_nombre_real_del_modelo_tambien_se_acepta(cliente: TestClient) -> None:
    """Quien ya lo tenga escrito en su cliente no debería tener que cambiarlo."""
    assert _pedir(cliente, model="qwen3:8b").status_code == 200


# --- La forma de la respuesta ---


def test_la_respuesta_tiene_la_forma_que_esperan_los_clientes(cliente: TestClient) -> None:
    """Si falta una clave, el cliente rompe con un KeyError y no con un error
    de Byte: por eso se verifican una por una."""
    cuerpo = _pedir(cliente).json()
    assert cuerpo["object"] == "chat.completion"
    assert cuerpo["id"].startswith("chatcmpl-")
    assert isinstance(cuerpo["created"], int)
    assert cuerpo["model"] == "byte"
    eleccion = cuerpo["choices"][0]
    assert eleccion["index"] == 0
    assert eleccion["finish_reason"] == "stop"
    assert eleccion["message"]["role"] == "assistant"
    assert eleccion["message"]["content"] == "Listo."


def test_el_usage_va_aunque_byte_no_cuente_tokens(cliente: TestClient) -> None:
    """Varios clientes rompen si el campo falta; cero es más honesto que un
    número inventado, porque un run son varias llamadas al modelo."""
    assert _pedir(cliente).json()["usage"]["total_tokens"] == 0


# --- Streaming ---


def _trozos(respuesta: object) -> list[dict]:
    """Los objetos JSON de un stream SSE de OpenAI, sin el `[DONE]`."""
    salida = []
    for linea in respuesta.text.splitlines():  # type: ignore[attr-defined]
        if linea.startswith("data: ") and linea != "data: [DONE]":
            salida.append(json.loads(linea[6:]))
    return salida


def test_el_stream_devuelve_los_chunks_de_openai(cliente: TestClient) -> None:
    respuesta = _pedir(cliente, stream=True)
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    trozos = _trozos(respuesta)
    assert all(t["object"] == "chat.completion.chunk" for t in trozos)
    # Todos los trozos de una respuesta comparten el id, como en OpenAI.
    assert len({t["id"] for t in trozos}) == 1


def test_el_stream_arranca_declarando_el_rol(cliente: TestClient) -> None:
    """Es lo que hace OpenAI, y algún cliente lo espera para abrir la burbuja."""
    assert _trozos(_pedir(cliente, stream=True))[0]["choices"][0]["delta"]["role"] == "assistant"


def test_el_texto_completo_sale_por_el_stream(cliente: TestClient) -> None:
    texto = "".join(
        t["choices"][0]["delta"].get("content", "") for t in _trozos(_pedir(cliente, stream=True))
    )
    assert texto == "Listo."


def test_el_stream_cierra_con_finish_reason_y_done(cliente: TestClient) -> None:
    """Sin el `[DONE]` el cliente se queda esperando."""
    respuesta = _pedir(cliente, stream=True)
    assert respuesta.text.rstrip().endswith("data: [DONE]")
    assert _trozos(respuesta)[-1]["choices"][0]["finish_reason"] == "stop"


# --- Lo que manda un cliente de verdad ---


def test_se_responde_al_ultimo_mensaje_del_usuario(cliente: TestClient) -> None:
    """El cliente reenvía toda la conversación en cada pedido; el historial lo
    maneja Byte con su checkpointer, así que solo hace falta el último turno."""
    respuesta = _pedir(
        cliente,
        messages=[
            {"role": "system", "content": "Sos un asistente."},
            {"role": "user", "content": "primera"},
            {"role": "assistant", "content": "respondí"},
            {"role": "user", "content": "la que importa"},
        ],
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["choices"][0]["message"]["content"] == "Listo."


def test_el_contenido_en_partes_tambien_se_entiende(cliente: TestClient) -> None:
    """El formato nuevo manda `content` como lista de partes; rechazarlo dejaría
    afuera a clientes que igual mandan solo texto."""
    respuesta = _pedir(
        cliente,
        messages=[{"role": "user", "content": [{"type": "text", "text": "hola"}]}],
    )
    assert respuesta.status_code == 200


def test_los_parametros_que_byte_no_usa_se_ignoran(cliente: TestClient) -> None:
    """Los clientes los mandan siempre. Un 422 por `temperature` los rompería
    sin que Byte gane nada: esos valores los decide su configuración."""
    respuesta = _pedir(cliente, temperature=0.7, top_p=0.9, n=1, seed=42, user="alguien")
    assert respuesta.status_code == 200


def test_un_pedido_sin_mensajes_del_usuario_se_rechaza(cliente: TestClient) -> None:
    respuesta = _pedir(cliente, messages=[{"role": "system", "content": "Sos un asistente."}])
    assert respuesta.status_code == 422


def test_un_mensaje_enorme_se_rechaza_como_en_la_api_nativa(cliente: TestClient) -> None:
    """El mismo límite que el endpoint nativo: entrar por OpenAI no puede ser
    una forma de saltarse los topes de Byte."""
    respuesta = _pedir(cliente, messages=[{"role": "user", "content": "x" * 20_000}])
    assert respuesta.status_code == 413


def test_la_conversacion_queda_guardada_como_cualquier_otra(cliente: TestClient) -> None:
    """Lo que entra por OpenAI se ve después en la web y en `byte conversations`."""
    _pedir(cliente, messages=[{"role": "user", "content": "desde open webui"}])
    items = cliente.get("/api/v1/conversations", headers=AUTH).json()["items"]
    assert any(c["title"].startswith("[openai]") for c in items)


# --- El modo seguro, que este formato no sabe expresar ---


@pytest.fixture
def cliente_que_pide_permiso(crear_cliente: Callable[..., TestClient]) -> TestClient:
    """Un run que dispara el modo seguro: buscar en la web y después ejecutar
    código lo activa solo, aunque nadie lo haya pedido."""
    return crear_cliente(
        turns=[
            tool_turn("web_search", '{"query": "algo"}', call_id="c1"),
            tool_turn("code_exec", json.dumps({"code": "print(1)"}), call_id="c2"),
            text_turn("listo"),
        ],
        con_sandbox=True,
    )


def test_un_run_que_necesita_aprobacion_no_deja_al_cliente_esperando(
    cliente_que_pide_permiso: TestClient,
) -> None:
    """No hay a quién preguntarle del otro lado: un cliente de OpenAI no tiene
    dónde mostrar un pedido de aprobación. Se corta y se explica, en vez de
    devolver una respuesta vacía o colgarse."""
    respuesta = cliente_que_pide_permiso.post(
        "/v1/chat/completions",
        json={"model": "byte", "messages": [{"role": "user", "content": "buscá y ejecutá"}]},
        headers=BEARER,
    )
    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["code"] == "approval_required"


def test_con_streaming_la_pausa_se_explica_en_el_texto(
    cliente_que_pide_permiso: TestClient,
) -> None:
    """Con el stream abierto ya no se puede cambiar el código HTTP, así que lo
    único que queda es decirlo donde el usuario lo va a leer."""
    respuesta = cliente_que_pide_permiso.post(
        "/v1/chat/completions",
        json={
            "model": "byte",
            "stream": True,
            "messages": [{"role": "user", "content": "buscá y ejecutá"}],
        },
        headers=BEARER,
    )
    assert respuesta.status_code == 200
    trozos = _trozos(respuesta)
    texto = "".join(t["choices"][0]["delta"].get("content", "") for t in trozos)
    assert "aprobación humana" in texto
    assert trozos[-1]["choices"][0]["finish_reason"] == "length"
    assert respuesta.text.rstrip().endswith("data: [DONE]")


# --- Lo que salió de revisar la fase ---


def test_un_run_que_falla_lo_dice_en_el_stream(
    crear_cliente: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con el stream ya abierto no se puede cambiar el código HTTP, así que el
    error tiene que ir en el texto.

    Sin esto el cliente recibe un 200 con `finish_reason: "stop"` y la burbuja
    vacía: parece que el modelo no tuvo nada que decir, y el usuario no se
    entera de que Byte falló.
    """
    cliente = crear_cliente(turns=[text_turn("Listo.")])
    monkeypatch.setenv("BYTE_API_KEY", "clave-de-prueba")

    async def romper(self, run, **kwargs):  # noqa: ANN001, ANN202, ARG001
        from agent.events import AGUI

        run.emit(AGUI.RUN_ERROR, {"message": "ollama no responde"})
        run.status = "error"

    monkeypatch.setattr(byte_runner.RunManager, "_execute", romper, raising=False)
    respuesta = cliente.post(
        "/v1/chat/completions",
        json={"model": "byte", "stream": True, "messages": [{"role": "user", "content": "hola"}]},
        headers=BEARER,
    )
    texto = "".join(t["choices"][0]["delta"].get("content", "") for t in _trozos(respuesta))
    assert "falló" in texto, f"el error no llegó al cliente: {texto!r}"
    assert respuesta.text.rstrip().endswith("data: [DONE]")


def test_un_run_pausado_no_queda_colgado(
    cliente_que_pide_permiso: TestClient,
) -> None:
    """`RunManager.cancel` no hace nada sobre un run pausado (`Run.finished`
    incluye "paused"), así que el run quedaba vivo con su interrupt de LangGraph
    hasta que lo purgara el TTL, una hora después.

    El endpoint dice que lo aborta: tiene que ser cierto.
    """
    respuesta = cliente_que_pide_permiso.post(
        "/v1/chat/completions",
        json={"model": "byte", "messages": [{"role": "user", "content": "buscá y ejecutá"}]},
        headers=BEARER,
    )
    assert respuesta.status_code == 409

    runs = cliente_que_pide_permiso.app.state.ctx.runs
    pausados = [r for r in runs._runs.values() if r.status == "paused"]
    assert pausados == [], f"quedó un run esperando aprobación que nadie va a dar: {pausados}"
