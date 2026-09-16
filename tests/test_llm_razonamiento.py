"""El razonamiento se pide por llamada, no por configuración global.

Lo que estos tests protegen es un PAR, no dos ajustes sueltos: encender el
pensamiento sin subir el presupuesto de tokens deja al modelo pensando y sin
emitir la llamada a la herramienta. Medido el 2026-09-14 con `qwen3:14b`: el
pensamiento ocupó 3.389 caracteres, agotó los 1024 de `ollama_num_predict` y la
respuesta salió VACÍA. Un agente que piensa y no llega a actuar es peor que uno
que no piensa.

Y protegen que la API NO lo pague: comparte `build_llm` con el agente en papel,
y la misma pregunta pasa de 88 s a 279 s con el pensamiento encendido.
"""

from typing import Any

import pytest

from agent.llm import build_llm
from api.config import Settings


class ChatOllamaEspia:
    """Doble de `ChatOllama` que solo guarda con qué lo construyeron."""

    ultimo: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        ChatOllamaEspia.ultimo = kwargs


@pytest.fixture
def espia(monkeypatch: pytest.MonkeyPatch) -> type[ChatOllamaEspia]:
    # `build_llm` importa ChatOllama DENTRO de la función, así que hay que
    # parchear el módulo de origen y no un símbolo ya importado.
    import langchain_ollama

    monkeypatch.setattr(langchain_ollama, "ChatOllama", ChatOllamaEspia)
    ChatOllamaEspia.ultimo = {}
    return ChatOllamaEspia


def test_la_api_no_paga_el_razonamiento(espia: type[ChatOllamaEspia]) -> None:
    """Una llamada pelada —la que hace la API— sigue sin pensar y con 1024."""
    ajustes = Settings(OLLAMA_NUM_PREDICT=1024)

    build_llm(ajustes)

    assert espia.ultimo["reasoning"] is False
    assert espia.ultimo["num_predict"] == 1024


def test_el_agente_en_papel_piensa_y_con_mas_presupuesto(
    espia: type[ChatOllamaEspia],
) -> None:
    """Lo que pide `paper/sesion.py`: pensamiento encendido y tokens de sobra."""
    ajustes = Settings(BYTE_PAPER_REASONING=True, BYTE_PAPER_NUM_PREDICT=4096)

    build_llm(
        ajustes,
        reasoning=ajustes.paper_reasoning,
        num_predict=ajustes.paper_num_predict,
    )

    assert espia.ultimo["reasoning"] is True
    # El valor que pide el llamante, sea cual sea: acá 4096 por el `Settings` de
    # arriba. El default está en `test_el_presupuesto_de_pensamiento_deja_aire`.
    assert espia.ultimo["num_predict"] == 4096


# Las claves que estos tests miden. Se borran del entorno para leer el default
# DEL CÓDIGO, no el de la máquina donde se corra.
CLAVES = ("BYTE_PAPER_REASONING", "BYTE_PAPER_NUM_PREDICT", "OLLAMA_NUM_PREDICT")


def defaults_del_codigo(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """`Settings` sin el `.env` NI las variables que ese `.env` ya exportó.

    ⚠ NO BASTA CON `_env_file=None`, Y EL TEST MENTÍA EN LA DIRECCIÓN PEOR.
    `api/config.py` hace `load_dotenv(override=False)` al importarse, así que el
    `.env` ya está en `os.environ` para cuando `Settings` se construye: pedirle
    que ignore el fichero no borra lo que el fichero dejó puesto. Medido: con
    `_env_file=None` y `BYTE_PAPER_REASONING=true` en el `.env`, seguía
    devolviendo True.

    Sin esto, en la máquina donde se encendió el razonamiento el test del
    default fallaba, mientras que en CI —que no tiene `.env`— pasaba en verde.
    Un test que solo protege donde nadie lo mira no protege nada.

    `monkeypatch` deshace el borrado al terminar cada test, así que esto no
    contamina al resto de la suite.
    """
    for clave in CLAVES:
        monkeypatch.delenv(clave, raising=False)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_el_presupuesto_de_papel_no_puede_ser_menor_que_el_de_la_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El par no se desacopla: pensar con el presupuesto de la API es el bug medido.

    No es una preferencia de números: con 1024 el pensamiento se come la
    respuesta entera. Si alguien baja `paper_num_predict` hasta el valor de la
    API, el razonamiento deja de servir y falla en silencio —el modelo piensa,
    no emite herramienta, y la vuelta se va en el tope de iteraciones sin que
    nada lo explique—.
    """
    ajustes = defaults_del_codigo(monkeypatch)

    assert ajustes.paper_num_predict > ajustes.ollama_num_predict


def test_el_razonamiento_viene_apagado_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encenderlo es una decisión explícita, no algo que pase solo.

    Cuesta ~3x en latencia por respuesta: si algún día se vuelve el default,
    que sea porque alguien lo escribió, no porque se coló.
    """
    assert defaults_del_codigo(monkeypatch).paper_reasoning is False


def test_el_agente_en_papel_usa_su_propio_contexto(espia: type[ChatOllamaEspia]) -> None:
    """12K y no 16K: 16K dejaba una capa del 14B en CPU en un Mac de 16 GB y el
    modelo iba a 1,9 tok/s; con 12K entra entero y va a 7,3. La API no cambia."""
    ajustes = Settings(OLLAMA_NUM_CTX=16384, BYTE_PAPER_NUM_CTX=12288)

    build_llm(ajustes, num_ctx=ajustes.paper_num_ctx)
    assert espia.ultimo["num_ctx"] == 12288

    build_llm(ajustes)
    assert espia.ultimo["num_ctx"] == 16384


def test_el_vigia_pide_keep_alive_y_la_api_no(espia: type[ChatOllamaEspia]) -> None:
    """El default de Ollama son 5 min y las vueltas del vigía distan horas: el
    14B se descargaba entre vueltas y cada una pagaba la carga en frío más la
    evaluación completa del prefijo (~1 min, medido el 2026-09-16 en su log).
    La API sigue soltando el modelo como siempre: su latencia no paga por esto."""
    ajustes = Settings(BYTE_PAPER_KEEP_ALIVE="4h")

    build_llm(ajustes, keep_alive=ajustes.paper_keep_alive)
    assert espia.ultimo["keep_alive"] == "4h"

    build_llm(ajustes)
    assert "keep_alive" not in espia.ultimo


def test_el_presupuesto_de_pensamiento_deja_aire_al_contexto() -> None:
    """Medido en el log de Ollama: pico de 11.572 tokens de 12.288 (94 %), con el
    pensamiento entre 836 y 1.083 por iteración. Con `num_predict` a 4096 el
    margen eran ~700 tokens, y un pensamiento largo en la última iteración habría
    provocado un `context shift` silencioso: Ollama tira el PRINCIPIO —la
    instrucción—. El tope de salida tiene que caber de sobra en lo que queda."""
    ajustes = Settings()

    assert ajustes.paper_num_predict == 3072
    # El pensamiento medido (≤1.100) cabe casi tres veces en el presupuesto...
    assert ajustes.paper_num_predict > 3 * 1000
    # ...y al prompt le queda más de lo que llegó a pedir (8,9K).
    assert ajustes.paper_num_ctx - ajustes.paper_num_predict > 8900
