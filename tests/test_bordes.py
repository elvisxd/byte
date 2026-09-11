"""Casos borde y garantías que son fáciles de romper sin darse cuenta."""

import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.graph import build_graph
from agent.prompts import SYSTEM_PROMPT
from api.config import Settings
from api.security import SESSION_COOKIE
from models.schemas import HARD_MAX_MESSAGE_CHARS
from tests.conftest import API_KEY, AUTH, nueva_conversacion
from tests.fakes import FakeLLM, SlowLLM, text_turn
from tools.base import ToolRegistry


def _crear_run(client: TestClient, conversacion: str, contenido: str = "hola") -> dict:
    respuesta = client.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": contenido},
        headers=AUTH,
    )
    assert respuesta.status_code == 202, respuesta.text
    return respuesta.json()


def _leer_eventos(client: TestClient, url: str, **params: object) -> list[tuple[str, dict]]:
    eventos: list[tuple[str, dict]] = []
    with client.stream("GET", url, params=params, headers=AUTH) as respuesta:
        tipo = None
        for linea in respuesta.iter_lines():
            if linea.startswith("event: "):
                tipo = linea.removeprefix("event: ")
            elif linea.startswith("data: ") and tipo:
                eventos.append((tipo, json.loads(linea.removeprefix("data: "))))
    return eventos


class _Grabador:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []

    def emit(self, tipo: str, data: dict) -> None:
        self.eventos.append((tipo, data))


# --- Configuración y límites ---


def test_el_tope_de_mensaje_no_se_puede_subir() -> None:
    """BYTE_MAX_MESSAGE_CHARS puede bajar el tope del contrato, nunca subirlo."""
    assert Settings(_env_file=None, BYTE_MAX_MESSAGE_CHARS=10**6).max_message_chars == (
        HARD_MAX_MESSAGE_CHARS
    )
    assert Settings(_env_file=None, BYTE_MAX_MESSAGE_CHARS=500).max_message_chars == 500
    # Un tope de 0 dejaría la API inutilizable.
    assert Settings(_env_file=None, BYTE_MAX_MESSAGE_CHARS=0).max_message_chars == 1


def test_produccion_no_arranca_en_memoria() -> None:
    """El plan es explícito: el checkpointer nunca va in-memory en producción."""
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            BYTE_ENV="prod",
            BYTE_API_KEY="k",
            BYTE_SECRET_KEY="s" * 32,
            BYTE_STORAGE="memory",
        )
    # Con Postgres configurado, arranca. BYTE_STORAGE va explícito porque el
    # fixture del entorno lo fuerza a "memory" para el resto de los tests.
    assert Settings(
        _env_file=None,
        BYTE_ENV="prod",
        BYTE_API_KEY="k",
        BYTE_SECRET_KEY="s" * 32,
        BYTE_STORAGE="auto",
        DATABASE_URL="postgresql://byte:byte@localhost:5432/byte",
    ).use_postgres


# --- Recorte de historial ---


async def test_el_recorte_conserva_el_system_prompt_y_lo_ultimo() -> None:
    """`retrieve_context` descarta por identidad de objeto: si `trim_messages`
    devolviera copias, borraría los mensajes equivocados. Esto lo detecta."""
    grafo = build_graph(FakeLLM([text_turn("ok")]), ToolRegistry(), num_ctx=1024)
    grabador = _Grabador()
    estado = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content="pregunta vieja " * 200),
            AIMessage(content="respuesta vieja " * 200),
            HumanMessage(content="LA PREGUNTA NUEVA"),
        ],
        "iterations": 0,
        "sources": [],
        "tools_used": [],
    }
    final = await grafo.ainvoke(estado, config={"configurable": {"emitter": grabador}})
    texto = " ".join(str(m.content) for m in final["messages"])

    assert "REGLA DE SEGURIDAD" in texto, "se perdió el system prompt"
    assert "LA PREGUNTA NUEVA" in texto, "se perdió la pregunta del usuario"
    assert "pregunta vieja" not in texto, "no recortó nada"


# --- Borrado de conversación con run en vuelo ---


def test_borrar_conversacion_corta_el_run(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(llm=SlowLLM(delay_s=30))
    conversacion = nueva_conversacion(cliente)
    aceptado = _crear_run(cliente, conversacion)

    assert cliente.delete(f"/api/v1/conversations/{conversacion}", headers=AUTH).status_code == 204

    estado = cliente.get(f"/api/v1/runs/{aceptado['run_id']}", headers=AUTH).json()
    assert estado["status"] == "cancelled", "el run quedó vivo contra una conversación borrada"
    eventos = _leer_eventos(cliente, aceptado["events_url"])
    assert eventos[-1][1]["status"] == "cancelled"


# --- Runs: casos borde ---


def test_cancelar_un_run_ya_terminado_no_lo_cambia(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = _crear_run(cliente, conversacion)
    _leer_eventos(cliente, aceptado["events_url"])

    assert (
        cliente.post(f"/api/v1/runs/{aceptado['run_id']}/cancel", headers=AUTH).status_code == 202
    )
    estado = cliente.get(f"/api/v1/runs/{aceptado['run_id']}", headers=AUTH).json()
    assert estado["status"] == "finished"


def test_reconexion_fuera_de_rango_no_repite_nada(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = _crear_run(cliente, conversacion)
    _leer_eventos(cliente, aceptado["events_url"])
    assert _leer_eventos(cliente, aceptado["events_url"], last_event_id=9999) == []


def test_last_event_id_invalido_manda_todo(cliente: TestClient) -> None:
    """Un Last-Event-ID que no es número no debe romper el stream."""
    conversacion = nueva_conversacion(cliente)
    aceptado = _crear_run(cliente, conversacion)
    eventos = _leer_eventos(cliente, aceptado["events_url"], last_event_id="basura")
    assert eventos[0][0] == "RUN_STARTED"


def test_un_solo_run_por_conversacion(crear_cliente: Callable[..., TestClient]) -> None:
    """Dos runs a la vez en la misma conversación compartirían el hilo del
    checkpointer y se pisarían el estado: el segundo recibe 409."""
    cliente = crear_cliente(llm=SlowLLM(delay_s=30), BYTE_MAX_CONCURRENT_RUNS=2)
    conversacion = nueva_conversacion(cliente)
    _crear_run(cliente, conversacion, "pregunta A")

    segundo = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "pregunta B"},
        headers=AUTH,
    )
    assert segundo.status_code == 409
    assert segundo.json()["error"]["code"] == "conversation_busy"


def test_dos_conversaciones_en_paralelo(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(llm=SlowLLM(delay_s=0.2), BYTE_MAX_CONCURRENT_RUNS=2)
    primera, segunda = nueva_conversacion(cliente), nueva_conversacion(cliente)
    una, otra = _crear_run(cliente, primera), _crear_run(cliente, segunda)
    for aceptado in (una, otra):
        eventos = _leer_eventos(cliente, aceptado["events_url"])
        assert eventos[-1][1]["status"] == "finished"


def test_mensaje_de_solo_espacios(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "   \n\t "},
        headers=AUTH,
    )
    assert respuesta.status_code == 422


def test_cookie_de_sesion_manipulada(cliente: TestClient) -> None:
    cliente.post("/api/v1/session", json={"api_key": API_KEY})
    cookie = cliente.cookies.get(SESSION_COOKIE)
    assert cookie

    # La cookie manipulada va como header, no por el jar de httpx: un `set()`
    # deja DOS cookies con el mismo nombre (la original con dominio
    # "testserver.local" y la nueva con dominio vacío), las manda a las dos y
    # cuál gana depende del orden. Así el test sería no determinista.
    cliente.cookies.clear()
    # Se toca el payload, no el final de la firma. La firma va en base64url y
    # sus últimos bits son de relleno: cambiar el último carácter da, una de
    # cada tres veces, otra cadena que decodifica a los mismos bytes y sigue
    # validando. El test fallaba de forma intermitente por eso.
    payload, punto, firma = cookie.partition(".")
    assert punto, "la cookie de sesión debería venir firmada"
    primero = payload[0]
    manipulada = ("A" if primero != "A" else "B") + payload[1:] + punto + firma

    respuesta = cliente.get(
        "/api/v1/conversations", headers={"Cookie": f"{SESSION_COOKIE}={manipulada}"}
    )
    assert respuesta.status_code == 401


# --- Paginación ---


def test_mensajes_paginan_hacia_atras(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente(llm=FakeLLM([text_turn("r1"), text_turn("r2"), text_turn("r3")]))
    conversacion = nueva_conversacion(cliente)
    for texto in ("uno", "dos", "tres"):
        cliente.post(
            f"/api/v1/conversations/{conversacion}/messages",
            json={"content": texto},
            params={"wait": True},
            headers=AUTH,
        )
    todos = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()["messages"]
    assert [m["content"] for m in todos] == ["uno", "r1", "dos", "r2", "tres", "r3"]

    anteriores = cliente.get(
        f"/api/v1/conversations/{conversacion}",
        params={"before": todos[2]["id"]},
        headers=AUTH,
    ).json()
    assert [m["content"] for m in anteriores["messages"]] == ["uno", "r1"]

    pagina = cliente.get(
        f"/api/v1/conversations/{conversacion}", params={"limit": 2}, headers=AUTH
    ).json()
    assert len(pagina["messages"]) == 2
    assert pagina["has_more"] is True


def test_el_cursor_no_duplica_ni_saltea(cliente: TestClient) -> None:
    """Varias conversaciones creadas en el mismo instante: el cursor desempata por id."""
    esperados = {nueva_conversacion(cliente) for _ in range(5)}
    vistos: list[str] = []
    cursor = None
    for _ in range(10):
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        pagina = cliente.get("/api/v1/conversations", params=params, headers=AUTH).json()
        vistos += [item["id"] for item in pagina["items"]]
        cursor = pagina["next_cursor"]
        if not cursor:
            break
    assert set(vistos) == esperados
    assert len(vistos) == len(set(vistos))


# --- Contrato publicado ---


def test_el_spec_declara_los_modelos_reales(cliente: TestClient) -> None:
    """El CLI en Go genera su cliente desde el spec: no puede quedar sin tipos."""
    spec = cliente.get("/openapi.json").json()
    respuestas = spec["paths"]["/api/v1/conversations/{conversation_id}/messages"]["post"][
        "responses"
    ]

    def referencia(codigo: str) -> str:
        return respuestas[codigo]["content"]["application/json"]["schema"]["$ref"]

    assert referencia("202").endswith("/RunAccepted")
    assert referencia("200").endswith("/MessageResult")
    # El 422 sale con el envoltorio del contrato, no con el de FastAPI.
    assert referencia("422").endswith("/ErrorEnvelope")
