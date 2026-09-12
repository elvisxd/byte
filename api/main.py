"""Aplicación FastAPI de Byte.

Monta la API bajo /api/v1, la página mínima del chat y arma en el arranque todo
lo que las rutas necesitan: credenciales, almacenamiento, modelo, herramientas,
grafo y el administrador de runs.
"""

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from agent.graph import build_graph
from agent.llm import build_llm
from agent.runner import RunManager
from api.auth import JWTService, Passwords, RefreshService
from api.config import Settings, get_settings
from api.deps import AppContext, limiter
from api.errors import error_response, register_error_handlers
from api.logging import configure_logging, get_logger, new_request_id, set_request_id
from api.observabilidad import build_trazas, init_errores
from api.routes import (
    auth,
    conversations,
    documents,
    execute,
    health,
    openai,
    runs,
    session,
    tools,
)
from api.security import Credentials, TokenService, resolve_secret_key, security_headers
from db.repository import Repository, build_repository
from models.schemas import ErrorEnvelope
from tools.base import ToolRegistry
from tools.registry import build_registry

logger = get_logger("api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# Tope de cuerpo para las rutas del MVP. Los uploads del RAG (Fase 2) tendrán el suyo.
MAX_REQUEST_BYTES = 64 * 1024


def _build_rag(settings: Settings, repo: Repository) -> Any:
    """El servicio de RAG, solo si el repositorio es Postgres con su pool abierto.

    Sin Postgres no hay pgvector: el agente arranca sin doc_search y las rutas
    de documentos responden 503.
    """
    pool = getattr(repo, "pool", None)
    if pool is None:
        return None
    from rag.service import build_rag_service

    return build_rag_service(settings, pool)


def _avisar_si_hay_varios_workers(env: str) -> None:
    """Byte asume un solo proceso, y conviene que se note al arrancar.

    Los runs viven en un dict del `RunManager` y los nonces ya canjeados en otro
    del `TokenService`, así que con dos workers: `GET /runs/{id}/events` no
    encuentra los runs del otro proceso, y el "un solo uso" del `resume_token` y
    del `events_token` deja de ser una garantía — el mismo token se canjea una
    vez por worker.

    El Dockerfile fija `--workers 1`, pero `WEB_CONCURRENCY` lo pisa sin tocarlo
    y es lo que varios PaaS —Railway incluido, que es el deploy de la Fase 7—
    definen solo. En prod se corta el arranque; en dev alcanza con avisar.
    """
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        return
    if workers <= 1:
        return

    detalle = (
        "Byte guarda los runs y los nonces en memoria del proceso: con varios "
        "workers el SSE no encuentra runs de otro proceso y los tokens de un "
        "solo uso valen una vez por worker. Sacá WEB_CONCURRENCY o dejalo en 1."
    )
    if env == "prod":
        raise RuntimeError(f"WEB_CONCURRENCY={workers} no está soportado. {detalle}")
    logger.warning("varios_workers", workers=workers, detail=detalle)


async def _purgar_periodicamente(repo: Repository, dias: int, cada_horas: int = 6) -> None:
    """Borra cada tantas horas las conversaciones viejas de `/v1`.

    Corre una vez al arrancar y después en bucle. Un cron sería más prolijo,
    pero agrega una pieza que hay que instalar y vigilar aparte; esto vive y
    muere con el proceso, que para una limpieza sin urgencia alcanza.

    Cualquier error se registra y el bucle sigue: que falle una purga no puede
    tirar abajo la app.
    """
    while True:
        try:
            borradas = await repo.purgar_conversaciones_openai(dias)
            if borradas:
                logger.info("conversaciones_openai_purgadas", cantidad=borradas, dias=dias)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - una purga fallida no rompe nada
            logger.warning("purga_fallida", error_type=type(exc).__name__)
        await asyncio.sleep(cada_horas * 3600)


async def _sumar_herramientas_mcp(
    settings: Settings, registry: ToolRegistry, stack: AsyncExitStack
) -> None:
    """Conecta los servidores MCP declarados y suma sus herramientas al registro.

    Se hace acá y no en `build_registry` porque conectar es asíncrono: cada
    servidor es un handshake y un `list_tools`. Un servidor caído no impide
    arrancar — el agente sigue con las herramientas que tenga, igual que arranca
    sin Tavily o sin sandbox.

    Un nombre repetido no pisa a una herramienta nativa: `code_exec` tiene que
    seguir siendo el sandbox de Byte aunque un servidor externo declare otra con
    ese nombre (docs/seguridad-byte.md, tool poisoning).
    """
    from mcp_client.client import conectar_servidores

    herramientas, servidores = await conectar_servidores(
        settings.mcp_servers,
        settings.max_tool_result_chars,
        settings.mcp_timeout_s,
        settings.mcp_tokens,
    )

    async def cerrar_todo() -> None:
        for servidor in servidores:
            await servidor.cerrar()

    stack.push_async_callback(cerrar_todo)

    for herramienta in herramientas:
        if not herramienta.name:
            # `conectar_servidores` ya descarta los nombres que no son
            # identificadores, pero el registro es la última puerta: una
            # herramienta sin nombre no se puede llamar y el modelo la vería
            # igual en su lista.
            logger.warning("mcp_herramienta_sin_nombre", origen=herramienta.source)
            continue
        if registry.get(herramienta.name) is not None:
            logger.warning(
                "mcp_herramienta_duplicada",
                herramienta=herramienta.name,
                origen=herramienta.source,
                detail="ya existe una con ese nombre; se ignora la del servidor MCP",
            )
            continue
        registry.add(herramienta)


async def _build_checkpointer(settings: Settings, stack: AsyncExitStack) -> Any:
    """Checkpointer de LangGraph: Postgres cuando hay DSN, memoria en dev.

    Con `thread_id = conversation_id`, cada conversación retoma donde quedó.
    """
    if settings.use_postgres:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(settings.database_url)
        )
        await saver.setup()
        logger.info("checkpointer_postgres")
        return saver

    from langgraph.checkpoint.memory import MemorySaver

    logger.warning(
        "checkpointer_memoria",
        detail="el hilo del agente se pierde al reiniciar; definí DATABASE_URL para Postgres",
    )
    return MemorySaver()


def create_app(
    *,
    settings: Settings | None = None,
    llm: Any | None = None,
    repository: Repository | None = None,
    registry: ToolRegistry | None = None,
) -> FastAPI:
    """Crea la app. Los parámetros permiten inyectar dobles en los tests."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        credentials = Credentials(resolved_settings.api_key, resolve_secret_key(resolved_settings))
        if not credentials.configured:
            if resolved_settings.env == "prod":
                raise RuntimeError("BYTE_API_KEY es obligatorio con BYTE_ENV=prod")
            logger.warning(
                "sin_api_key", detail="definí BYTE_API_KEY: la API rechaza todo sin ella"
            )

        # Observabilidad: las dos apagadas si no están configuradas, y ninguna
        # puede impedir que Byte arranque.
        init_errores(resolved_settings)
        trazas = build_trazas(resolved_settings)

        _avisar_si_hay_varios_workers(resolved_settings.env)

        repo = repository or build_repository(
            resolved_settings.use_postgres, resolved_settings.database_url
        )
        await repo.startup()

        # El RAG reusa el pool del repositorio: sin Postgres no hay store, y
        # tanto la búsqueda en documentos como /documents quedan fuera.
        rag = _build_rag(resolved_settings, repo)
        if rag is not None:
            # Las tareas de ingesta viven en memoria: lo que estaba indexándose
            # cuando se cayó el proceso quedaría en 'processing' para siempre.
            huerfanos = await rag.store.recuperar_huerfanos()
            if huerfanos:
                logger.warning("documentos_interrumpidos", cantidad=huerfanos)

        async with AsyncExitStack() as stack:
            checkpointer = await _build_checkpointer(resolved_settings, stack)
            tool_registry = registry or build_registry(
                resolved_settings, rag.store if rag else None
            )
            # Las herramientas MCP se suman al registro ya armado: conectar es
            # asíncrono (handshake por servidor) y build_registry no lo es. Las
            # conexiones viven lo que vive la app, y el stack las cierra.
            if registry is None and resolved_settings.mcp_servers:
                await _sumar_herramientas_mcp(resolved_settings, tool_registry, stack)
            modelo = llm or build_llm(resolved_settings)
            graph = build_graph(
                modelo,
                tool_registry,
                max_iterations=resolved_settings.max_iterations,
                max_tool_result_chars=resolved_settings.max_tool_result_chars,
                num_ctx=resolved_settings.ollama_num_ctx,
                checkpointer=checkpointer,
            )
            tokens = TokenService(
                resolve_secret_key(resolved_settings),
                resolved_settings.events_token_ttl_s,
                resolved_settings.session_ttl_s,
                resolved_settings.resume_token_ttl_s,
            )
            run_manager = RunManager(
                graph,
                repo,
                tokens,
                max_concurrent_runs=resolved_settings.max_concurrent_runs,
                run_timeout_s=resolved_settings.run_timeout_s,
                max_iterations=resolved_settings.max_iterations,
                resume_ttl_s=resolved_settings.resume_token_ttl_s,
                trazas=trazas,
            )
            app.state.ctx = AppContext(
                settings=resolved_settings,
                credentials=credentials,
                tokens=tokens,
                passwords=Passwords(),
                jwt=JWTService(resolve_secret_key(resolved_settings), resolved_settings.jwt_ttl_s),
                refresh=RefreshService(repo, resolved_settings.refresh_ttl_s),
                trazas=trazas,
                repository=repo,
                runs=run_manager,
                registry=tool_registry,
                checkpointer=checkpointer,
                rag=rag,
                # Para el /compact manual, que resume fuera del grafo.
                llm=modelo,
            )
            logger.info(
                "byte_arriba",
                env=resolved_settings.env,
                modelo=resolved_settings.ollama_model,
                herramientas=[tool.name for tool in tool_registry.all()],
                storage="postgres" if resolved_settings.use_postgres else "memoria",
            )
            purga: asyncio.Task[None] | None = None
            if resolved_settings.openai_retencion_dias > 0:
                purga = asyncio.create_task(
                    _purgar_periodicamente(repo, resolved_settings.openai_retencion_dias)
                )

            try:
                yield
            finally:
                if purga is not None:
                    purga.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await purga
                await run_manager.shutdown()
                await repo.shutdown()
                # Lo último: manda las trazas del último run antes de cerrar.
                trazas.flush()

    app = FastAPI(
        title="Byte",
        description="Agente de IA local (self-hosted)",
        version=resolved_settings.version,
        lifespan=lifespan,
    )
    app.state.limiter = limiter

    # --- Middlewares (el último agregado corre primero) ---
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """request_id para correlacionar, cabeceras de seguridad y tope de cuerpo."""
        request_id = new_request_id()
        set_request_id(request_id)

        # Los uploads del RAG tienen su propio tope (max_document_bytes, 20 MB):
        # el de 64 KB es para los JSON del resto de la API.
        tope = (
            resolved_settings.max_document_bytes
            if request.url.path.endswith("/documents")
            else MAX_REQUEST_BYTES
        )
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > tope:
            return error_response(413, "payload_too_large", "El cuerpo es demasiado grande")

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        es_docs = request.url.path in ("/docs", "/redoc")
        for header, value in security_headers(resolved_settings.env, docs=es_docs).items():
            response.headers.setdefault(header, value)
        response.headers["X-Request-ID"] = request_id
        # Sin contenido de mensajes en el log: solo ruta, código y duración.
        logger.info(
            "http",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round(elapsed_ms, 1),
        )
        return response

    if resolved_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key", "Last-Event-ID"],
        )
    # Rate limit general por credencial. Los límites más ajustados van por ruta.
    app.add_middleware(SlowAPIMiddleware)

    # --- Errores ---
    register_error_handlers(app)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_request: Request, exc: RateLimitExceeded) -> Response:
        return error_response(
            429,
            "rate_limited",
            "Demasiadas solicitudes: bajá el ritmo",
            {"Retry-After": "60"},
        )

    # --- Rutas ---
    # Todos los errores salen con el envoltorio del contrato, incluido el 422:
    # se declara así para que el spec no prometa el formato por defecto de
    # FastAPI, que no es el que devuelve la API.
    errores_comunes: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorEnvelope, "description": descripcion}
        for code, descripcion in (
            (401, "Credencial faltante o inválida"),
            (404, "No existe"),
            (413, "Cuerpo demasiado grande"),
            (422, "Validación"),
            (429, "Rate limit o demasiados runs"),
            (500, "Error interno"),
            (503, "Servicio o modelo no disponible"),
        )
    }
    for router in (
        health.router,
        auth.router,
        session.router,
        conversations.router,
        runs.router,
        tools.router,
        execute.router,
        documents.router,
    ):
        app.include_router(router, prefix="/api/v1", responses=errores_comunes)

    # La compatibilidad con OpenAI va en /v1 y no en /api/v1: los clientes arman
    # la URL pegando "/chat/completions" a la base que uno configura, y la
    # mayoría no deja poner un prefijo propio.
    app.include_router(openai.router, prefix="/v1", responses=errores_comunes)

    static_dir = WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        page = WEB_DIR / "templates" / "index.html"
        if not page.is_file():
            return HTMLResponse("<h1>Byte</h1><p>API en /docs</p>")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    return app


app = create_app()
