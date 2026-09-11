"""Runs: eventos AG-UI por SSE, reconexión, cancelación y límites."""

import json
from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import AUTH, nueva_conversacion
from tests.fakes import BrokenLLM, FakeLLM, OllamaCaidoLLM, SlowLLM, text_turn, tool_turn


def leer_eventos(client: TestClient, url: str, **params: object) -> list[tuple[str, dict]]:
    """Consume un stream SSE completo y devuelve (tipo, data) por evento."""
    eventos: list[tuple[str, dict]] = []
    with client.stream("GET", url, params=params, headers=AUTH) as respuesta:
        assert respuesta.status_code == 200, respuesta.read()
        assert respuesta.headers["content-type"].startswith("text/event-stream")
        assert respuesta.headers["cache-control"].startswith("no-cache")
        assert respuesta.headers["x-accel-buffering"] == "no"
        tipo = None
        for linea in respuesta.iter_lines():
            if linea.startswith("event: "):
                tipo = linea.removeprefix("event: ")
            elif linea.startswith("data: ") and tipo:
                eventos.append((tipo, json.loads(linea.removeprefix("data: "))))
    return eventos


def crear_run(client: TestClient, conversacion: str, contenido: str = "hola") -> dict:
    respuesta = client.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": contenido},
        headers=AUTH,
    )
    assert respuesta.status_code == 202, respuesta.text
    return respuesta.json()


def test_run_completo_con_herramienta(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(
        turns=[
            tool_turn("web_search", '{"query": "ultima version de fastapi"}'),
            text_turn("La última versión es 0.141.1."),
        ]
    )
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion, "qué versión de fastapi hay?")
    assert aceptado["events_url"] == f"/api/v1/runs/{aceptado['run_id']}/events"

    eventos = leer_eventos(cliente, aceptado["events_url"])
    tipos = [tipo for tipo, _ in eventos]

    # El ciclo ReAct completo, en orden, con los nombres del protocolo AG-UI.
    assert tipos[0] == "RUN_STARTED"
    assert tipos[-1] == "RUN_FINISHED"
    for esperado in (
        "STEP_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "STATE_DELTA",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "STATE_SNAPSHOT",
    ):
        assert esperado in tipos, f"falta {esperado}"

    # El texto llega token a token y se rearma completo.
    texto = "".join(d["delta"] for t, d in eventos if t == "TEXT_MESSAGE_CONTENT")
    assert texto == "La última versión es 0.141.1."

    final = eventos[-1][1]
    assert final["status"] == "finished"
    assert final["message_id"]
    assert final["sources"][0]["url"] == "https://fastapi.tiangolo.com"
    # La query que mandó el modelo llegó a la herramienta.
    assert cliente.tavily.queries == ["ultima version de fastapi"]  # type: ignore[attr-defined]


def test_todos_los_eventos_llevan_id_incremental(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)
    ids: list[int] = []
    with cliente.stream("GET", aceptado["events_url"], headers=AUTH) as respuesta:
        for linea in respuesta.iter_lines():
            if linea.startswith("id: "):
                ids.append(int(linea.removeprefix("id: ")))
    assert ids == list(range(1, len(ids) + 1))


def test_reconexion_con_last_event_id(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)
    completo = leer_eventos(cliente, aceptado["events_url"])
    assert len(completo) > 3

    # Reconectar pidiendo desde el evento 3: solo debe repetir lo que falta.
    with cliente.stream(
        "GET",
        aceptado["events_url"],
        headers={**AUTH, "Last-Event-ID": "3"},
    ) as respuesta:
        reanudado = [
            int(linea.removeprefix("id: "))
            for linea in respuesta.iter_lines()
            if linea.startswith("id: ")
        ]
    assert reanudado[0] == 4
    assert len(reanudado) == len(completo) - 3


def test_wait_devuelve_el_mensaje(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(
        turns=[
            tool_turn("web_search", '{"query": "fastapi"}'),
            text_turn("Listo, lo busqué."),
        ]
    )
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá algo"},
        params={"wait": True},
        headers=AUTH,
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["message"]["role"] == "assistant"
    assert cuerpo["message"]["content"] == "Listo, lo busqué."
    # metadata alimenta la UI: fuentes, herramientas usadas e iteraciones.
    assert cuerpo["message"]["metadata"]["tools_used"] == ["web_search"]
    assert cuerpo["message"]["metadata"]["iterations"] == 2
    assert cuerpo["sources"]

    # El mensaje se puede recuperar por su id.
    por_id = cliente.get(f"/api/v1/messages/{cuerpo['message']['id']}", headers=AUTH)
    assert por_id.status_code == 200


def test_estado_del_run(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)
    leer_eventos(cliente, aceptado["events_url"])
    # El run terminado sigue consultable después de cerrar el stream.
    estado = cliente.get(f"/api/v1/runs/{aceptado['run_id']}", headers=AUTH).json()
    assert estado["status"] == "finished"
    assert estado["iterations"] >= 1
    assert estado["started_at"]
    assert estado["finished_at"]


def test_cancelar_run(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(llm=SlowLLM(delay_s=30))
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)

    assert (
        cliente.post(f"/api/v1/runs/{aceptado['run_id']}/cancel", headers=AUTH).status_code == 202
    )
    estado = cliente.get(f"/api/v1/runs/{aceptado['run_id']}", headers=AUTH).json()
    assert estado["status"] == "cancelled"

    # El stream cierra con RUN_FINISHED status cancelled.
    eventos = leer_eventos(cliente, aceptado["events_url"])
    assert eventos[-1][0] == "RUN_FINISHED"
    assert eventos[-1][1]["status"] == "cancelled"


def test_error_del_modelo_termina_en_run_error(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(llm=BrokenLLM())
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)
    eventos = leer_eventos(cliente, aceptado["events_url"])
    assert eventos[-1][0] == "RUN_ERROR"
    # Al cliente no le llega el detalle interno.
    assert "ollama caido" not in json.dumps(eventos[-1][1])
    assert (
        cliente.get(f"/api/v1/runs/{aceptado['run_id']}", headers=AUTH).json()["status"] == "error"
    )


def test_timeout_del_run(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(llm=SlowLLM(delay_s=30), BYTE_RUN_TIMEOUT_S=1)
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)
    eventos = leer_eventos(cliente, aceptado["events_url"])
    assert eventos[-1][0] == "RUN_ERROR"
    assert eventos[-1][1]["code"] == "run_timeout"


def test_tope_de_runs_concurrentes(crear_cliente: Callable[..., TestClient]) -> None:
    """El tope es por credencial: se prueba con dos conversaciones distintas,
    porque dentro de una sola el segundo run corta antes con 409."""
    cliente = crear_cliente(llm=SlowLLM(delay_s=30), BYTE_MAX_CONCURRENT_RUNS=1)
    primera = nueva_conversacion(cliente)
    segunda = nueva_conversacion(cliente)
    crear_run(cliente, primera)
    otro = cliente.post(
        f"/api/v1/conversations/{segunda}/messages",
        json={"content": "otra"},
        headers=AUTH,
    )
    assert otro.status_code == 429
    assert otro.json()["error"]["code"] == "too_many_runs"
    assert otro.headers["retry-after"] == "5"


def test_mensaje_demasiado_largo(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(BYTE_MAX_MESSAGE_CHARS=100)
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "x" * 101},
        headers=AUTH,
    )
    assert respuesta.status_code == 413
    assert respuesta.json()["error"]["code"] == "payload_too_large"


def test_rate_limit_de_runs(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(BYTE_RATE_LIMIT_RUNS="2/minute")
    conversacion = nueva_conversacion(cliente)
    for _ in range(2):
        respuesta = cliente.post(
            f"/api/v1/conversations/{conversacion}/messages",
            json={"content": "hola"},
            params={"wait": True},
            headers=AUTH,
        )
        assert respuesta.status_code == 200
    tercera = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "hola"},
        headers=AUTH,
    )
    assert tercera.status_code == 429
    assert tercera.json()["error"]["code"] == "rate_limited"
    assert tercera.headers["retry-after"] == "60"


def test_el_agente_recuerda_dentro_de_la_conversacion(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """El checkpointer mantiene el hilo: el segundo run ve el primero."""
    modelo = FakeLLM([text_turn("Hola Elvis."), text_turn("Te llamás Elvis.")])
    cliente = crear_cliente(llm=modelo)
    conversacion = nueva_conversacion(cliente)
    for texto in ("me llamo Elvis", "cómo me llamo?"):
        cliente.post(
            f"/api/v1/conversations/{conversacion}/messages",
            json={"content": texto},
            params={"wait": True},
            headers=AUTH,
        )
    detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    assert [m["role"] for m in detalle["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert modelo.calls == 2


def test_modelo_caido_da_codigo_propio(crear_cliente: Callable[..., TestClient]) -> None:
    """Sin Ollama levantado, el run falla claro: model_unavailable, no 500 genérico."""
    cliente = crear_cliente(llm=OllamaCaidoLLM())
    conversacion = nueva_conversacion(cliente)
    aceptado = crear_run(cliente, conversacion)
    eventos = leer_eventos(cliente, aceptado["events_url"])
    assert eventos[-1][0] == "RUN_ERROR"
    assert eventos[-1][1]["code"] == "model_unavailable"
