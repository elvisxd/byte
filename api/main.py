"""Aplicación FastAPI de Byte.

Monta la API bajo /api/v1, la página mínima del chat y arma en el arranque todo
lo que las rutas necesitan: credenciales, almacenamiento, modelo, herramientas,
grafo y el administrador de runs.
"""

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
from api.config import Settings, get_settings
from api.deps import AppContext, limiter
from api.errors import error_response, register_error_handlers
from api.logging import configure_logging, get_logger, new_request_id, set_request_id
from api.routes import conversations, health, runs, session, tools
from api.security import Credentials, TokenService, resolve_secret_key, security_headers
from db.repository import Repository, build_repository
from tools.base import ToolRegistry
from tools.web_search import build_registry

logger = get_logger("api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# Tope de cuerpo para las rutas del MVP. Los uploads del RAG (Fase 2) tendrán el suyo.
MAX_REQUEST_BYTES = 64 * 1024


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
        credentials = Credentials(resolved_settings.api_key)
        if not credentials.configured:
            if resolved_settings.env == "prod":
                raise RuntimeError("BYTE_API_KEY es obligatorio con BYTE_ENV=prod")
            logger.warning(
                "sin_api_key", detail="definí BYTE_API_KEY: la API rechaza todo sin ella"
            )

        repo = repository or build_repository(
            resolved_settings.use_postgres, resolved_settings.database_url
        )
        await repo.startup()

        async with AsyncExitStack() as stack:
            checkpointer = await _build_checkpointer(resolved_settings, stack)
            tool_registry = registry or build_registry(
                resolved_settings.tavily_api_key,
                resolved_settings.max_tool_result_chars,
                resolved_settings.max_search_query_chars,
            )
            graph = build_graph(
                llm or build_llm(resolved_settings),
                tool_registry,
                max_iterations=resolved_settings.max_iterations,
                max_tool_result_chars=resolved_settings.max_tool_result_chars,
                num_ctx=resolved_settings.ollama_num_ctx,
                checkpointer=checkpointer,
            )
            run_manager = RunManager(
                graph,
                repo,
                max_concurrent_runs=resolved_settings.max_concurrent_runs,
                run_timeout_s=resolved_settings.run_timeout_s,
                max_iterations=resolved_settings.max_iterations,
            )
            app.state.ctx = AppContext(
                settings=resolved_settings,
                credentials=credentials,
                tokens=TokenService(
                    resolve_secret_key(resolved_settings),
                    resolved_settings.events_token_ttl_s,
                    resolved_settings.session_ttl_s,
                ),
                repository=repo,
                runs=run_manager,
                registry=tool_registry,
                checkpointer=checkpointer,
            )
            logger.info(
                "byte_arriba",
                env=resolved_settings.env,
                modelo=resolved_settings.ollama_model,
                herramientas=[tool.name for tool in tool_registry.all()],
                storage="postgres" if resolved_settings.use_postgres else "memoria",
            )
            try:
                yield
            finally:
                await run_manager.shutdown()
                await repo.shutdown()

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

        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
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
    for router in (health.router, session.router, conversations.router, runs.router, tools.router):
        app.include_router(router, prefix="/api/v1")

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
