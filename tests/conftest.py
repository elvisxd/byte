"""Fixtures comunes. Los tests no tocan Ollama, Tavily ni Postgres reales."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import create_app
from db.repository import MemoryRepository
from tests.fakes import FakeLLM, FakeSandbox, FakeTavily, text_turn
from tools.base import ToolRegistry
from tools.code_exec import build_code_exec_tool
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
    "SANDBOX_URL",
    "SANDBOX_TOKEN",
    "OLLAMA_BASE_URL",
)

# Las de texto que el `.env` del desarrollador suele tener puestas y que algún
# test necesita ver ausentes. Vacío es el default de todas: `Settings` lo lee
# como "no configurado" y el agente arranca sin esa herramienta.
_VACIAS = (
    "TAVILY_API_KEY",
    "SANDBOX_URL",
    "SANDBOX_TOKEN",
    "BYTE_MCP_SERVERS",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Aísla la configuración: el .env del desarrollador no debe afectar los tests.

    Con las de texto no alcanza borrarlas del entorno: `Settings` tiene
    `env_file=".env"`, así que lo que esté en el archivo gana sobre una variable
    ausente. Esas se ponen en vacío, que es su default y lo que `Settings` lee
    como "no configurado" — si no, tener un sandbox o una clave de Tavily en el
    `.env` hace fallar los tests que prueban justamente que no están.
    """
    for variable in _VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    for variable in _VACIAS:
        monkeypatch.setenv(variable, "")
    monkeypatch.setenv("BYTE_API_KEY", API_KEY)
    monkeypatch.setenv("BYTE_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("BYTE_STORAGE", "memory")
    # Los tests no hablan con un Ollama de verdad: el LLM va siempre falseado.
    # Se apunta a un puerto muerto para que el chequeo de salud dé "caido" esté
    # o no corriendo Ollama en la máquina de quien ejecuta los tests.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
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
        con_sandbox: bool = False,
        sandbox_resultado: dict[str, Any] | None = None,
        **env: Any,
    ) -> TestClient:
        for clave, valor in env.items():
            monkeypatch.setenv(clave, str(valor))
        get_settings.cache_clear()

        modelo = llm or FakeLLM(turns or [text_turn("Listo.")])
        tavily = FakeTavily(tavily_results)
        sandbox = FakeSandbox(sandbox_resultado)
        registry = ToolRegistry()
        if con_busqueda:
            registry.add(build_web_search_tool("falsa", 4000, 200, client=tavily))
        if con_sandbox:
            registry.add(build_code_exec_tool("http://sandbox:3000", "tok", 4000, client=sandbox))

        app = create_app(llm=modelo, repository=MemoryRepository(), registry=registry)
        client = TestClient(app)
        client.__enter__()  # corre el lifespan (arma el contexto de la app)
        abiertos.append(client)
        client.llm = modelo  # type: ignore[attr-defined]
        client.tavily = tavily  # type: ignore[attr-defined]
        client.sandbox = sandbox  # type: ignore[attr-defined]
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
