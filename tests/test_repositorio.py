"""El contrato del repositorio, contra las dos implementaciones.

Los mismos tests corren en memoria y en Postgres. Eso es justamente lo que
encontró el bug de paginación: la versión en memoria pasaba y la de Postgres
no, porque las filas de psycopg son dicts y no objetos.

Postgres se saltea si no hay `BYTE_TEST_DATABASE_URL`. En CI el job lo levanta
como service container.
"""

import os
from collections.abc import AsyncIterator

import pytest

from db.repository import MemoryRepository, PostgresRepository, Repository, title_from_content

DSN = os.environ.get("BYTE_TEST_DATABASE_URL", "")


async def _vaciar(dsn: str) -> None:
    """Cada test arranca con la base limpia."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute("TRUNCATE conversations CASCADE")


@pytest.fixture(params=["memoria", "postgres"])
async def repositorio(request: pytest.FixtureRequest) -> AsyncIterator[Repository]:
    if request.param == "memoria":
        repo: Repository = MemoryRepository()
        await repo.startup()
        yield repo
        await repo.shutdown()
        return

    if not DSN:
        pytest.skip("sin BYTE_TEST_DATABASE_URL: no hay Postgres contra el cual probar")
    repo = PostgresRepository(DSN)
    await repo.startup()
    await _vaciar(DSN)
    yield repo
    await repo.shutdown()


async def test_crear_y_recuperar(repositorio: Repository) -> None:
    creada = await repositorio.create_conversation("Primera")
    traida = await repositorio.get_conversation(creada.id)
    assert traida is not None
    assert traida.id == creada.id
    assert traida.title == "Primera"
    assert traida.summary is None


async def test_mensajes_y_metadata(repositorio: Repository) -> None:
    conversacion = await repositorio.create_conversation("c")
    await repositorio.add_message(conversacion.id, "user", "hola")
    await repositorio.add_message(
        conversacion.id, "tool", "salida interna", metadata={"tool": "web_search"}
    )
    guardado = await repositorio.add_message(
        conversacion.id,
        "assistant",
        "hola!",
        metadata={"sources": [{"url": "https://ejemplo"}], "iterations": 2},
    )

    # La metadata tiene que volver igual (en Postgres pasa por jsonb).
    traido = await repositorio.get_message(guardado.id)
    assert traido is not None
    assert traido.metadata["sources"][0]["url"] == "https://ejemplo"
    assert traido.metadata["iterations"] == 2

    visibles, _ = await repositorio.list_messages(conversacion.id)
    assert [m.role for m in visibles] == ["user", "assistant"]
    completos, _ = await repositorio.list_messages(conversacion.id, include_tool_messages=True)
    assert [m.role for m in completos] == ["user", "tool", "assistant"]


async def test_orden_y_paginacion_de_mensajes(repositorio: Repository) -> None:
    conversacion = await repositorio.create_conversation("c")
    creados = [
        await repositorio.add_message(conversacion.id, "user", f"mensaje {i}") for i in range(5)
    ]
    todos, has_more = await repositorio.list_messages(conversacion.id, limit=50)
    assert [m.content for m in todos] == [f"mensaje {i}" for i in range(5)]
    assert has_more is False

    pagina, has_more = await repositorio.list_messages(conversacion.id, limit=2)
    assert len(pagina) == 2
    assert has_more is True

    anteriores, _ = await repositorio.list_messages(conversacion.id, before=creados[2].id)
    assert [m.content for m in anteriores] == ["mensaje 0", "mensaje 1"]


async def test_el_cursor_recorre_todo_sin_repetir(repositorio: Repository) -> None:
    """El bug que tenía Postgres: la segunda página reventaba."""
    esperadas = {(await repositorio.create_conversation(f"conv {i}")).id for i in range(5)}
    vistas: list[str] = []
    cursor = None
    for _ in range(10):
        pagina, cursor = await repositorio.list_conversations(2, cursor)
        vistas += [c.id for c in pagina]
        if not cursor:
            break
    assert set(vistas) == esperadas
    assert len(vistas) == len(set(vistas))


async def test_el_sidebar_muestra_el_ultimo_mensaje(repositorio: Repository) -> None:
    conversacion = await repositorio.create_conversation("c")
    await repositorio.add_message(conversacion.id, "user", "pregunta")
    await repositorio.add_message(conversacion.id, "assistant", "respuesta final")
    # Los mensajes de herramienta no son preview.
    await repositorio.add_message(conversacion.id, "tool", "ruido interno")

    items, _ = await repositorio.list_conversations(10, None)
    item = next(i for i in items if i.id == conversacion.id)
    assert item.preview == "respuesta final"


async def test_un_mensaje_nuevo_sube_la_conversacion(repositorio: Repository) -> None:
    vieja = await repositorio.create_conversation("vieja")
    nueva = await repositorio.create_conversation("nueva")
    items, _ = await repositorio.list_conversations(10, None)
    assert [i.id for i in items][:2] == [nueva.id, vieja.id]

    await repositorio.add_message(vieja.id, "user", "revivida")
    items, _ = await repositorio.list_conversations(10, None)
    assert items[0].id == vieja.id


async def test_renombrar(repositorio: Repository) -> None:
    conversacion = await repositorio.create_conversation("original")
    renombrada = await repositorio.set_title(conversacion.id, "nueva")
    assert renombrada is not None
    assert renombrada.title == "nueva"
    assert await repositorio.set_title("00000000-0000-0000-0000-000000000000", "x") is None


async def test_borrar_arrastra_los_mensajes(repositorio: Repository) -> None:
    conversacion = await repositorio.create_conversation("c")
    await repositorio.add_message(conversacion.id, "user", "hola")
    assert await repositorio.delete_conversation(conversacion.id) is True
    assert await repositorio.delete_conversation(conversacion.id) is False
    assert await repositorio.get_conversation(conversacion.id) is None
    restantes, _ = await repositorio.list_messages(conversacion.id, include_tool_messages=True)
    assert restantes == []


async def test_un_id_invalido_no_explota(repositorio: Repository) -> None:
    """La API recibe ids de afuera: un uuid inválido es 404, no un 500."""
    assert await repositorio.get_conversation("no-es-un-uuid") is None
    assert await repositorio.get_message("tampoco") is None
    assert await repositorio.delete_conversation("nada") is False
    assert await repositorio.history("nada") == []


async def test_titulo_desde_el_primer_mensaje() -> None:
    assert title_from_content("  hola   mundo  ") == "hola mundo"
    largo = title_from_content("x" * 100)
    assert len(largo) == 60
    assert largo.endswith("...")


# --- Checkpointer de LangGraph sobre Postgres ---


@pytest.mark.skipif(not DSN, reason="sin BYTE_TEST_DATABASE_URL")
async def test_el_hilo_del_agente_sobrevive_al_reinicio() -> None:
    """Lo que el plan pide del PostgresSaver: que la conversación retome donde
    quedó, incluso después de reiniciar el proceso."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from agent.graph import build_graph
    from tests.fakes import FakeLLM, text_turn
    from tools.base import ToolRegistry

    hilo = "22222222-2222-2222-2222-222222222222"
    config = {"configurable": {"thread_id": hilo}}
    entrada = {"iterations": 0, "sources": None, "tools_used": None}

    async with AsyncPostgresSaver.from_conn_string(DSN) as saver:
        await saver.setup()
        await saver.adelete_thread(hilo)  # por si quedó de una corrida anterior
        grafo = build_graph(FakeLLM([text_turn("Hola Elvis.")]), ToolRegistry(), checkpointer=saver)
        await grafo.ainvoke(
            {
                "messages": [SystemMessage(content="sos byte"), HumanMessage(content="soy Elvis")],
                **entrada,
            },
            config=config,
        )

    # Otro saver = otro proceso: el hilo tiene que seguir ahí.
    async with AsyncPostgresSaver.from_conn_string(DSN) as saver:
        grafo = build_graph(
            FakeLLM([text_turn("Te llamás Elvis.")]), ToolRegistry(), checkpointer=saver
        )
        final = await grafo.ainvoke(
            {"messages": [HumanMessage(content="cómo me llamo?")], **entrada}, config=config
        )
        assert [m.type for m in final["messages"]] == ["system", "human", "ai", "human", "ai"]
        # Los acumuladores son por run, el hilo no.
        assert final["sources"] == []
        assert final["tools_used"] == []

        # Y borrar la conversación se lleva el hilo (lo hace DELETE /conversations).
        await saver.adelete_thread(hilo)
        estado = await grafo.aget_state(config)
        assert not estado.values.get("messages")


@pytest.mark.skipif(not DSN, reason="sin BYTE_TEST_DATABASE_URL")
def test_la_api_entera_sobre_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cubre el cableado real: build_repository + checkpointer de Postgres,
    que hasta ahora solo se verificaba a mano."""
    from fastapi.testclient import TestClient

    from api.config import get_settings
    from api.main import create_app
    from tests.conftest import API_KEY, AUTH
    from tests.fakes import FakeLLM, text_turn

    monkeypatch.setenv("BYTE_STORAGE", "postgres")
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    get_settings.cache_clear()

    app = create_app(llm=FakeLLM([text_turn("respuesta guardada en Postgres")]))
    with TestClient(app) as cliente:
        assert cliente.get("/api/v1/health").json() == {"status": "ok"}
        assert cliente.get("/api/v1/health/details", headers=AUTH).json()["db"] == "ok"

        conversacion = cliente.post("/api/v1/conversations", json={}, headers=AUTH).json()["id"]
        respuesta = cliente.post(
            f"/api/v1/conversations/{conversacion}/messages",
            json={"content": "hola"},
            params={"wait": True},
            headers=AUTH,
        )
        assert respuesta.status_code == 200
        assert respuesta.json()["message"]["content"] == "respuesta guardada en Postgres"

        # Sobrevive a releer desde la base.
        detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
        assert [m["role"] for m in detalle["messages"]] == ["user", "assistant"]
        assert detalle["title"] == "hola"

        assert (
            cliente.delete(f"/api/v1/conversations/{conversacion}", headers=AUTH).status_code == 204
        )
        assert cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).status_code == 404

    get_settings.cache_clear()
