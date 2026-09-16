"""Construcción del modelo (Ollama, o Gemini por API) y chequeo de que esté vivo."""

from typing import Any

import httpx

from api.config import Settings
from api.logging import get_logger

logger = get_logger("agent.llm")

# El tope por llamada al modelo LOCAL, mucho más generoso que el de los remotos
# (TIMEOUT_REMOTO_S, más abajo): el 14B piensa en la GPU de la Mac y una
# respuesta suya tarda minutos. Ver `build_llm`.
TIMEOUT_LOCAL_S = 900.0


def es_de_google(nombre: str) -> bool:
    """Un `gemini-…` se pide a la API de Google.

    Solo `gemini`: los Gemma también corren en Ollama (`gemma3:4b`) y no se
    quiere que un nombre local se vaya a la red por parecerse.
    """
    return nombre.startswith("gemini")


def es_de_groq(nombre: str) -> bool:
    """`groq/<id>` se pide a Groq por el protocolo de OpenAI; el prefijo se quita."""
    return nombre.startswith("groq/")


def es_remoto(nombre: str) -> bool:
    """Todo lo que no es Ollama. Un brazo remoto puede mezclar proveedores: cada
    escritura queda sellada con el modelo que la hizo (paper/CRITERIO_COMPARACION.md)."""
    return es_de_google(nombre) or es_de_groq(nombre)


def build_llm(
    settings: Settings,
    modelo: str = "",
    *,
    reasoning: bool = False,
    num_predict: int | None = None,
    num_ctx: int | None = None,
    keep_alive: str | None = None,
) -> Any:
    """ChatOllama con el contexto y el tope de tokens explícitos.

    Ollama arranca en 4.096 tokens de contexto aunque el modelo soporte más, así
    que `num_ctx` se setea siempre. `num_predict` acota el gasto por respuesta.

    `modelo` permite armar un cliente para un modelo alternativo sin tocar la
    configuración: es lo que usa el cambio en caliente.

    ⚠ `reasoning` VIENE APAGADO Y ASÍ SE QUEDA PARA LA API. Encenderlo cambia el
    coste de cada respuesta, no solo su calidad: medido el 2026-09-14 con
    `qwen3:14b`, la misma pregunta pasó de 88 s a 279 s —de 1,5 a 4,6 minutos— y
    el pensamiento se comió los 1024 tokens de `num_predict` enteros, dejando la
    respuesta VACÍA. Un agente que piensa y no llega a emitir la llamada a la
    herramienta es peor que uno que no piensa.

    Por eso quien lo encienda tiene que subir también `num_predict`: son un par,
    no dos ajustes independientes. El agente en papel lo hace en `paper/sesion.py`;
    la API no lo usa, y su latencia sigue igual que antes.
    """
    nombre = modelo or settings.ollama_model
    if es_de_google(nombre):
        return _gemini(settings, nombre, reasoning=reasoning)
    if es_de_groq(nombre):
        return _groq(settings, nombre, reasoning=reasoning)

    from langchain_ollama import ChatOllama

    # ⚠ `keep_alive` LO PONE QUIEN SABE CUÁNTO VA A TARDAR EN VOLVER. El
    # default de Ollama son 5 minutos y las vueltas del vigía distan horas: el
    # 14B se descargaba entre vueltas y cada una pagaba la carga en frío más la
    # evaluación completa del prefijo (~5k tokens a ~90 tok/s ≈ 1 min, medido
    # el 2026-09-16 en el log de Ollama). El vigía lo sube dentro de su
    # ventana; la API no lo toca y sigue soltando el modelo como siempre.
    extra = {"keep_alive": keep_alive} if keep_alive else {}
    # ⚠ TOPE TAMBIÉN EN EL LOCAL, Y NO ES SIMETRÍA: ES UN FALLO MEDIDO. El
    # 2026-09-16 la Mac se durmió por emergencia térmica en mitad de una vuelta
    # (`Dark Wake Thermal Emergency`, 11 min); el runner de Ollama murió con
    # ella y la petición HTTP del vigía quedó esperando una respuesta que nunca
    # llegó: UNA HORA sin sondear, sin resolver predicciones y sin un error que
    # mirar, con el proceso vivo al 0,3 % de CPU. Los brazos remotos ya tenían
    # su tope (TIMEOUT_REMOTO_S); este es el mismo remedio para el que faltaba.
    #
    # Quince minutos, no dos: el 14B tarda 5-12 min por respuesta cuando piensa
    # —medido—, así que un tope corto cortaría vueltas buenas. Lo que esto
    # corta es lo que no va a terminar nunca.
    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=modelo or settings.ollama_model,
        num_ctx=num_ctx or settings.ollama_num_ctx,
        num_predict=num_predict or settings.ollama_num_predict,
        temperature=0.2,
        reasoning=reasoning,
        client_kwargs={"timeout": TIMEOUT_LOCAL_S},
        **extra,
    )


def _gemini(settings: Settings, nombre: str, *, reasoning: bool) -> Any:
    """El brazo remoto de la comparación. Ver paper/CRITERIO_COMPARACION.md.

    `reasoning` acá no enciende el pensamiento —los Flash 3.x piensan solos— sino
    que pide VERLO (`include_thoughts`): llega como bloques `thinking` en el
    contenido, que el grafo emite igual que el `reasoning_content` de Ollama.

    `max_retries=1`: un solo intento. El reintento ante 429/5xx lo hace el
    relevo cambiando de modelo, que es más útil que insistirle al agotado.

    `num_ctx` y `num_predict` no aplican: el contexto es de un millón y el tope
    de salida tiene el suyo (`gemini_num_predict`, con la medición).
    """
    if not settings.gemini_api_key:
        raise ValueError(f"GEMINI_API_KEY vacía: no se puede armar {nombre}")
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=nombre,
        google_api_key=settings.gemini_api_key,
        temperature=0.2,
        max_output_tokens=settings.gemini_num_predict,
        include_thoughts=True if reasoning else None,
        max_retries=1,
        timeout=TIMEOUT_REMOTO_S,
    )


GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# ⚠ SIN ESTO UNA LLAMADA COLGADA PARA EL BRAZO ENTERO. Medido el 2026-09-15:
# tras dos 503, la llamada siguiente de Gemini se quedó esperando UNA HORA sin
# respuesta —los clientes no traen tope— y el vigía no sondeó ni resolvió nada
# hasta que lo pararon. Dos minutos sobra para una respuesta (las reales tardan
# segundos); pasado eso el relevo lo trata como caída y pasa al siguiente.
TIMEOUT_REMOTO_S = 120.0


def _con_razonamiento_de_groq(base: Any) -> Any:
    """Una subclase de ChatOpenAI que conserva el `reasoning` del delta de Groq.

    `langchain-openai` no extrae campos de razonamiento propios de un
    proveedor —lo dice su propia cabecera: «use a provider-specific
    subclass»—. Groq, con `reasoning_format="parsed"`, manda el pensamiento
    de gpt-oss en `delta.reasoning`; sin esto se perdía y la traza enseñaba
    las llamadas sin el razonamiento que las precedió. Se crea sobre la clase
    real en tiempo de ejecución para que el import de `langchain_openai` siga
    siendo perezoso (la API no lo paga).
    """

    class ChatOpenAIConRazonamiento(base):  # type: ignore[misc, valid-type]
        def _convert_chunk_to_generation_chunk(
            self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
        ) -> Any:
            generado = super()._convert_chunk_to_generation_chunk(
                chunk, default_chunk_class, base_generation_info
            )
            elecciones = chunk.get("choices") or []
            delta = (elecciones[0] or {}).get("delta") if elecciones else None
            pensamiento = (delta or {}).get("reasoning") if isinstance(delta, dict) else None
            if generado is not None and pensamiento:
                generado.message.additional_kwargs["reasoning"] = str(pensamiento)
            return generado

    return ChatOpenAIConRazonamiento


def _groq(settings: Settings, nombre: str, *, reasoning: bool) -> Any:
    """Groq por el protocolo de OpenAI: una dependencia que sirve para varios proveedores.

    `reasoning_format="parsed"` es de Groq: el pensamiento de gpt-oss viaja
    aparte del texto (`reasoning`), y el grafo lo emite a la traza como el de
    Ollama. Sin él vendría dentro del texto entre etiquetas.

    ⚠ VA EN `extra_body`, NO EN `model_kwargs`. Medido el 2026-09-15: los
    `model_kwargs` se pasan como argumentos al SDK de OpenAI, que rechaza los
    que no conoce («unexpected keyword argument 'reasoning_format'»); lo que es
    propio de un proveedor viaja en el cuerpo del pedido.

    `max_retries=1` por lo mismo que en Gemini: el reintento lo hace el relevo.
    """
    if not settings.groq_api_key:
        raise ValueError(f"GROQ_API_KEY vacía: no se puede armar {nombre}")
    from langchain_openai import ChatOpenAI

    extra: dict[str, Any] = {}
    clase: Any = ChatOpenAI
    if reasoning:
        extra["reasoning_format"] = "parsed"
        clase = _con_razonamiento_de_groq(ChatOpenAI)
    return clase(
        base_url=GROQ_BASE_URL,
        api_key=settings.groq_api_key,
        model=nombre.removeprefix("groq/"),
        temperature=0.2,
        max_tokens=settings.groq_num_predict,
        max_retries=1,
        timeout=TIMEOUT_REMOTO_S,
        extra_body=extra or None,
    )


async def ollama_status(base_url: str, model: str, timeout_s: float = 2.0) -> str:
    """ "ok" si responde y tiene el modelo; "sin_modelo" / "caido" si no.

    Alimenta /health/details, que es lo que lee el indicador "En línea" de la UI.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            tags = response.json().get("models", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ollama_no_responde", error_type=type(exc).__name__)
        return "caido"
    # Ollama devuelve "qwen2.5-coder:7b"; se compara sin el tag para ser tolerante.
    wanted = model.split(":")[0]
    available = {str(tag.get("name", "")).split(":")[0] for tag in tags}
    return "ok" if wanted in available else "sin_modelo"
