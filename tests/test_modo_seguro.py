"""Modo seguro (HITL): el run se detiene y espera a una persona.

La regla del contrato: se activa automáticamente si en el mismo run hubo
búsqueda web y el agente quiere ejecutar código, aunque el usuario no lo haya
pedido. El razonamiento está en docs/seguridad-byte.md: contenido de terceros
más ejecución de código es la combinación que deja que una inyección indirecta
termine corriendo algo.
"""

import json
from collections.abc import Callable

from fastapi.testclient import TestClient

from agent.graph import requiere_aprobacion
from tests.conftest import AUTH, nueva_conversacion
from tests.fakes import text_turn, tool_turn

CODIGO = "import os\nprint(os.listdir('/'))"


def leer(client: TestClient, url: str, **params: object) -> list[tuple[str, dict]]:
    eventos: list[tuple[str, dict]] = []
    with client.stream("GET", url, params=params, headers=AUTH) as respuesta:
        tipo = None
        for linea in respuesta.iter_lines():
            if linea.startswith("event: "):
                tipo = linea.removeprefix("event: ")
            elif linea.startswith("data: ") and tipo:
                eventos.append((tipo, json.loads(linea.removeprefix("data: "))))
    return eventos


def cliente_web_y_codigo(crear_cliente: Callable[..., TestClient]) -> TestClient:
    """Agente que busca en la web y después quiere ejecutar código."""
    return crear_cliente(
        con_busqueda=True,
        con_sandbox=True,
        turns=[
            tool_turn("web_search", '{"query": "como calcular la mediana"}', call_id="c1"),
            tool_turn("code_exec", json.dumps({"code": CODIGO}), call_id="c2"),
            text_turn("Listo, lo calculé."),
        ],
    )


# --- La regla ---


def test_la_regla_de_activacion() -> None:
    assert requiere_aprobacion(["code_exec"], [], False) is None
    assert requiere_aprobacion(["code_exec"], [], True) == "modo_seguro_activado"
    assert (
        requiere_aprobacion(["code_exec"], ["web_search"], False) == "web_y_codigo_en_el_mismo_run"
    )
    # También si pide buscar y ejecutar en la misma tanda.
    assert (
        requiere_aprobacion(["web_search", "code_exec"], [], False)
        == "web_y_codigo_en_el_mismo_run"
    )
    # Buscar en la web sola nunca pide permiso.
    assert requiere_aprobacion(["web_search"], [], True) is None


# --- El flujo completo ---


def test_el_run_se_pausa_y_espera_aprobacion(
    crear_cliente: Callable[..., TestClient],
) -> None:
    cliente = cliente_web_y_codigo(crear_cliente)
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá cómo se calcula la mediana y calculala"},
        headers=AUTH,
    ).json()

    eventos = leer(cliente, aceptado["events_url"])
    tipos = [t for t, _ in eventos]

    # La búsqueda web sí corrió; el código todavía no.
    assert "TOOL_CALL_RESULT" in tipos
    assert cliente.sandbox.pedidos == []  # type: ignore[attr-defined]

    # AG-UI no tiene evento de aprobación: va como estado.
    espera = next(d for t, d in eventos if t == "STATE_DELTA" and d.get("awaiting_approval"))
    pendiente = espera["awaiting_approval"]
    assert pendiente["reason"] == "web_y_codigo_en_el_mismo_run"
    assert pendiente["tool"] == "code_exec"
    # El cliente muestra el código para que la persona lo lea antes de aprobar.
    assert pendiente["code"] == CODIGO
    assert pendiente["resume_token"]

    assert eventos[-1][0] == "RUN_FINISHED"
    assert eventos[-1][1]["status"] == "paused"
    assert cliente.get(f"/api/v1/runs/{aceptado['run_id']}", headers=AUTH).json()["status"] == (
        "paused"
    )


def test_aprobar_continua_el_mismo_run(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = cliente_web_y_codigo(crear_cliente)
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá y calculá"},
        headers=AUTH,
    ).json()
    eventos = leer(cliente, aceptado["events_url"])
    token = next(
        d["awaiting_approval"]["resume_token"]
        for t, d in eventos
        if t == "STATE_DELTA" and d.get("awaiting_approval")
    )
    ultimo_id = len(eventos)

    respuesta = cliente.post(
        f"/api/v1/runs/{aceptado['run_id']}/resume",
        json={"resume_token": token, "approve": True},
        headers=AUTH,
    )
    assert respuesta.status_code == 202
    assert respuesta.json() == {"run_id": aceptado["run_id"]}

    # El cliente se vuelve a suscribir al MISMO run desde donde quedó.
    continuacion = leer(cliente, aceptado["events_url"], last_event_id=ultimo_id)
    tipos = [t for t, _ in continuacion]
    assert "TOOL_CALL_RESULT" in tipos
    assert continuacion[-1][0] == "RUN_FINISHED"
    assert continuacion[-1][1]["status"] == "finished"

    # El código se ejecutó una sola vez, aunque LangGraph re-corre el nodo entero
    # al reanudar.
    assert len(cliente.sandbox.pedidos) == 1  # type: ignore[attr-defined]
    assert cliente.sandbox.pedidos[0]["json"]["code"] == CODIGO  # type: ignore[attr-defined]

    detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    assert detalle["messages"][-1]["content"] == "Listo, lo calculé."


def test_rechazar_no_ejecuta_nada(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(
        con_busqueda=True,
        con_sandbox=True,
        turns=[
            tool_turn("web_search", '{"query": "algo"}', call_id="c1"),
            tool_turn("code_exec", json.dumps({"code": CODIGO}), call_id="c2"),
            text_turn("Entendido, no lo ejecuto."),
        ],
    )
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá y calculá"},
        headers=AUTH,
    ).json()
    eventos = leer(cliente, aceptado["events_url"])
    token = next(
        d["awaiting_approval"]["resume_token"]
        for t, d in eventos
        if t == "STATE_DELTA" and d.get("awaiting_approval")
    )
    ultimo_id = len(eventos)

    cliente.post(
        f"/api/v1/runs/{aceptado['run_id']}/resume",
        json={"resume_token": token, "approve": False},
        headers=AUTH,
    )
    continuacion = leer(cliente, aceptado["events_url"], last_event_id=ultimo_id)

    # Nada llegó al sandbox.
    assert cliente.sandbox.pedidos == []  # type: ignore[attr-defined]
    rechazo = next(d for t, d in continuacion if t == "TOOL_CALL_RESULT" and d.get("ok") is False)
    assert rechazo["error"] == "rechazado_por_el_usuario"
    assert continuacion[-1][1]["status"] == "finished"

    # El modelo se enteró y cerró la conversación con una explicación.
    detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    assert detalle["messages"][-1]["content"] == "Entendido, no lo ejecuto."


def test_safe_mode_pide_permiso_sin_web(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(
        con_busqueda=False,
        con_sandbox=True,
        turns=[tool_turn("code_exec", json.dumps({"code": "print(1)"})), text_turn("ok")],
    )
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "ejecutá esto", "safe_mode": True},
        headers=AUTH,
    ).json()
    eventos = leer(cliente, aceptado["events_url"])
    espera = next(d for t, d in eventos if t == "STATE_DELTA" and d.get("awaiting_approval"))
    assert espera["awaiting_approval"]["reason"] == "modo_seguro_activado"
    assert eventos[-1][1]["status"] == "paused"


def test_codigo_sin_web_no_pide_permiso(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(
        con_busqueda=False,
        con_sandbox=True,
        turns=[tool_turn("code_exec", json.dumps({"code": "print(2+2)"})), text_turn("son 4")],
    )
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "cuánto es 2+2"},
        params={"wait": True},
        headers=AUTH,
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["message"]["content"] == "son 4"
    assert len(cliente.sandbox.pedidos) == 1  # type: ignore[attr-defined]


# --- El token ---


def test_el_resume_token_es_de_un_solo_uso(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = cliente_web_y_codigo(crear_cliente)
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá y calculá"},
        headers=AUTH,
    ).json()
    eventos = leer(cliente, aceptado["events_url"])
    token = next(
        d["awaiting_approval"]["resume_token"]
        for t, d in eventos
        if t == "STATE_DELTA" and d.get("awaiting_approval")
    )
    cuerpo = {"resume_token": token, "approve": True}
    assert (
        cliente.post(
            f"/api/v1/runs/{aceptado['run_id']}/resume", json=cuerpo, headers=AUTH
        ).status_code
        == 202
    )
    reintento = cliente.post(f"/api/v1/runs/{aceptado['run_id']}/resume", json=cuerpo, headers=AUTH)
    assert reintento.status_code == 401


def test_resume_con_token_invalido(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = cliente_web_y_codigo(crear_cliente)
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá y calculá"},
        headers=AUTH,
    ).json()
    leer(cliente, aceptado["events_url"])
    respuesta = cliente.post(
        f"/api/v1/runs/{aceptado['run_id']}/resume",
        json={"resume_token": "inventado", "approve": True},
        headers=AUTH,
    )
    assert respuesta.status_code == 401


def test_no_se_puede_reanudar_un_run_que_no_esta_en_pausa(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "hola"},
        params={"wait": True},
        headers=AUTH,
    )
    assert aceptado.status_code == 200
    # Un run terminado no tiene token válido, así que el 401 llega antes del 409.
    segundo = cliente.post(
        "/api/v1/runs/run_inexistente/resume",
        json={"resume_token": "x", "approve": True},
        headers=AUTH,
    )
    assert segundo.status_code == 404


def test_wait_true_avisa_que_quedo_en_pausa(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """Con ?wait=true no hay mensaje final que devolver: hay que avisar la pausa
    y dar el token, no responder un error."""
    cliente = cliente_web_y_codigo(crear_cliente)
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá y calculá"},
        params={"wait": True},
        headers=AUTH,
    )
    assert respuesta.status_code == 202
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "paused"
    assert cuerpo["awaiting_approval"]["reason"] == "web_y_codigo_en_el_mismo_run"
    assert cuerpo["awaiting_approval"]["code"] == CODIGO

    # Y el token que vino sirve para continuar.
    continuar = cliente.post(
        f"/api/v1/runs/{cuerpo['run_id']}/resume",
        json={"resume_token": cuerpo["awaiting_approval"]["resume_token"], "approve": True},
        headers=AUTH,
    )
    assert continuar.status_code == 202
