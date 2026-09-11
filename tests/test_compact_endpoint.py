"""POST /conversations/{id}/compact.

Lo que importa: que compacte de verdad. Una versión anterior resumía el
historial de MESSAGES sin tocar el hilo del agente, así que escribía un resumen
y el run siguiente mandaba todo el historial **más** el resumen: más contexto,
no menos.
"""

from collections.abc import Callable

from starlette.testclient import TestClient

from tests.fakes import text_turn

AUTH = {"X-API-Key": "clave-de-prueba"}


def _conversacion_con_historial(cliente: TestClient, turnos: int = 4) -> str:
    conversacion = cliente.post("/api/v1/conversations", json={}, headers=AUTH).json()["id"]
    for i in range(turnos):
        cliente.post(
            f"/api/v1/conversations/{conversacion}/messages",
            json={"content": f"mensaje numero {i}"},
            params={"wait": True},
            headers=AUTH,
        )
    return conversacion


def _mensajes_del_hilo(cliente: TestClient, conversacion: str) -> list[object]:
    estado = cliente.app.state.ctx.runs._graph.get_state(  # type: ignore[attr-defined]
        {"configurable": {"thread_id": conversacion}}
    )
    return list((estado.values or {}).get("messages") or []) if estado else []


def test_compactar_saca_los_mensajes_viejos_del_hilo(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """El resumen sin sacar los mensajes no libera nada: el run siguiente
    mandaría el historial entero y encima el resumen."""
    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = _conversacion_con_historial(cliente)

    antes = len(_mensajes_del_hilo(cliente, conversacion))
    respuesta = cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["compacted_messages"] > 0
    assert cuerpo["summary"]

    despues = len(_mensajes_del_hilo(cliente, conversacion))
    assert despues < antes, "los mensajes viejos tienen que salir del hilo"
    assert despues == antes - cuerpo["compacted_messages"]


def test_el_resumen_queda_en_la_conversacion(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = _conversacion_con_historial(cliente)
    cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)

    detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    assert detalle["summary"]


def test_los_mensajes_originales_no_se_borran(crear_cliente: Callable[..., TestClient]) -> None:
    """Compactar es para el modelo: la UI tiene que seguir mostrando todo."""
    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = _conversacion_con_historial(cliente)
    antes = len(
        cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()["messages"]
    )

    cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)

    despues = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()["messages"]
    assert len(despues) == antes


def test_sin_historial_viejo_no_hay_nada_que_compactar(
    crear_cliente: Callable[..., TestClient],
) -> None:
    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = cliente.post("/api/v1/conversations", json={}, headers=AUTH).json()["id"]

    respuesta = cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "nada_para_compactar"


def test_compactar_dos_veces_no_repite_lo_ya_resumido(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """La segunda pasada solo ve lo que quedó: si volviera a leer todo el
    historial, duplicaría los mismos hechos en el resumen."""
    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = _conversacion_con_historial(cliente, turnos=6)

    primera = cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH).json()
    quedaron = len(_mensajes_del_hilo(cliente, conversacion))

    segunda = cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)
    # Sin mensajes nuevos y con pocos en el hilo, no hay nada viejo que sacar.
    assert segunda.status_code == 422
    assert len(_mensajes_del_hilo(cliente, conversacion)) == quedaron
    assert primera["compacted_messages"] > 0


def test_conversacion_inexistente(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(turns=[text_turn("ok")])
    respuesta = cliente.post(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000/compact", headers=AUTH
    )
    assert respuesta.status_code == 404


def test_sin_credencial(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = _conversacion_con_historial(cliente, turnos=2)
    assert cliente.post(f"/api/v1/conversations/{conversacion}/compact").status_code == 401


def test_el_system_prompt_sobrevive_a_la_compactacion(
    crear_cliente: Callable[..., TestClient],
) -> None:
    """El system prompt vive al principio del hilo y no se reinyecta: sacarlo
    una vez lo pierde para siempre, con las reglas anti-inyección adentro."""
    from langchain_core.messages import SystemMessage

    cliente = crear_cliente(turns=[text_turn("ok")])
    conversacion = _conversacion_con_historial(cliente, turnos=5)

    antes = _mensajes_del_hilo(cliente, conversacion)
    assert isinstance(antes[0], SystemMessage), "el hilo tiene que arrancar con el system prompt"

    cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)

    despues = _mensajes_del_hilo(cliente, conversacion)
    assert any(isinstance(m, SystemMessage) for m in despues), (
        "la compactación borró el system prompt: la conversación pierde sus reglas"
    )


def test_no_deja_un_tool_message_huerfano(crear_cliente: Callable[..., TestClient]) -> None:
    """Un ToolMessage sin el AIMessage que lo pidió rompe el pareo de
    tool_call_id y Ollama rechaza la conversación entera."""
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    from tests.fakes import tool_turn

    cliente = crear_cliente(
        turns=[tool_turn("code_exec", json.dumps({"code": "print(1)"})), text_turn("listo")],
        con_sandbox=True,
        con_busqueda=False,
    )
    conversacion = _conversacion_con_historial(cliente, turnos=4)

    cliente.post(f"/api/v1/conversations/{conversacion}/compact", headers=AUTH)

    mensajes = _mensajes_del_hilo(cliente, conversacion)
    pedidos = {
        llamada["id"]
        for m in mensajes
        if isinstance(m, AIMessage)
        for llamada in (m.tool_calls or [])
    }
    respondidos = {m.tool_call_id for m in mensajes if isinstance(m, ToolMessage)}
    assert respondidos <= pedidos, f"ToolMessage sin su AIMessage: {respondidos - pedidos}"
