"""Lo que pasa alrededor de una aprobación pendiente y del buffer de eventos.

Los tres casos de acá fueron bugs reales encontrados revisando el código:
un run pausado que se purgaba con la aprobación adentro, un mensaje nuevo que
abandonaba esa aprobación dejando el historial inconsistente, y el replay que
perdía texto en silencio.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agent.runner import RUNS_CON_TEXTO_COMPLETO, Run, RunManager
from tests.conftest import AUTH, nueva_conversacion
from tests.fakes import text_turn, tool_turn

CODIGO = "print('hola')"


def agente_que_pide_aprobacion(crear_cliente: Callable[..., TestClient]) -> TestClient:
    return crear_cliente(
        con_busqueda=True,
        con_sandbox=True,
        turns=[
            tool_turn("web_search", '{"query": "algo"}', call_id="c1"),
            tool_turn("code_exec", json.dumps({"code": CODIGO}), call_id="c2"),
            text_turn("listo"),
        ],
    )


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


def pausar(cliente: TestClient) -> tuple[str, str, str]:
    """Deja un run esperando aprobación y devuelve (conversación, run, token)."""
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "buscá y calculá"},
        params={"wait": True},
        headers=AUTH,
    ).json()
    assert respuesta["status"] == "paused"
    return conversacion, respuesta["run_id"], respuesta["awaiting_approval"]["resume_token"]


# --- Un run pausado no se puede olvidar ---


def test_un_run_pausado_no_se_purga(cliente: TestClient) -> None:
    """Si se purgara, la aprobación pendiente desaparecería sin que nadie se
    entere y la conversación quedaría trabada."""
    manager: RunManager = cliente.app.state.ctx.runs
    pausado = Run(
        id="run_pausado",
        conversation_id="c-pausada",
        credential_id="cred",
        status="paused",
        finished_at=datetime.now(UTC),
    )
    manager._runs[pausado.id] = pausado

    for indice in range(300):
        terminado = Run(
            id=f"run_{indice}",
            conversation_id=f"c{indice}",
            credential_id="cred",
            status="finished",
            finished_at=datetime.now(UTC),
        )
        manager._runs[terminado.id] = terminado
        manager._prune()

    assert "run_pausado" in manager._runs


def test_un_pausado_vencido_si_se_purga(cliente: TestClient) -> None:
    """Pasado el plazo del resume_token ya nadie puede aprobarlo: ahí sí se olvida."""
    manager: RunManager = cliente.app.state.ctx.runs
    vencido = Run(
        id="run_vencido",
        conversation_id="c-vieja",
        credential_id="cred",
        status="paused",
        finished_at=datetime.now(UTC) - timedelta(seconds=manager._resume_ttl_s + 60),
    )
    assert manager._purgable(vencido) is True


# --- Un mensaje nuevo no pisa una aprobación pendiente ---


def test_mensaje_nuevo_con_aprobacion_pendiente(
    crear_cliente: Callable[..., TestClient],
) -> None:
    cliente = agente_que_pide_aprobacion(crear_cliente)
    conversacion, run_id, _ = pausar(cliente)

    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "mejor olvidalo"},
        headers=AUTH,
    )
    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["code"] == "approval_pending"
    assert run_id in respuesta.json()["error"]["message"]

    # La aprobación sigue en pie y el código sigue sin ejecutarse.
    assert cliente.get(f"/api/v1/runs/{run_id}", headers=AUTH).json()["status"] == "paused"
    assert cliente.sandbox.pedidos == []  # type: ignore[attr-defined]


def test_despues_de_resolver_se_puede_seguir(
    crear_cliente: Callable[..., TestClient],
) -> None:
    cliente = agente_que_pide_aprobacion(crear_cliente)
    conversacion, run_id, token = pausar(cliente)

    cliente.post(
        f"/api/v1/runs/{run_id}/resume",
        json={"resume_token": token, "approve": False},
        headers=AUTH,
    )
    # /resume responde 202 y el run sigue corriendo en segundo plano. Hay que
    # esperar a que termine: si no, el mensaje de abajo llega con el run todavía
    # activo y lo rechaza el 409 de "un run por conversación".
    leer(cliente, f"/api/v1/runs/{run_id}/events")

    # Con la decisión tomada, la conversación sigue normalmente.
    siguiente = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "ahora sí, otra cosa"},
        headers=AUTH,
    )
    assert siguiente.status_code == 202


def test_una_aprobacion_huerfana_se_cierra_sola(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """Si el proceso se reinicia, el run en memoria se pierde pero el hilo queda
    interrumpido. Un mensaje nuevo lo cerraba dejando un tool_call sin su
    ToolMessage: historial inválido para el modelo."""
    cliente = agente_que_pide_aprobacion(crear_cliente)
    conversacion, run_id, _ = pausar(cliente)

    # Simula el reinicio: el registro de runs se vacía, el checkpointer no.
    manager: RunManager = cliente.app.state.ctx.runs
    manager._runs.clear()

    siguiente = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "otra cosa"},
        params={"wait": True},
        headers=AUTH,
    )
    assert siguiente.status_code == 200

    # El sandbox nunca corrió: la aprobación se cerró como rechazo.
    assert cliente.sandbox.pedidos == []  # type: ignore[attr-defined]

    detalle = cliente.get(
        f"/api/v1/conversations/{conversacion}",
        params={"include_tool_messages": True},
        headers=AUTH,
    ).json()
    roles = [m["role"] for m in detalle["messages"]]
    # No quedan dos mensajes del usuario seguidos sin nada en el medio.
    assert not any(roles[i] == "user" and roles[i + 1] == "user" for i in range(len(roles) - 1)), (
        roles
    )


# --- El buffer de eventos ---


def test_los_runs_viejos_sueltan_el_texto_y_avisan(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """Guardar cada token de cada run sería insostenible en memoria. Los viejos
    se compactan, y quien se suscriba después tiene que enterarse."""
    cliente = crear_cliente(turns=[text_turn("una respuesta cualquiera")])
    conversacion = nueva_conversacion(cliente)
    primero = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "hola"},
        headers=AUTH,
    ).json()
    assert any(t == "TEXT_MESSAGE_CONTENT" for t, _ in leer(cliente, primero["events_url"]))

    # Pasan varios runs más: el primero deja de guardar su texto.
    for _ in range(RUNS_CON_TEXTO_COMPLETO + 1):
        otra = nueva_conversacion(cliente)
        cliente.post(
            f"/api/v1/conversations/{otra}/messages",
            json={"content": "hola"},
            params={"wait": True},
            headers=AUTH,
        )

    eventos = leer(cliente, primero["events_url"])
    tipos = [t for t, _ in eventos]
    assert "TEXT_MESSAGE_CONTENT" not in tipos
    aviso = next(d for t, d in eventos if t == "STATE_DELTA" and d.get("replay_incompleto"))
    assert aviso["replay_incompleto"] is True
    # El final sigue estando: de ahí sale el message_id para pedir el texto.
    assert tipos[-1] == "RUN_FINISHED"


def test_un_pedido_rechazado_no_deja_el_mensaje_guardado(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """Si el 409 llega después de persistir, queda un mensaje del usuario que
    nadie va a responder nunca."""
    from tests.fakes import SlowLLM

    cliente = crear_cliente(llm=SlowLLM(delay_s=30))
    conversacion = nueva_conversacion(cliente)
    cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "el primero"},
        headers=AUTH,
    )
    rechazado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "el que se rechaza"},
        headers=AUTH,
    )
    assert rechazado.status_code == 409

    detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    contenidos = [m["content"] for m in detalle["messages"]]
    assert "el que se rechaza" not in contenidos, contenidos
