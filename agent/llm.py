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


def es_de_cerebras(nombre: str) -> bool:
    """`cerebras/<id>` se pide a Cerebras, también por el protocolo de OpenAI.

    Un tercer brazo vale por traer una familia distinta, no un modelo más: los
    otros dos son Gemini y los gpt-oss de Groq, y Cerebras sirve Llama y Qwen
    (`paper/CRITERIO_COMPARACION.md`). El prefijo se quita antes de pedirlo.
    """
    return nombre.startswith("cerebras/")


def es_de_nvidia(nombre: str) -> bool:
    """`nvidia/<id>` se pide a NVIDIA NIM, también por el protocolo de OpenAI.

    Vale por la familia: NIM sirve DeepSeek, que ni Gemini ni los gpt-oss de
    Groq tienen (`paper/CRITERIO_COMPARACION.md`, un brazo nuevo vale por la
    familia que trae y no por el modelo).

    ⚠ EL ID DE NIM LLEVA BARRA DENTRO (`deepseek-ai/deepseek-v4-flash-0731`),
    así que el modelo completo queda `nvidia/deepseek-ai/deepseek-v4-…`. Quitar
    el prefijo deja la barra interna intacta, que es la que el servidor espera.
    """
    return nombre.startswith("nvidia/")


def es_de_mistral(nombre: str) -> bool:
    """`mistral/<id>` se pide a Mistral, otra vez por el protocolo de OpenAI.

    Trae la familia Mistral, que no está en ninguno de los otros brazos. Su capa
    gratuita es la más estrecha de las cuatro (del orden de una petición por
    minuto), así que el brazo aguanta las 8 vueltas del día y poco más.
    """
    return nombre.startswith("mistral/")


def es_de_openrouter(nombre: str) -> bool:
    """`openrouter/<id>` sale por OpenRouter, que es un intermediario y no un
    proveedor: una clave para muchas familias.

    ⚠ SUS IDS LLEVAN BARRA Y DOS PUNTOS (`deepseek/deepseek-chat-v3-0324:free`),
    así que el modelo del brazo queda `openrouter/deepseek/deepseek-…:free`. El
    `:free` NO es decorativo: marca la variante sin coste, y sin él la misma
    petición se cobra. Quitar solo el prefijo deja los dos intactos.

    ⚠ Y SU CATÁLOGO GRATIS CAMBIA SIN AVISAR: un modelo `:free` hoy puede dejar
    de serlo mañana y el brazo se queda con un id que ya no existe. Por eso se
    sondea antes (`scripts/sondear-proveedor.sh openrouter` del dashboard) y por
    eso el relevo manda un 404 a cuarentena permanente en vez de morir con él.
    """
    return nombre.startswith("openrouter/")


def es_remoto(nombre: str) -> bool:
    """Todo lo que no es Ollama. Un brazo remoto puede mezclar proveedores: cada
    escritura queda sellada con el modelo que la hizo (paper/CRITERIO_COMPARACION.md)."""
    return (
        es_de_google(nombre)
        or es_de_groq(nombre)
        or es_de_cerebras(nombre)
        or es_de_nvidia(nombre)
        or es_de_mistral(nombre)
        or es_de_openrouter(nombre)
    )


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
    if es_de_cerebras(nombre):
        return _cerebras(settings, nombre)
    if es_de_nvidia(nombre):
        return _nvidia(settings, nombre)
    if es_de_mistral(nombre):
        return _mistral(settings, nombre)
    if es_de_openrouter(nombre):
        return _openrouter(settings, nombre)

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
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

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


def _cerebras(settings: Settings, nombre: str) -> Any:
    """Cerebras por el protocolo de OpenAI, igual que Groq pero sin `reasoning`.

    No lleva `reasoning_format`: eso es propio de Groq y de los gpt-oss. Los
    Llama y Qwen que sirve Cerebras no separan el pensamiento del texto, así
    que pedirlo haría que el SDK mande un campo que el servidor no conoce.

    `max_retries=1` por lo mismo que en los otros dos: ante un 429 el reintento
    lo hace el relevo, que sabe turnar los modelos de la lista del brazo.
    """
    if not settings.cerebras_api_key:
        raise ValueError(f"CEREBRAS_API_KEY vacía: no se puede armar {nombre}")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=CEREBRAS_BASE_URL,
        api_key=settings.cerebras_api_key,
        model=nombre.removeprefix("cerebras/"),
        temperature=0.2,
        max_tokens=settings.cerebras_num_predict,
        max_retries=1,
        timeout=TIMEOUT_REMOTO_S,
    )


def _nvidia(settings: Settings, nombre: str) -> Any:
    """NVIDIA NIM por el protocolo de OpenAI, igual que Cerebras y sin `reasoning`.

    ⚠ LA CLAVE SE LLAMA `NVIDIA_NIM_API_KEY`, NO `NVIDIA_API_KEY`. Es el nombre
    con el que está puesta en Railway, y un alias distinto dejaría el brazo
    arrancando con la clave vacía — el mismo fallo que el `GEMINI_API_KEY`
    contra `GEMINI_API_KEY_GRATIS` del 2026-09-15, que costó una jornada.

    ⚠ Y EL CATÁLOGO DE NIM NO ES EL DE LA CUENTA. Sondeado el 2026-09-17 desde el
    contenedor de Railway: `/v1/models` devolvió 82 modelos y el primero de ellos
    contestó 404 «Function …: Not found for account …». O sea que NIM lista el
    catálogo PÚBLICO y la cuenta tiene habilitado otro subconjunto — peor que
    Cerebras, donde `/v1/models` sí era de la clave. El que sí contestó 200 fue
    `deepseek-ai/deepseek-v4-flash-0731`. Antes de tocar la lista de modelos de
    este brazo, correr `scripts/sondear-proveedor.sh nvidia` del dashboard, que
    prueba los candidatos EN ORDEN hasta que uno conteste.

    Sin `reasoning_format`: eso es propio de Groq y de los gpt-oss. Mandarlo haría
    que el servidor reciba un campo que no conoce.

    `max_retries=1` por lo mismo que en los otros tres: ante un 429 el reintento
    lo hace el relevo, que sabe turnar los modelos de la lista del brazo.
    """
    if not settings.nvidia_api_key:
        raise ValueError(f"NVIDIA_NIM_API_KEY vacía: no se puede armar {nombre}")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=settings.nvidia_api_key,
        model=nombre.removeprefix("nvidia/"),
        temperature=0.2,
        max_tokens=settings.nvidia_num_predict,
        max_retries=1,
        timeout=TIMEOUT_REMOTO_S,
    )


def _mistral(settings: Settings, nombre: str) -> Any:
    """Mistral por el protocolo de OpenAI. Sin `reasoning`, como Cerebras y NIM.

    `max_retries=1` por lo de siempre: ante un 429 turna el relevo. Y acá importa
    más que en los otros, porque la capa gratuita de Mistral es la más estrecha
    de las cuatro: un reintento del SDK se comería la cuota del minuto sin que el
    relevo llegue a cambiar de modelo.
    """
    if not settings.mistral_api_key:
        raise ValueError(f"MISTRAL_API_KEY vacía: no se puede armar {nombre}")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=MISTRAL_BASE_URL,
        api_key=settings.mistral_api_key,
        model=nombre.removeprefix("mistral/"),
        temperature=0.2,
        max_tokens=settings.mistral_num_predict,
        max_retries=1,
        timeout=TIMEOUT_REMOTO_S,
    )


def _openrouter(settings: Settings, nombre: str) -> Any:
    """OpenRouter por el protocolo de OpenAI. Un intermediario, no un proveedor.

    ⚠ EL `:free` DEL ID VIAJA TAL CUAL Y ES LO QUE DECIDE SI ESTO CUESTA DINERO.
    `deepseek/deepseek-chat-v3-0324:free` y el mismo id sin sufijo son la misma
    familia y facturas distintas, y la regla del proyecto es no pagar una IA antes
    de saber si es rentable. `removeprefix("openrouter/")` no toca ni la barra
    interna ni el sufijo; cualquier limpieza «de más» aquí se convierte en una
    factura.

    Sin `reasoning_format`: es propio de Groq. OpenRouter enruta a docenas de
    modelos y la mayoría no lo conoce.
    """
    if not settings.openrouter_api_key:
        raise ValueError(f"OPENROUTER_API_KEY vacía: no se puede armar {nombre}")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=settings.openrouter_api_key,
        model=nombre.removeprefix("openrouter/"),
        temperature=0.2,
        max_tokens=settings.openrouter_num_predict,
        max_retries=1,
        timeout=TIMEOUT_REMOTO_S,
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
