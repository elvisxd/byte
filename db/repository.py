"""Persistencia de conversaciones y mensajes.

Dos implementaciones con la misma interfaz:
- `MemoryRepository`: por defecto en desarrollo y en los tests. No sobrevive al reinicio.
- `PostgresRepository`: la real, según db/migrations/001_mvp.sql.

Se elige con BYTE_STORAGE (auto/memory/postgres); en `auto` alcanza con definir
DATABASE_URL para usar Postgres.
"""

import asyncio
import base64
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from api.logging import get_logger
from models.schemas import Conversation, ConversationListItem, Message, MessageRole

logger = get_logger("db")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# El dueño de una conversación. `conversations.user_id` existe desde el MVP
# (001_mvp.sql) pero nadie la escribía ni la leía: las consultas ya filtran, y
# mientras el valor sea siempre el mismo el filtro no discrimina. Cuando el JWT
# traiga el usuario real, reemplazar los usos de esta constante por el del
# request — buscarla da los puntos exactos, que es para lo que existe.
#
# `messages` no lleva su propia columna: cuelga de `conversations` por FK, así
# que filtrar por la conversación ya decide quién puede ver sus mensajes.
SIN_USUARIO: str | None = None
# Id arbitrario pero fijo para el advisory lock de las migraciones.
MIGRATION_LOCK_ID = 8_675_309
PREVIEW_CHARS = 120


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


def _encode_cursor(updated_at: datetime, conversation_id: str) -> str:
    raw = f"{updated_at.isoformat()}|{conversation_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        stamp, _, conversation_id = raw.partition("|")
        return datetime.fromisoformat(stamp), conversation_id
    except (ValueError, UnicodeDecodeError):
        return None


def title_from_content(content: str) -> str:
    """Título truncando el primer mensaje, sin llamar al LLM (contrato)."""
    flat = " ".join(content.split())
    return flat[:60] if len(flat) <= 60 else flat[:57] + "..."


class Repository(Protocol):
    """Interfaz que consume la API. Implementada en memoria y en Postgres."""

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def ping(self) -> bool: ...

    async def create_conversation(self, title: str, user_id: str | None = None) -> Conversation: ...
    async def get_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> Conversation | None: ...
    async def list_conversations(
        self, limit: int, cursor: str | None, user_id: str | None = None
    ) -> tuple[list[ConversationListItem], str | None]: ...
    async def set_title(
        self, conversation_id: str, title: str, user_id: str | None = None
    ) -> Conversation | None: ...
    async def set_summary(
        self, conversation_id: str, summary: str, up_to_message_id: str | None
    ) -> Conversation | None: ...
    async def delete_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> bool: ...

    async def add_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
        langfuse_trace_id: str | None = None,
    ) -> Message: ...
    async def get_message(self, message_id: str, user_id: str | None = None) -> Message | None: ...
    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        before: str | None = None,
        include_tool_messages: bool = False,
    ) -> tuple[list[Message], bool]: ...
    async def history(self, conversation_id: str) -> list[Message]: ...


class MemoryRepository:
    """Implementación en memoria. Sin durabilidad: solo dev y tests."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        # Dueño por conversación. En Postgres es `conversations.user_id`, que
        # existe desde el MVP; acá hace falta un dict propio.
        self._duenos: dict[str, str | None] = {}
        self._messages: dict[str, list[Message]] = {}
        self._by_message_id: dict[str, Message] = {}
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        logger.warning(
            "storage_memoria",
            detail="las conversaciones se pierden al reiniciar; definí DATABASE_URL para Postgres",
        )

    async def shutdown(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def create_conversation(self, title: str, user_id: str | None = None) -> Conversation:
        now = _now()
        conversation = Conversation(id=_new_id(), title=title, created_at=now, updated_at=now)
        async with self._lock:
            self._conversations[conversation.id] = conversation
            self._duenos[conversation.id] = user_id
            self._messages[conversation.id] = []
        return conversation

    def _es_de(self, conversation_id: str, user_id: str | None) -> bool:
        """Si esa conversación le pertenece a ese usuario.

        Con `SIN_USUARIO` a los dos lados es siempre cierto, que es el
        comportamiento de hoy; con usuarios reales, decide.
        """
        return self._duenos.get(conversation_id) == user_id

    async def get_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> Conversation | None:
        if not self._es_de(conversation_id, user_id):
            return None
        return self._conversations.get(conversation_id)

    async def list_conversations(
        self, limit: int, cursor: str | None, user_id: str | None = None
    ) -> tuple[list[ConversationListItem], str | None]:
        ordered = sorted(
            (c for c in self._conversations.values() if self._es_de(c.id, user_id)),
            key=lambda c: (c.updated_at, c.id),
            reverse=True,
        )
        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded:
                stamp, last_id = decoded
                ordered = [c for c in ordered if (c.updated_at, c.id) < (stamp, last_id)]
        page = ordered[:limit]
        next_cursor = (
            _encode_cursor(page[-1].updated_at, page[-1].id) if len(ordered) > limit else None
        )
        items = [
            ConversationListItem(
                id=c.id,
                title=c.title,
                preview=self._preview(c.id),
                updated_at=c.updated_at,
            )
            for c in page
        ]
        return items, next_cursor

    def _preview(self, conversation_id: str) -> str:
        messages = [m for m in self._messages.get(conversation_id, []) if m.role != "tool"]
        if not messages:
            return ""
        return messages[-1].content[:PREVIEW_CHARS]

    async def set_title(
        self, conversation_id: str, title: str, user_id: str | None = None
    ) -> Conversation | None:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or not self._es_de(conversation_id, user_id):
                return None
            updated = conversation.model_copy(update={"title": title, "updated_at": _now()})
            self._conversations[conversation_id] = updated
            return updated

    async def set_summary(
        self, conversation_id: str, summary: str, up_to_message_id: str | None
    ) -> Conversation | None:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                return None
            # updated_at no se toca: compactar es mantenimiento interno y no
            # debería reordenar el sidebar como si el usuario hubiera escrito.
            updated = conversation.model_copy(
                update={"summary": summary, "summary_up_to_message_id": up_to_message_id}
            )
            self._conversations[conversation_id] = updated
            return updated

    async def delete_conversation(self, conversation_id: str, user_id: str | None = None) -> bool:
        async with self._lock:
            if conversation_id not in self._conversations or not self._es_de(
                conversation_id, user_id
            ):
                return False
            del self._conversations[conversation_id]
            self._duenos.pop(conversation_id, None)
            for message in self._messages.pop(conversation_id, []):
                self._by_message_id.pop(message.id, None)
            return True

    async def add_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
        langfuse_trace_id: str | None = None,
    ) -> Message:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            message = Message(
                id=_new_id(),
                conversation_id=conversation_id,
                role=role,
                content=content,
                metadata=metadata or {},
                langfuse_trace_id=langfuse_trace_id,
                created_at=_now(),
            )
            self._messages[conversation_id].append(message)
            self._by_message_id[message.id] = message
            # updated_at manda el orden del sidebar.
            self._conversations[conversation_id] = conversation.model_copy(
                update={"updated_at": message.created_at}
            )
            return message

    async def get_message(self, message_id: str, user_id: str | None = None) -> Message | None:
        mensaje = self._by_message_id.get(message_id)
        if mensaje is None or not self._es_de(mensaje.conversation_id, user_id):
            return None
        return mensaje

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        before: str | None = None,
        include_tool_messages: bool = False,
    ) -> tuple[list[Message], bool]:
        messages = list(self._messages.get(conversation_id, []))
        if not include_tool_messages:
            messages = [m for m in messages if m.role != "tool"]
        if before:
            index = next((i for i, m in enumerate(messages) if m.id == before), None)
            if index is not None:
                messages = messages[:index]
        has_more = len(messages) > limit
        return messages[-limit:], has_more

    async def history(self, conversation_id: str) -> list[Message]:
        return list(self._messages.get(conversation_id, []))


class PostgresRepository:
    """Implementación real sobre PostgreSQL con psycopg 3 (pool async)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def startup(self) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            self._dsn, min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row}
        )
        await self._pool.open(wait=True, timeout=10)
        await self._migrate()
        logger.info("storage_postgres_listo")

    async def _migrate(self) -> None:
        """Aplica las migraciones pendientes, en orden y una sola vez.

        Hasta ahora se re-ejecutaban todas en cada arranque y funcionaba solo
        porque son `IF NOT EXISTS`. En cuanto una migración tenga un ALTER o
        mueva datos, eso rompe. El registro va en `schema_migrations`.
        """
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    archivo     text PRIMARY KEY,
                    aplicada_en timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            # Dos instancias arrancando a la vez no deben aplicar lo mismo.
            await conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            try:
                cur = await conn.execute("SELECT archivo FROM schema_migrations")
                aplicadas = {fila["archivo"] for fila in await cur.fetchall()}

                for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if path.name in aplicadas:
                        continue
                    async with conn.transaction():
                        await conn.execute(path.read_text(encoding="utf-8"))
                        await conn.execute(
                            "INSERT INTO schema_migrations (archivo) VALUES (%s)",
                            (path.name,),
                        )
                    logger.info("migracion_aplicada", archivo=path.name)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @property
    def pool(self) -> Any:
        """El pool ya abierto, para que el store del RAG no abra otro propio."""
        return self._pool

    async def ping(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001 - el detalle va al log, no al cliente
            logger.warning("postgres_ping_fallo", error_type=type(exc).__name__)
            return False

    @staticmethod
    def _to_conversation(row: dict[str, Any]) -> Conversation:
        return Conversation(
            id=str(row["id"]),
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=row["summary"],
            summary_up_to_message_id=(
                str(row["summary_up_to_message_id"]) if row["summary_up_to_message_id"] else None
            ),
        )

    @staticmethod
    def _to_message(row: dict[str, Any]) -> Message:
        return Message(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            role=row["role"],
            content=row["content"],
            metadata=row["metadata"] or {},
            langfuse_trace_id=row["langfuse_trace_id"],
            created_at=row["created_at"],
        )

    _CONVERSATION_COLS = "id, title, summary, summary_up_to_message_id, created_at, updated_at"
    _MESSAGE_COLS = "id, conversation_id, role, content, metadata, langfuse_trace_id, created_at"

    async def create_conversation(self, title: str, user_id: str | None = None) -> Conversation:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO conversations (title, user_id) VALUES (%s, %s) "
                f"RETURNING {self._CONVERSATION_COLS}",
                (title, user_id),
            )
            row = await cur.fetchone()
        return self._to_conversation(row)

    async def get_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> Conversation | None:
        if not _is_uuid(conversation_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {self._CONVERSATION_COLS} FROM conversations "
                "WHERE id = %s AND user_id IS NOT DISTINCT FROM %s",
                (conversation_id, user_id),
            )
            row = await cur.fetchone()
        return self._to_conversation(row) if row else None

    async def list_conversations(
        self, limit: int, cursor: str | None, user_id: str | None = None
    ) -> tuple[list[ConversationListItem], str | None]:
        # Se pide una fila extra para saber si hay página siguiente.
        decoded = _decode_cursor(cursor) if cursor else None
        params: tuple[Any, ...]
        # El filtro por dueño va siempre; el del cursor solo cuando hay página.
        where = "WHERE c.user_id IS NOT DISTINCT FROM %s"
        if decoded:
            where += " AND (c.updated_at, c.id) < (%s, %s)"
            params = (user_id, decoded[0], decoded[1], limit + 1)
        else:
            params = (user_id, limit + 1)
        query = f"""
            SELECT c.id, c.title, c.updated_at,
                   COALESCE((
                       SELECT LEFT(m.content, {PREVIEW_CHARS}) FROM messages m
                       WHERE m.conversation_id = c.id AND m.role <> 'tool'
                       ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                   ), '') AS preview
            FROM conversations c
            {where}
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT %s
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(query, params)
            rows = await cur.fetchall()
        page = rows[:limit]
        items = [
            ConversationListItem(
                id=str(r["id"]), title=r["title"], preview=r["preview"], updated_at=r["updated_at"]
            )
            for r in page
        ]
        # El cursor se arma con `items`, no con `page`: las filas de psycopg son
        # dicts y no tienen atributos.
        next_cursor = (
            _encode_cursor(items[-1].updated_at, items[-1].id)
            if len(rows) > limit and items
            else None
        )
        return items, next_cursor

    async def set_title(
        self, conversation_id: str, title: str, user_id: str | None = None
    ) -> Conversation | None:
        if not _is_uuid(conversation_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""UPDATE conversations SET title = %s, updated_at = now()
                    WHERE id = %s AND user_id IS NOT DISTINCT FROM %s
                    RETURNING {self._CONVERSATION_COLS}""",
                (title, conversation_id, user_id),
            )
            row = await cur.fetchone()
        return self._to_conversation(row) if row else None

    async def set_summary(
        self, conversation_id: str, summary: str, up_to_message_id: str | None
    ) -> Conversation | None:
        if not _is_uuid(conversation_id):
            return None
        # updated_at no se toca: compactar es mantenimiento interno y no debería
        # reordenar el sidebar como si el usuario hubiera escrito algo.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""UPDATE conversations SET summary = %s, summary_up_to_message_id = %s
                    WHERE id = %s RETURNING {self._CONVERSATION_COLS}""",
                (summary, up_to_message_id, conversation_id),
            )
            row = await cur.fetchone()
        return self._to_conversation(row) if row else None

    async def delete_conversation(self, conversation_id: str, user_id: str | None = None) -> bool:
        if not _is_uuid(conversation_id):
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM conversations WHERE id = %s AND user_id IS NOT DISTINCT FROM %s",
                (conversation_id, user_id),
            )
        return cur.rowcount > 0

    async def add_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
        langfuse_trace_id: str | None = None,
    ) -> Message:
        from psycopg.types.json import Jsonb

        if not _is_uuid(conversation_id):
            raise KeyError(conversation_id)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    f"""INSERT INTO messages
                        (conversation_id, role, content, metadata, langfuse_trace_id)
                        VALUES (%s, %s, %s, %s, %s) RETURNING {self._MESSAGE_COLS}""",
                    (conversation_id, role, content, Jsonb(metadata or {}), langfuse_trace_id),
                )
                row = await cur.fetchone()
                await conn.execute(
                    "UPDATE conversations SET updated_at = now() WHERE id = %s",
                    (conversation_id,),
                )
        return self._to_message(row)

    async def get_message(self, message_id: str, user_id: str | None = None) -> Message | None:
        if not _is_uuid(message_id):
            return None
        # `messages` no lleva `user_id`: el dueño es el de su conversación, así
        # que el filtro va por el JOIN. Una columna propia podría desincronizarse
        # con la de la conversación, que es la que manda.
        cols = ", ".join(f"m.{c}" for c in self._MESSAGE_COLS.split(", "))
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT {cols} FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.id = %s AND c.user_id IS NOT DISTINCT FROM %s""",
                (message_id, user_id),
            )
            row = await cur.fetchone()
        return self._to_message(row) if row else None

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        before: str | None = None,
        include_tool_messages: bool = False,
    ) -> tuple[list[Message], bool]:
        if not _is_uuid(conversation_id):
            return [], False
        clauses = ["conversation_id = %s"]
        params: list[Any] = [conversation_id]
        if not include_tool_messages:
            clauses.append("role <> 'tool'")
        if before and _is_uuid(before):
            clauses.append("(created_at, id) < (SELECT created_at, id FROM messages WHERE id = %s)")
            params.append(before)
        params.append(limit + 1)
        query = f"""
            SELECT {self._MESSAGE_COLS} FROM messages
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(query, params)
            rows = await cur.fetchall()
        has_more = len(rows) > limit
        # Se consulta en desc para poder paginar hacia atrás; la UI los quiere en orden.
        messages = [self._to_message(r) for r in reversed(rows[:limit])]
        return messages, has_more

    async def history(self, conversation_id: str) -> list[Message]:
        if not _is_uuid(conversation_id):
            return []
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT {self._MESSAGE_COLS} FROM messages WHERE conversation_id = %s
                    ORDER BY created_at ASC, id ASC""",
                (conversation_id,),
            )
            rows = await cur.fetchall()
        return [self._to_message(r) for r in rows]


def _is_uuid(value: str) -> bool:
    """Evita que un id inválido llegue a Postgres y explote como error 500."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def build_repository(use_postgres: bool, database_url: str) -> Repository:
    if use_postgres:
        if not database_url:
            raise RuntimeError("BYTE_STORAGE=postgres requiere DATABASE_URL")
        return PostgresRepository(database_url)
    return MemoryRepository()
