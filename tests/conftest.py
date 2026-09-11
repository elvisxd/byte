"""Fixtures comunes. Los tests no tocan Ollama, Tavily ni Postgres reales."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import create_app
from db.repository import MemoryRepository
from tests.fakes import FakeLLM, FakeTavily, text_turn
from tools.base import ToolRegistry
from tools.web_search import build_web_search_tool

API_KEY = "clave-de-prueba"
AUTH = {"X-API-Key": API_KEY}

_VARIABLES = (
    "BYTE_API_KEY",
    "BYTE_SECRET_KEY",
    "BYTE_ENV",
    "BYTE_STORAGE",
    "DATABASE_URL",
    "TAVILY_API_KEY",
    "BYTE_RATE_LIMIT_GENERAL",
    "BYTE_RATE_LIMIT_RUNS",
    "BYTE_MAX_CONCURRENT_RUNS",
    "BYTE_MAX_ITERATIONS",
    "BYTE_RUN_TIMEOUT_S",
    "BYTE_MAX_MESSAGE_CHARS",
    "BYTE_EVENTS_TOKEN_TTL_S",
)


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Aísla la configuración: el .env del desarrollador no debe afectar los tests."""
    for variable in _VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    monkeypatch.setenv("BYTE_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("BYTE_STORAGE", "memory")
    # Los límites reales se prueban en su test; el resto no debe chocar con ellos.
    monkeypatch.setenv("BYTE_RATE_LIMIT_GENERAL", "1000/minute")
    monkeypatch.setenv("BYTE_RATE_LIMIT_RUNS", "1000/minute")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def crear_cliente(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., TestClient]]:
    """Devuelve una fábrica de clientes de prueba con el LLM y la búsqueda falsos."""
    abiertos: list[TestClient] = []

    def build(
        *,
        turns: list[Any] | None = None,
        llm: Any | None = None,
        tavily_results: list[dict[str, Any]] | None = None,
        con_busqueda: bool = True,
        **env: Any,
    ) -> TestClient:
        for clave, valor in env.items():
            monkeypatch.setenv(clave, str(valor))
        get_settings.cache_clear()

        modelo = llm or FakeLLM(turns or [text_turn("Listo.")])
        tavily = FakeTavily(tavily_results)
        registry = ToolRegistry()
        if con_busqueda:
            registry.add(build_web_search_tool("falsa", 4000, 200, client=tavily))

        app = create_app(llm=modelo, repository=MemoryRepository(), registry=registry)
        client = TestClient(app)
        client.__enter__()  # corre el lifespan (arma el contexto de la app)
        abiertos.append(client)
        client.llm = modelo  # type: ignore[attr-defined]
        client.tavily = tavily  # type: ignore[attr-defined]
        return client

    yield build
    for client in abiertos:
        client.__exit__(None, None, None)


@pytest.fixture
def cliente(crear_cliente: Callable[..., TestClient]) -> TestClient:
    return crear_cliente()


def nueva_conversacion(client: TestClient) -> str:
    respuesta = client.post("/api/v1/conversations", json={}, headers=AUTH)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["id"]
