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
