"""`build_llm` elige el proveedor por el nombre: `gemini-…` va a la API de Google.

Lo que protege: que la API y el agente local NO cambien —un nombre de Ollama
sigue armando un ChatOllama con los mismos parámetros—, que el remoto lleve la
clave y el tope de salida medido, y que sin clave no se arme nada en silencio.
"""

from typing import Any

import pytest

from agent.llm import build_llm, es_de_google
from api.config import Settings


class _Espia:
    ultimo: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).ultimo = kwargs


class GeminiEspia(_Espia):
    ultimo: dict[str, Any] = {}


class OllamaEspia(_Espia):
    ultimo: dict[str, Any] = {}


@pytest.fixture
def espias(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain_google_genai
    import langchain_ollama

    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", GeminiEspia)
    monkeypatch.setattr(langchain_ollama, "ChatOllama", OllamaEspia)
    GeminiEspia.ultimo = {}
    OllamaEspia.ultimo = {}


def test_solo_gemini_es_de_google() -> None:
    assert es_de_google("gemini-3.8-flash")
    assert not es_de_google("qwen3:14b")
    # Los Gemma también corren en Ollama: un nombre local no se va a la red.
    assert not es_de_google("gemma3:4b")


def test_un_gemini_se_arma_con_la_clave_y_su_tope(espias: None) -> None:
    ajustes = Settings(GEMINI_API_KEY="clave-de-prueba", BYTE_GEMINI_NUM_PREDICT=8192)

    build_llm(ajustes, "gemini-3.8-flash", reasoning=True)

    assert GeminiEspia.ultimo["model"] == "gemini-3.8-flash"
    assert GeminiEspia.ultimo["google_api_key"] == "clave-de-prueba"
    assert GeminiEspia.ultimo["max_output_tokens"] == 8192
    assert GeminiEspia.ultimo["include_thoughts"] is True
    # Un solo intento: el reintento lo hace el relevo cambiando de modelo.
    assert GeminiEspia.ultimo["max_retries"] == 1
    assert OllamaEspia.ultimo == {}


def test_sin_clave_no_se_arma(espias: None) -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_llm(Settings(GEMINI_API_KEY=""), "gemini-3.8-flash")


def test_el_local_sigue_igual(espias: None) -> None:
    ajustes = Settings(OLLAMA_MODEL="qwen3:14b", GEMINI_API_KEY="clave")

    build_llm(ajustes, num_ctx=12288)

    assert OllamaEspia.ultimo["model"] == "qwen3:14b"
    assert OllamaEspia.ultimo["num_ctx"] == 12288
    assert GeminiEspia.ultimo == {}
