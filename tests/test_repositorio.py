"""El contrato del repositorio, contra las dos implementaciones.

Los mismos tests corren en memoria y en Postgres. Eso es justamente lo que
encontró el bug de paginación: la versión en memoria pasaba y la de Postgres
no, porque las filas de psycopg son dicts y no objetos.

Postgres se saltea si no hay `BYTE_TEST_DATABASE_URL`. En CI el job lo levanta
como service container.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from db.repository import (
    PREFIJO_OPENAI,
    SIN_USUARIO,
    MemoryRepository,
    PostgresRepository,
    Repository,
    title_from_content,
)

DSN = os.environ.get("BYTE_TEST_DATABASE_URL", "")

# Dos dueños de prueba. Son uuid porque `conversations.user_id` es
# `uuid REFERENCES users (id)`: Postgres rechaza cualquier otra cosa.
FAMILIA = "33333333-3333-4333-8333-333333333333"
ANA = "11111111-1111-4111-8111-111111111111"
BETO = "22222222-2222-4222-8222-222222222222"


async def _vaciar(dsn: str) -> None:
    """Cada test arranca con la base limpia y con los usuarios de prueba.

    `conversations.user_id` tiene FK a `users`, así que los tests de aislamiento
    necesitan que esos ids existan. En memoria no hace falta: no hay integridad
    referencial que respetar.
    """
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute("TRUNCATE conversations, users, refresh_tokens CASCADE")
        for uid, email in ((ANA, "ana@ejemplo.test"), (BETO, "beto@ejemplo.test")):
            await conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'x')",
                (uid, email),
            )


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


@pytest.mark.skipif(not DSN, reason="sin BYTE_TEST_DATABASE_URL")
async def test_las_migraciones_se_aplican_una_sola_vez() -> None:
    """Antes se re-ejecutaban todas en cada arranque y funcionaba de casualidad,
    porque son IF NOT EXISTS. Con un ALTER eso se rompe."""
    import psycopg

    from db.repository import MIGRATIONS_DIR

    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        # Todo lo que crean las migraciones. Si queda una tabla viva, su
        # `CREATE TABLE IF NOT EXISTS` no vuelve a correr y se pierde lo que
        # el DROP CASCADE se llevó de ella —por ejemplo, una FK hacia `users`—,
        # así que las corridas siguientes arrancan con un esquema incompleto.
        await conn.execute(
            "DROP TABLE IF EXISTS schema_migrations, messages, conversations, "
            "document_chunks, documents, refresh_tokens, users CASCADE"
        )

    esperadas = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))

    primero = PostgresRepository(DSN)
    await primero.startup()
    await primero.shutdown()
    segundo = PostgresRepository(DSN)
    await segundo.startup()
    await segundo.shutdown()

    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        cur = await conn.execute("SELECT archivo FROM schema_migrations ORDER BY archivo")
        registradas = [fila[0] for fila in await cur.fetchall()]

    # Una fila por migración, aunque se haya arrancado dos veces.
    assert registradas == esperadas


# --- Aislamiento por dueño (preparación de la Fase 4) ---
#
# Hoy todos los call sites pasan `SIN_USUARIO`, así que el filtro no discrimina
# y el comportamiento no cambia. Estos pasan un dueño de verdad para probar que
# el filtro *funciona* cuando el JWT lo traiga: sin esto el parámetro sería
# plomería sin garantía. Van por la fixture para correr también contra Postgres,
# que es donde el `IS NOT DISTINCT FROM` de verdad se ejercita.
#
# El dueño tiene que ser un uuid: `conversations.user_id` es `uuid REFERENCES
# users (id)`, y Postgres rechaza cualquier otra cosa.


async def test_una_conversacion_de_otro_no_se_ve(repositorio: Repository) -> None:
    ajena = await repositorio.create_conversation("de ana", user_id=ANA)

    assert await repositorio.get_conversation(ajena.id, user_id=BETO) is None
    assert await repositorio.get_conversation(ajena.id, user_id=ANA) is not None


async def test_el_listado_solo_trae_lo_propio(repositorio: Repository) -> None:
    await repositorio.create_conversation("de ana", user_id=ANA)
    await repositorio.create_conversation("de beto", user_id=BETO)

    de_ana, _ = await repositorio.list_conversations(10, None, user_id=ANA)
    assert [c.title for c in de_ana] == ["de ana"]


async def test_no_se_puede_renombrar_la_conversacion_de_otro(repositorio: Repository) -> None:
    ajena = await repositorio.create_conversation("de ana", user_id=ANA)

    assert await repositorio.set_title(ajena.id, "secuestrada", user_id=BETO) is None
    intacta = await repositorio.get_conversation(ajena.id, user_id=ANA)
    assert intacta is not None
    assert intacta.title == "de ana"


async def test_no_se_puede_borrar_la_conversacion_de_otro(repositorio: Repository) -> None:
    ajena = await repositorio.create_conversation("de ana", user_id=ANA)

    assert await repositorio.delete_conversation(ajena.id, user_id=BETO) is False
    assert await repositorio.get_conversation(ajena.id, user_id=ANA) is not None


async def test_un_mensaje_de_otro_no_se_lee_por_su_id(repositorio: Repository) -> None:
    """`GET /messages/{id}` toma un id de recurso suelto: es el IDOR más directo
    de la API si el filtro no está."""
    ajena = await repositorio.create_conversation("de ana", user_id=ANA)
    mensaje = await repositorio.add_message(ajena.id, "user", "algo privado")

    assert await repositorio.get_message(mensaje.id, user_id=BETO) is None
    assert await repositorio.get_message(mensaje.id, user_id=ANA) is not None


async def test_sin_usuario_no_ve_lo_de_un_usuario_real(repositorio: Repository) -> None:
    """El comportamiento de hoy: con `SIN_USUARIO` a los dos lados el filtro no
    discrimina, que es lo que mantiene la API andando hasta el JWT. Pero no
    mezcla con lo de un usuario de verdad."""
    propia = await repositorio.create_conversation("del MVP", user_id=SIN_USUARIO)
    await repositorio.create_conversation("de ana", user_id=ANA)

    assert await repositorio.get_conversation(propia.id, SIN_USUARIO) is not None
    items, _ = await repositorio.list_conversations(10, None, SIN_USUARIO)
    assert [c.title for c in items] == ["del MVP"]


# --- Usuarios (Fase 4) ---


async def test_crear_y_buscar_un_usuario(repositorio: Repository) -> None:
    creado = await repositorio.create_user("ana@ejemplo.com", "hash-falso")
    assert creado is not None

    encontrado = await repositorio.get_user_by_email("ana@ejemplo.com")
    assert encontrado is not None
    usuario, password_hash = encontrado
    assert usuario.id == creado.id
    assert password_hash == "hash-falso"
    assert (await repositorio.get_user(creado.id)).email == "ana@ejemplo.com"


async def test_el_email_es_unico(repositorio: Repository) -> None:
    """Lo decide el UNIQUE de la tabla, no un SELECT previo: entre consultar y
    escribir pueden entrar dos registros con el mismo email."""
    assert await repositorio.create_user("ana@ejemplo.com", "h1") is not None
    assert await repositorio.create_user("ana@ejemplo.com", "h2") is None


async def test_el_email_se_normaliza(repositorio: Repository) -> None:
    """`Ana@X.com` y `ana@x.com` son la misma persona."""
    await repositorio.create_user("  ANA@Ejemplo.com  ", "h")
    assert await repositorio.get_user_by_email("ana@ejemplo.com") is not None
    assert await repositorio.create_user("ana@ejemplo.com", "h2") is None


async def test_un_usuario_que_no_existe_no_rompe(repositorio: Repository) -> None:
    assert await repositorio.get_user_by_email("nadie@ejemplo.com") is None
    assert await repositorio.get_user("no-es-un-uuid") is None
    assert await repositorio.get_user("99999999-9999-4999-8999-999999999999") is None


async def test_se_puede_migrar_el_hash(repositorio: Repository) -> None:
    """El login rehashea cuando los parámetros de argon2 quedaron viejos."""
    creado = await repositorio.create_user("ana@ejemplo.com", "hash-viejo")
    assert creado is not None

    assert await repositorio.set_password_hash(creado.id, "hash-nuevo") is True
    _, guardado = await repositorio.get_user_by_email("ana@ejemplo.com")
    assert guardado == "hash-nuevo"


async def test_borrar_un_usuario_arrastra_sus_conversaciones(repositorio: Repository) -> None:
    """La FK es `ON DELETE CASCADE`: no pueden quedar conversaciones sin dueño
    apuntando a un usuario que ya no existe."""
    if not isinstance(repositorio, PostgresRepository):
        pytest.skip("la implementación en memoria no tiene integridad referencial")

    creado = await repositorio.create_user("ana@ejemplo.com", "h")
    assert creado is not None
    conversacion = await repositorio.create_conversation("suya", user_id=creado.id)

    async with repositorio.pool.connection() as conn:
        await conn.execute("DELETE FROM users WHERE id = %s", (creado.id,))

    assert await repositorio.get_conversation(conversacion.id, user_id=creado.id) is None


# --- Refresh tokens ---


async def _un_usuario(repositorio: Repository) -> str:
    creado = await repositorio.create_user("ana@ejemplo.com", "h")
    assert creado is not None
    return creado.id


async def test_un_refresh_se_canjea_una_sola_vez(repositorio: Repository) -> None:
    user_id = await _un_usuario(repositorio)
    vence = datetime.now(UTC) + timedelta(days=1)
    await repositorio.save_refresh_token(user_id, "hash-1", FAMILIA, vence)

    primero = await repositorio.use_refresh_token("hash-1")
    assert primero.user_id == user_id
    assert primero.reusado is False

    segundo = await repositorio.use_refresh_token("hash-1")
    assert segundo.user_id is None
    assert segundo.reusado is True, "un token ya canjeado tiene que delatarse"


async def test_un_refresh_vencido_no_se_canjea(repositorio: Repository) -> None:
    user_id = await _un_usuario(repositorio)
    await repositorio.save_refresh_token(
        user_id, "hash-viejo", FAMILIA, datetime.now(UTC) - timedelta(seconds=1)
    )
    resultado = await repositorio.use_refresh_token("hash-viejo")
    assert resultado.user_id is None
    assert resultado.reusado is False


async def test_revocar_la_familia_corta_todos_sus_tokens(repositorio: Repository) -> None:
    user_id = await _un_usuario(repositorio)
    vence = datetime.now(UTC) + timedelta(days=1)
    await repositorio.save_refresh_token(user_id, "hash-a", FAMILIA, vence)
    await repositorio.save_refresh_token(user_id, "hash-b", FAMILIA, vence)

    assert await repositorio.revoke_refresh_family(FAMILIA) == 2
    assert (await repositorio.use_refresh_token("hash-b")).user_id is None


async def test_un_token_que_no_existe_no_es_un_reuso(repositorio: Repository) -> None:
    """Distinguir los dos casos es lo que hace útil la detección: si todo
    pareciera reuso, cualquier token inventado revocaría una sesión."""
    resultado = await repositorio.use_refresh_token("nunca-existio")
    assert resultado.user_id is None
    assert resultado.reusado is False


async def test_la_familia_se_puede_consultar_sin_canjear(repositorio: Repository) -> None:
    """Lo usa el logout: marcar el token como usado haría que el siguiente
    intento pareciera un reuso."""
    user_id = await _un_usuario(repositorio)
    await repositorio.save_refresh_token(
        user_id, "hash-1", FAMILIA, datetime.now(UTC) + timedelta(days=1)
    )

    assert await repositorio.family_of_refresh_token("hash-1") == FAMILIA
    # Y sigue canjeable, porque consultar no consume.
    assert (await repositorio.use_refresh_token("hash-1")).user_id == user_id


async def test_borrar_el_usuario_arrastra_sus_refresh(repositorio: Repository) -> None:
    """`ON DELETE CASCADE`: no pueden quedar tokens vivos de un usuario que ya
    no existe."""
    if not isinstance(repositorio, PostgresRepository):
        pytest.skip("la implementación en memoria no tiene integridad referencial")

    user_id = await _un_usuario(repositorio)
    await repositorio.save_refresh_token(
        user_id, "hash-1", FAMILIA, datetime.now(UTC) + timedelta(days=1)
    )
    async with repositorio.pool.connection() as conn:
        await conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    assert (await repositorio.use_refresh_token("hash-1")).user_id is None


# --- Retención de las conversaciones de /v1 ---


async def _envejecer(repositorio: Repository, conversation_id: str, dias: int) -> None:
    """Mueve `updated_at` al pasado, para no esperar 30 días en un test."""
    viejo = datetime.now(UTC) - timedelta(days=dias)
    if isinstance(repositorio, PostgresRepository):
        async with repositorio.pool.connection() as conn:
            await conn.execute(
                "UPDATE conversations SET updated_at = %s WHERE id = %s", (viejo, conversation_id)
            )
        return
    actual = repositorio._conversations[conversation_id]
    repositorio._conversations[conversation_id] = actual.model_copy(update={"updated_at": viejo})


async def test_las_conversaciones_viejas_de_openai_se_purgan(repositorio: Repository) -> None:
    """Cada pedido a `/v1` crea una y el cliente no tiene dónde guardar su id:
    sin purga, con el canal de email de n8n activo la base crece sin techo."""
    vieja = await repositorio.create_conversation(f"{PREFIJO_OPENAI}hace mucho")
    await _envejecer(repositorio, vieja.id, 40)

    assert await repositorio.purgar_conversaciones_openai(30) == 1
    assert await repositorio.get_conversation(vieja.id) is None


async def test_una_conversacion_reciente_de_openai_no_se_toca(repositorio: Repository) -> None:
    reciente = await repositorio.create_conversation(f"{PREFIJO_OPENAI}de ayer")
    await _envejecer(repositorio, reciente.id, 2)

    assert await repositorio.purgar_conversaciones_openai(30) == 0
    assert await repositorio.get_conversation(reciente.id) is not None


async def test_la_purga_no_toca_las_conversaciones_del_chat(repositorio: Repository) -> None:
    """Una conversación del chat la abrió alguien a propósito: borrarla sola
    sería perder trabajo. Las de `/v1` las crea una llamada de API que ya se
    llevó su respuesta."""
    propia = await repositorio.create_conversation("la que abrí yo")
    await _envejecer(repositorio, propia.id, 400)

    assert await repositorio.purgar_conversaciones_openai(30) == 0
    assert await repositorio.get_conversation(propia.id) is not None


async def test_purgar_arrastra_los_mensajes(repositorio: Repository) -> None:
    """Si los mensajes quedaran, la purga liberaría las filas que menos pesan."""
    vieja = await repositorio.create_conversation(f"{PREFIJO_OPENAI}con mensajes")
    mensaje = await repositorio.add_message(vieja.id, "user", "algo")
    await _envejecer(repositorio, vieja.id, 40)

    await repositorio.purgar_conversaciones_openai(30)
    assert await repositorio.get_message(mensaje.id) is None
