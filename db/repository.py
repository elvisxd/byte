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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from api.logging import get_logger
from models.schemas import Conversation, ConversationListItem, Message, MessageRole, User

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

# Las conversaciones que entra por `/v1/chat/completions` llevan este prefijo en
# el título. Un cliente de OpenAI manda su historial completo en cada pedido y no
# tiene dónde guardar un id de Byte, así que cada llamada crea una: sin purga,
# con el canal de email de n8n activo la base crece sin techo. Es el prefijo y no
# una columna porque no cambia nada del modelo — y si algún día hace falta
# distinguir más orígenes, ahí sí conviene la columna.
PREFIJO_OPENAI = "[openai] "
# Id arbitrario pero fijo para el advisory lock de las migraciones.
MIGRATION_LOCK_ID = 8_675_309
PREVIEW_CHARS = 120


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Lo que pasó al intentar canjear un refresh token.

    `reusado` es el caso que importa: un token ya canjeado que vuelve a
    aparecer significa que alguien tiene una copia — la de quien lo robó o la
    del dueño legítimo, y no hay forma de saber cuál. Se revoca la familia
    entera y los dos tienen que volver a entrar, que es la única respuesta
    segura (OAuth 2.0 BCP, sección 4.13.2).
    """

    user_id: str | None = None
    family_id: str | None = None
    reusado: bool = False


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

    # --- Usuarios (Fase 4) ---
    async def create_user(self, email: str, password_hash: str) -> User | None: ...
    async def get_user_by_email(self, email: str) -> tuple[User, str] | None: ...
    async def get_user(self, user_id: str) -> User | None: ...
    async def set_password_hash(self, user_id: str, password_hash: str) -> bool: ...

    # --- Refresh tokens (Fase 4) ---
    async def save_refresh_token(
        self, user_id: str, token_hash: str, family_id: str, expires_at: datetime
    ) -> None: ...
    async def use_refresh_token(self, token_hash: str) -> RefreshResult: ...
    async def revoke_refresh_family(self, family_id: str) -> int: ...
    async def family_of_refresh_token(self, token_hash: str) -> str | None: ...

    async def purgar_conversaciones_openai(self, dias: int) -> int: ...

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
        # Usuario → (usuario, hash de su contraseña). El hash vive acá y no en
        # el modelo: `User` es lo que la API puede devolver.
        self._usuarios: dict[str, tuple[User, str]] = {}
        # hash del token → su fila. En Postgres es la tabla `refresh_tokens`.
        self._refresh: dict[str, dict[str, Any]] = {}
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

    async def create_user(self, email: str, password_hash: str) -> User | None:
        """Devuelve `None` si el email ya está tomado, como el UNIQUE de Postgres."""
        email = email.strip().lower()
        async with self._lock:
            if any(u.email == email for u, _ in self._usuarios.values()):
                return None
            usuario = User(id=_new_id(), email=email, created_at=_now())
            self._usuarios[usuario.id] = (usuario, password_hash)
            return usuario

    async def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        """El usuario y su hash. El hash solo sale por acá, para el login."""
        email = email.strip().lower()
        return next((par for par in self._usuarios.values() if par[0].email == email), None)

    async def get_user(self, user_id: str) -> User | None:
        par = self._usuarios.get(user_id)
        return par[0] if par else None

    async def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        """Para migrar un hash con parámetros viejos en el login."""
        async with self._lock:
            par = self._usuarios.get(user_id)
            if par is None:
                return False
            self._usuarios[user_id] = (par[0], password_hash)
            return True

    async def save_refresh_token(
        self, user_id: str, token_hash: str, family_id: str, expires_at: datetime
    ) -> None:
        async with self._lock:
            self._refresh[token_hash] = {
                "user_id": user_id,
                "family_id": family_id,
                "expires_at": expires_at,
                "used_at": None,
                "revoked_at": None,
            }

    async def use_refresh_token(self, token_hash: str) -> RefreshResult:
        async with self._lock:
            fila = self._refresh.get(token_hash)
            if fila is None:
                return RefreshResult()
            if fila["used_at"] is not None:
                # Ya se canjeó y vuelve a aparecer: alguien tiene una copia.
                return RefreshResult(family_id=fila["family_id"], reusado=True)
            if fila["revoked_at"] is not None or fila["expires_at"] <= _now():
                return RefreshResult()
            fila["used_at"] = _now()
            return RefreshResult(user_id=fila["user_id"], family_id=fila["family_id"])

    async def family_of_refresh_token(self, token_hash: str) -> str | None:
        """La familia de un token, sin canjearlo. Para el logout: marcar el
        token como usado haría que el siguiente intento pareciera un reuso."""
        fila = self._refresh.get(token_hash)
        return fila["family_id"] if fila else None

    async def revoke_refresh_family(self, family_id: str) -> int:
        async with self._lock:
            revocados = 0
            for fila in self._refresh.values():
                if fila["family_id"] == family_id and fila["revoked_at"] is None:
                    fila["revoked_at"] = _now()
                    revocados += 1
            return revocados

    async def purgar_conversaciones_openai(self, dias: int) -> int:
        """Borra las conversaciones de `/v1` sin actividad en `dias`.

        Solo las de ese origen: una conversación del chat la abrió alguien a
        propósito y borrarla sola sería perder trabajo. Las de `/v1` las crea
        una llamada de API que ya se llevó su respuesta.
        """
        corte = _now() - timedelta(days=dias)
        async with self._lock:
            viejas = [
                c.id
                for c in self._conversations.values()
                if c.title.startswith(PREFIJO_OPENAI) and c.updated_at < corte
            ]
            for conversation_id in viejas:
                del self._conversations[conversation_id]
                self._duenos.pop(conversation_id, None)
                for message in self._messages.pop(conversation_id, []):
                    self._by_message_id.pop(message.id, None)
            return len(viejas)

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

    @staticmethod
    def _to_user(row: dict[str, Any]) -> User:
        return User(id=str(row["id"]), email=row["email"], created_at=row["created_at"])

    async def create_user(self, email: str, password_hash: str) -> User | None:
        """`None` si el email ya está tomado: lo decide el UNIQUE de la tabla.

        `ON CONFLICT DO NOTHING` en vez de consultar antes: entre el SELECT y el
        INSERT pueden entrar dos registros con el mismo email, y el índice único
        es la única garantía real.
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO users (email, password_hash) VALUES (%s, %s)
                   ON CONFLICT (email) DO NOTHING
                   RETURNING id, email, created_at""",
                (email.strip().lower(), password_hash),
            )
            row = await cur.fetchone()
        return self._to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, email, created_at, password_hash FROM users WHERE email = %s",
                (email.strip().lower(),),
            )
            row = await cur.fetchone()
        return (self._to_user(row), row["password_hash"]) if row else None

    async def get_user(self, user_id: str) -> User | None:
        if not _is_uuid(user_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, email, created_at FROM users WHERE id = %s", (user_id,)
            )
            row = await cur.fetchone()
        return self._to_user(row) if row else None

    async def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        """Para migrar un hash con parámetros viejos en el login."""
        if not _is_uuid(user_id):
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id)
            )
        return cur.rowcount > 0

    async def save_refresh_token(
        self, user_id: str, token_hash: str, family_id: str, expires_at: datetime
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, token_hash, family_id, expires_at),
            )

    async def use_refresh_token(self, token_hash: str) -> RefreshResult:
        """Canjea un refresh token, marcándolo como usado.

        El UPDATE condicional hace el canje atómico: dos peticiones con el mismo
        token compiten por la misma fila y solo una la actualiza. Con un SELECT
        previo, las dos lo verían sin usar y las dos recibirían tokens nuevos —
        que es justo el agujero que la rotación tiene que cerrar.
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE refresh_tokens SET used_at = now()
                   WHERE token_hash = %s
                     AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
                   RETURNING user_id, family_id""",
                (token_hash,),
            )
            fila = await cur.fetchone()
            if fila is not None:
                return RefreshResult(user_id=str(fila["user_id"]), family_id=str(fila["family_id"]))

            # No se canjeó. Distinguir "ya usado" de "no existe" es lo que
            # permite detectar el reuso: por eso las filas no se borran.
            cur = await conn.execute(
                "SELECT family_id FROM refresh_tokens "
                "WHERE token_hash = %s AND used_at IS NOT NULL",
                (token_hash,),
            )
            usado = await cur.fetchone()
        if usado is not None:
            return RefreshResult(family_id=str(usado["family_id"]), reusado=True)
        return RefreshResult()

    async def family_of_refresh_token(self, token_hash: str) -> str | None:
        """La familia de un token, sin canjearlo (ver la versión en memoria)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT family_id FROM refresh_tokens WHERE token_hash = %s", (token_hash,)
            )
            fila = await cur.fetchone()
        return str(fila["family_id"]) if fila else None

    async def revoke_refresh_family(self, family_id: str) -> int:
        if not _is_uuid(family_id):
            return 0
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE refresh_tokens SET revoked_at = now() "
                "WHERE family_id = %s AND revoked_at IS NULL",
                (family_id,),
            )
        return cur.rowcount

    async def purgar_conversaciones_openai(self, dias: int) -> int:
        """Borra las conversaciones de `/v1` sin actividad en `dias`.

        Los mensajes se van con ellas por el `ON DELETE CASCADE` de la FK.
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM conversations "
                "WHERE title LIKE %s AND updated_at < now() - make_interval(days => %s)",
                # En LIKE de Postgres los corchetes no son especiales; solo lo
                # son `%` y `_`, que el prefijo no tiene.
                (PREFIJO_OPENAI + "%", dias),
            )
        return cur.rowcount

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
