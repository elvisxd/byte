"""`build_llm` elige el proveedor por el nombre: `gemini-…` va a la API de Google.

Lo que protege: que la API y el agente local NO cambien —un nombre de Ollama
sigue armando un ChatOllama con los mismos parámetros—, que el remoto lleve la
clave y el tope de salida medido, y que sin clave no se arme nada en silencio.
"""

from typing import Any

import pytest

from agent.llm import (
    CEREBRAS_BASE_URL,
    GROQ_BASE_URL,
    MISTRAL_BASE_URL,
    NVIDIA_BASE_URL,
    OPENROUTER_BASE_URL,
    build_llm,
    es_de_google,
    es_remoto,
)
from api.config import Settings


class _Espia:
    ultimo: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).ultimo = kwargs


class GeminiEspia(_Espia):
    ultimo: dict[str, Any] = {}


class OllamaEspia(_Espia):
    ultimo: dict[str, Any] = {}


class OpenAIEspia(_Espia):
    ultimo: dict[str, Any] = {}


@pytest.fixture
def espias(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain_google_genai
    import langchain_ollama
    import langchain_openai

    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", GeminiEspia)
    monkeypatch.setattr(langchain_ollama, "ChatOllama", OllamaEspia)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", OpenAIEspia)
    GeminiEspia.ultimo = {}
    OllamaEspia.ultimo = {}
    OpenAIEspia.ultimo = {}


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
    # Y con tope: una llamada colgada paró el brazo una hora (medido).
    assert GeminiEspia.ultimo["timeout"] == 120.0
    assert OllamaEspia.ultimo == {}


def test_un_groq_va_por_el_protocolo_de_openai_sin_el_prefijo(espias: None) -> None:
    ajustes = Settings(GROQ_API_KEY="clave-groq", BYTE_GROQ_NUM_PREDICT=8192)

    llm = build_llm(ajustes, "groq/openai/gpt-oss-120b", reasoning=True)

    # Con razonamiento se construye una SUBCLASE (la que rescata `reasoning`):
    # el espía guarda los argumentos en la clase de la instancia.
    assert isinstance(llm, OpenAIEspia) and type(llm) is not OpenAIEspia
    ultimo = type(llm).ultimo
    assert ultimo["base_url"] == GROQ_BASE_URL
    assert ultimo["api_key"] == "clave-groq"
    assert ultimo["model"] == "openai/gpt-oss-120b"
    assert ultimo["max_tokens"] == 8192
    assert ultimo["max_retries"] == 1
    assert ultimo["timeout"] == 120.0
    # En el cuerpo del pedido: como argumento del SDK lo rechaza (medido).
    assert ultimo["extra_body"] == {"reasoning_format": "parsed"}
    assert "model_kwargs" not in ultimo

    sin = build_llm(ajustes, "groq/openai/gpt-oss-20b")
    assert type(sin) is OpenAIEspia and OpenAIEspia.ultimo["extra_body"] is None
    assert GeminiEspia.ultimo == {} and OllamaEspia.ultimo == {}


def test_la_subclase_de_groq_rescata_el_reasoning_del_delta() -> None:
    """Groq manda el pensamiento en `delta.reasoning`; langchain-openai lo tiraba."""
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    from agent.llm import _con_razonamiento_de_groq

    class _Base:
        def _convert_chunk_to_generation_chunk(
            self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
        ) -> ChatGenerationChunk:
            return ChatGenerationChunk(message=AIMessageChunk(content=""))

    clase = _con_razonamiento_de_groq(_Base)
    trozo = {"choices": [{"delta": {"content": None, "reasoning": "miro el rango"}}]}
    generado = clase()._convert_chunk_to_generation_chunk(trozo, AIMessageChunk, None)
    assert generado.message.additional_kwargs["reasoning"] == "miro el rango"

    sin = {"choices": [{"delta": {"content": "hola"}}]}
    assert (
        "reasoning"
        not in clase()
        ._convert_chunk_to_generation_chunk(sin, AIMessageChunk, None)
        .message.additional_kwargs
    )


def test_es_remoto_solo_con_prefijo_o_gemini() -> None:
    assert es_remoto("groq/llama-3.3-70b-versatile")
    assert es_remoto("gemini-3.8-flash")
    assert not es_remoto("qwen3:14b")
    assert not es_remoto("llama3.3:70b")


def test_sin_clave_de_groq_no_se_arma(espias: None) -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_llm(Settings(GROQ_API_KEY=""), "groq/openai/gpt-oss-120b")


def test_sin_clave_no_se_arma(espias: None) -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_llm(Settings(GEMINI_API_KEY=""), "gemini-3.8-flash")


def test_el_local_sigue_igual(espias: None) -> None:
    ajustes = Settings(OLLAMA_MODEL="qwen3:14b", GEMINI_API_KEY="clave")

    build_llm(ajustes, num_ctx=12288)

    assert OllamaEspia.ultimo["model"] == "qwen3:14b"
    assert OllamaEspia.ultimo["num_ctx"] == 12288
    assert GeminiEspia.ultimo == {}


def test_el_local_tambien_tiene_tope_por_llamada(espias: None) -> None:
    """Medido el 2026-09-16: el runner murió con un sueño térmico y el vigía
    esperó UNA HORA una respuesta que no iba a llegar."""
    from agent.llm import TIMEOUT_LOCAL_S

    build_llm(Settings(OLLAMA_MODEL="qwen3:14b"))

    assert OllamaEspia.ultimo["client_kwargs"] == {"timeout": TIMEOUT_LOCAL_S}
    # Generoso a propósito: el 14B tarda minutos cuando piensa.
    assert TIMEOUT_LOCAL_S >= 600


def test_un_cerebras_va_por_el_protocolo_de_openai_sin_el_prefijo(espias: None) -> None:
    """El tercer brazo del experimento en papel: `cerebras/<id>` sale a Cerebras
    y no a Ollama. Sin el prefijo quitado, se pediría un modelo que se llama
    "cerebras/llama-…" y que no existe en ningún catálogo.
    """
    ajustes = Settings(CEREBRAS_API_KEY="clave-cerebras", BYTE_CEREBRAS_NUM_PREDICT=8192)

    llm = build_llm(ajustes, "cerebras/llama-3.3-70b")

    assert isinstance(llm, OpenAIEspia)
    ultimo = OpenAIEspia.ultimo
    assert ultimo["base_url"] == CEREBRAS_BASE_URL
    assert ultimo["api_key"] == "clave-cerebras"
    assert ultimo["model"] == "llama-3.3-70b"
    assert ultimo["max_tokens"] == 8192
    # Un solo intento: ante un 429 el relevo turna los modelos del brazo.
    assert ultimo["max_retries"] == 1
    assert ultimo["timeout"] == 120.0
    assert OllamaEspia.ultimo == {}


def test_a_cerebras_no_se_le_pide_el_reasoning_de_groq(espias: None) -> None:
    """`reasoning_format` es propio de Groq y de los gpt-oss. Los Llama y Qwen
    de Cerebras no separan el pensamiento del texto, así que mandarlo haría que
    el servidor reciba un campo que no conoce — el mismo error que ya costó una
    tarde con `model_kwargs` en Groq.
    """
    ajustes = Settings(CEREBRAS_API_KEY="clave-cerebras")

    build_llm(ajustes, "cerebras/qwen-3-32b", reasoning=True)

    assert OpenAIEspia.ultimo.get("extra_body") is None
    assert "reasoning_format" not in str(OpenAIEspia.ultimo)


def test_sin_clave_de_cerebras_no_se_arma(espias: None) -> None:
    """Falla al construirlo y no en la primera llamada: un brazo que arranca sin
    clave gasta una vuelta entera del vigía para descubrir que no puede pedir
    nada.
    """
    with pytest.raises(ValueError, match="CEREBRAS_API_KEY"):
        build_llm(Settings(CEREBRAS_API_KEY=""), "cerebras/llama-3.3-70b")


def test_un_nvidia_va_por_el_protocolo_de_openai_y_conserva_la_barra_interna(
    espias: None,
) -> None:
    """⚠ EL ID DE NIM LLEVA BARRA DENTRO, y eso es lo que este test protege.

    `deepseek-ai/deepseek-v4-flash-0731` es el id real —el que contestó 200 al
    sondeo del 2026-09-17—, así que el modelo completo del brazo es
    `nvidia/deepseek-ai/deepseek-v4-flash-0731`. Quitar el prefijo tiene que
    dejar la barra interna intacta: un `split("/")` o un `removeprefix` de más
    pediría `deepseek-v4-flash-0731` a secas, que no existe, y el 404 no rota en
    el relevo hasta que se le enseñó que es permanente.
    """
    ajustes = Settings(NVIDIA_NIM_API_KEY="clave-nvidia", BYTE_NVIDIA_NUM_PREDICT=8192)

    llm = build_llm(ajustes, "nvidia/deepseek-ai/deepseek-v4-flash-0731")

    assert isinstance(llm, OpenAIEspia)
    ultimo = OpenAIEspia.ultimo
    assert ultimo["base_url"] == NVIDIA_BASE_URL
    assert ultimo["api_key"] == "clave-nvidia"
    assert ultimo["model"] == "deepseek-ai/deepseek-v4-flash-0731"
    assert ultimo["max_tokens"] == 8192
    assert ultimo["max_retries"] == 1
    assert ultimo["timeout"] == 120.0
    assert OllamaEspia.ultimo == {}


def test_a_nvidia_no_se_le_pide_el_reasoning_de_groq(espias: None) -> None:
    """`reasoning_format` es propio de Groq y de los gpt-oss. Mandarlo a NIM haría
    que el servidor reciba un campo que no conoce — el error que ya costó una
    tarde con `model_kwargs`.
    """
    build_llm(
        Settings(NVIDIA_NIM_API_KEY="clave-nvidia"), "nvidia/qwen/qwen3-next-80b", reasoning=True
    )

    assert OpenAIEspia.ultimo.get("extra_body") is None
    assert "reasoning_format" not in str(OpenAIEspia.ultimo)


def test_la_clave_de_nvidia_se_lee_del_nombre_que_esta_en_railway() -> None:
    """⚠ `NVIDIA_NIM_API_KEY`, NO `NVIDIA_API_KEY`. Es el nombre con el que la
    clave está puesta en Railway; con otro alias el brazo arrancaría con la clave
    vacía y gastaría la jornada descubriéndolo. Es el fallo del 2026-09-15 con
    `GEMINI_API_KEY` contra `GEMINI_API_KEY_GRATIS`.
    """
    assert Settings(NVIDIA_NIM_API_KEY="de-railway").nvidia_api_key == "de-railway"


def test_sin_clave_de_nvidia_no_se_arma(espias: None) -> None:
    """Falla al construirlo y no en la primera llamada: un brazo que arranca sin
    clave gasta una vuelta entera del vigía para descubrir que no puede pedir nada.
    """
    with pytest.raises(ValueError, match="NVIDIA_NIM_API_KEY"):
        build_llm(Settings(NVIDIA_NIM_API_KEY=""), "nvidia/deepseek-ai/deepseek-v4-flash-0731")


def test_un_nvidia_cuenta_como_brazo_remoto() -> None:
    """`es_remoto` decide qué sella cada escritura y qué esperas aplica el relevo.
    Si NIM no entra ahí, sus operaciones quedarían marcadas como del modelo local
    y la comparación entre brazos mediría cualquier cosa.
    """
    from agent.llm import es_remoto

    assert es_remoto("nvidia/deepseek-ai/deepseek-v4-flash-0731") is True
    assert es_remoto("qwen3:14b") is False


def test_un_mistral_va_por_el_protocolo_de_openai_sin_el_prefijo(espias: None) -> None:
    """La familia Mistral no está en ningún otro brazo. Sin quitar el prefijo se
    pediría un modelo llamado "mistral/mistral-large-latest", que no existe.
    """
    ajustes = Settings(MISTRAL_API_KEY="clave-mistral", BYTE_MISTRAL_NUM_PREDICT=8192)

    llm = build_llm(ajustes, "mistral/mistral-large-latest")

    assert isinstance(llm, OpenAIEspia)
    ultimo = OpenAIEspia.ultimo
    assert ultimo["base_url"] == MISTRAL_BASE_URL
    assert ultimo["api_key"] == "clave-mistral"
    assert ultimo["model"] == "mistral-large-latest"
    assert ultimo["max_tokens"] == 8192
    # Un solo intento, y acá más que en ningún otro: la capa gratuita de Mistral
    # es la más estrecha de las cuatro y un reintento del SDK se comería la cuota
    # del minuto sin dejar que el relevo cambie de modelo.
    assert ultimo["max_retries"] == 1
    assert OllamaEspia.ultimo == {}


def test_openrouter_conserva_el_sufijo_free_porque_es_lo_que_decide_si_cuesta(
    espias: None,
) -> None:
    """⚠ EL TEST QUE IMPIDE UNA FACTURA.

    `deepseek/deepseek-chat-v3-0324:free` y el mismo id sin `:free` son la misma
    familia y facturas distintas. El id lleva barra Y dos puntos, así que el
    modelo del brazo es `openrouter/deepseek/deepseek-chat-v3-0324:free`: quitar
    el prefijo tiene que dejar los dos intactos. Cualquier limpieza «de más»
    —un `split(":")`, un `split("/")`— se convierte en dinero, y la regla del
    proyecto es no pagar una IA antes de saber si es rentable.
    """
    ajustes = Settings(OPENROUTER_API_KEY="clave-or", BYTE_OPENROUTER_NUM_PREDICT=8192)

    llm = build_llm(ajustes, "openrouter/deepseek/deepseek-chat-v3-0324:free")

    assert isinstance(llm, OpenAIEspia)
    ultimo = OpenAIEspia.ultimo
    assert ultimo["base_url"] == OPENROUTER_BASE_URL
    assert ultimo["model"] == "deepseek/deepseek-chat-v3-0324:free"
    assert ultimo["model"].endswith(":free")
    assert ultimo["api_key"] == "clave-or"


def test_ni_mistral_ni_openrouter_llevan_el_reasoning_de_groq(espias: None) -> None:
    """`reasoning_format` es propio de Groq y de los gpt-oss. OpenRouter enruta a
    docenas de modelos y la mayoría no lo conoce; mandarlo haría que el servidor
    reciba un campo que no entiende, el error que ya costó una tarde.
    """
    build_llm(Settings(MISTRAL_API_KEY="k"), "mistral/mistral-large-latest", reasoning=True)
    assert OpenAIEspia.ultimo.get("extra_body") is None

    build_llm(Settings(OPENROUTER_API_KEY="k"), "openrouter/qwen/qwen3-8b:free", reasoning=True)
    assert OpenAIEspia.ultimo.get("extra_body") is None
    assert "reasoning_format" not in str(OpenAIEspia.ultimo)


def test_sin_clave_no_se_arma_ni_mistral_ni_openrouter(espias: None) -> None:
    """Falla al construirlo y no en la primera llamada: un brazo que arranca sin
    clave gasta una vuelta entera del vigía para descubrir que no puede pedir nada.
    """
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        build_llm(Settings(MISTRAL_API_KEY=""), "mistral/mistral-large-latest")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_llm(Settings(OPENROUTER_API_KEY=""), "openrouter/qwen/qwen3-8b:free")


def test_mistral_y_openrouter_cuentan_como_brazos_remotos() -> None:
    """`es_remoto` decide qué sella cada escritura. Si no entran ahí, sus
    operaciones quedarían marcadas como del modelo local y la comparación entre
    brazos mediría cualquier cosa.
    """
    from agent.llm import es_remoto

    assert es_remoto("mistral/mistral-large-latest") is True
    assert es_remoto("openrouter/deepseek/deepseek-chat-v3-0324:free") is True
    assert es_remoto("qwen3:14b") is False


def test_un_cerebras_cuenta_como_brazo_remoto() -> None:
    """`es_remoto` decide qué sella cada escritura y qué esperas aplica el
    relevo. Si Cerebras no entra ahí, sus operaciones quedarían marcadas como
    del modelo local y la comparación entre brazos mediría cualquier cosa.
    """
    from agent.llm import es_remoto

    assert es_remoto("cerebras/llama-3.3-70b") is True
    assert es_remoto("qwen3:14b") is False
