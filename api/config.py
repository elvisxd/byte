"""Configuración de Byte. Todo se lee del entorno (o de .env en desarrollo)."""

from functools import lru_cache
from typing import Annotated, Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from models.schemas import HARD_MAX_MESSAGE_CHARS

# ⚠ EL .env TAMBIÉN TIENE QUE LLEGAR A `os.environ`, y no es redundante con el
# `env_file` de abajo: aquél solo puebla `Settings`, y hay código que lee el
# entorno directo porque no pasa por ahí — `paper/mercado.py` (BYTE_PAPER_SCRIPTS),
# `paper/sesion.py` (BYTE_PAPER_DB), `paper/trace.py` y `paper/publicar.py`
# (PANEL_URL, PANEL_TOKEN). Sin esto, esas cuatro variables escritas en el .env
# se ignoran: la sesión aborta con «falta BYTE_PAPER_SCRIPTS» y el SQLite cae al
# default relativo en vez de al repo de trading.
#
# No se notaba porque en el Codespace `devcontainer.json` las pone en el entorno
# por `remoteEnv`, así que allí funcionaba por coincidencia y no por diseño. En
# una máquina local no hay remoteEnv y el fallo aflora.
#
# `override=False` (el default) es lo que mantiene esa coincidencia sana: lo que
# ya está en el entorno gana sobre el archivo, así que el Codespace y `docker run
# -e` siguen mandando. Y al correr en el import, ocurre antes que los fixtures de
# los tests que limpian variables con monkeypatch: no se las reintroduce.
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Entorno ---
    env: Literal["dev", "prod"] = Field(default="dev", alias="BYTE_ENV")
    version: str = Field(default="0.1.0", alias="BYTE_VERSION")

    # --- Credenciales ---
    # La API key viaja en X-API-Key (CLI) o se canjea por una cookie httpOnly (web).
    # Nunca se guarda en claro: api.security la hashea al arrancar.
    api_key: str = Field(default="", alias="BYTE_API_KEY")
    # Firma de la cookie de sesión y de los events_token de 60 s.
    secret_key: str = Field(default="", alias="BYTE_SECRET_KEY")

    # --- Modelo ---
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5-coder:7b", alias="OLLAMA_MODEL")
    # Modelos entre los que se puede cambiar en caliente, separados por comas.
    # El de `OLLAMA_MODEL` siempre está disponible aunque no figure acá.
    #
    # No entran dos modelos a la vez en una máquina de 16 GB, así que Ollama
    # desaloja uno para cargar el otro y **cambiar cuesta unos 27 segundos**
    # (medido). Por eso el cambio es explícito —un comando del usuario— y no
    # automático: un clasificador que dude paga ese precio cada vez que cambia
    # de opinión, y encima sin que se vea por qué.
    ollama_models: str = Field(default="", alias="OLLAMA_MODELS_DISPONIBLES")
    # Ollama arranca en 4.096 tokens por defecto aunque el modelo soporte más: se setea explícito.
    ollama_num_ctx: int = Field(default=16384, alias="OLLAMA_NUM_CTX")
    # Tope de tokens por respuesta (denegación de billetera).
    ollama_num_predict: int = Field(default=1024, alias="OLLAMA_NUM_PREDICT")
    # --- Razonamiento, SOLO para el agente en papel ---
    # ⚠ VAN EN PAREJA. Encender el razonamiento sin subir el presupuesto de
    # tokens deja al modelo pensando y sin emitir la llamada a la herramienta:
    # medido el 2026-09-14 con qwen3:14b, el pensamiento ocupó 3.389 caracteres,
    # agotó los 1024 de `ollama_num_predict` y la respuesta salió VACÍA.
    # La API no los lee —usa los de arriba—, así que su latencia no cambia.
    paper_reasoning: bool = Field(default=False, alias="BYTE_PAPER_REASONING")
    # ⚠ 3072 Y NO 4096 DESDE EL 2026-09-16, PARA DARLE AIRE AL CONTEXTO. Medido
    # en el log de Ollama: el pico de una vuelta fue 11.572 tokens de 12.288
    # —el 94 %—, con el pensamiento entre 836 y 1.083 tokens por iteración. Los
    # 4.096 no se usaban y, si una vuelta pensara largo en la última iteración,
    # Ollama haría un `context shift` silencioso y tiraría el principio del
    # contexto: la instrucción. Con 3.072 sobra margen y el pensamiento medido
    # cabe tres veces.
    paper_num_predict: int = Field(default=3072, alias="BYTE_PAPER_NUM_PREDICT")
    # Cuánto se queda el modelo cargado en Ollama entre vueltas del vigía. El
    # default son 5 min y las vueltas distan horas. Ver `build_llm`.
    paper_keep_alive: str = Field(default="4h", alias="BYTE_PAPER_KEEP_ALIVE")
    # ⚠ 12K Y NO 16K, PORQUE 16K NO CABE EN LA GPU DE UN MAC DE 16 GB. Medido el
    # 2026-09-14: con 16K, Ollama dejaba una capa del 14B en CPU (40/41) y macOS
    # tenía 12-17 GB en swap; el modelo generaba a 1,9 tok/s. Con 12K entra
    # entero (41/41) y va a 7,3 tok/s: casi 4×. Una vuelta usa ~9-10K —prompt,
    # herramientas, mapa y seis iteraciones—, así que 12K sobra y 8K no cabría
    # (y 8K no fue más rápido). Solo lo usa el agente en papel.
    paper_num_ctx: int = Field(default=12288, alias="BYTE_PAPER_NUM_CTX")
    # El mapa de `mirar_mercado` son tres gráficos en una respuesta; con los
    # 4000 de la API el de 4h se recortaba. Solo lo usa el agente en papel.
    paper_max_tool_result_chars: int = Field(default=8000, alias="BYTE_PAPER_MAX_TOOL_RESULT_CHARS")
    # --- Gemini: SOLO el brazo remoto de la comparación (paper/CRITERIO_COMPARACION.md) ---
    # Capa gratuita. Vacía, `build_llm` se niega a armar un modelo de Google.
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    # ⚠ EL PENSAMIENTO CUENTA DENTRO DEL TOPE, Y GEMINI PIENSA LARGO. Medido el
    # 2026-09-15 con tope 512: 3.8-flash devolvió 20 tokens de respuesta y 488
    # de pensamiento —508, al borde—, y 3.7 y 3.5 quedaron igual de justos. Con
    # los 4096 del local, una vuelta que piense largo se quedaría sin sitio
    # para la llamada a la herramienta y fallaría en silencio: el mismo bug
    # medido con qwen3:14b y 1024 (ver `paper_num_predict`). No es adaptar el
    # experimento al modelo —el prompt y las herramientas son los mismos—, es
    # no cortarle la respuesta.
    gemini_num_predict: int = Field(default=8192, alias="BYTE_GEMINI_NUM_PREDICT")
    # Segundos entre llamadas: la capa gratuita tiene tope por minuto, y una
    # vuelta son hasta seis llamadas seguidas. Ver agent/relevo.py.
    gemini_espera_s: float = Field(default=12.0, alias="BYTE_GEMINI_ESPERA_S")
    # --- Groq: otro brazo remoto, capa gratuita, protocolo de OpenAI ---
    # Modelos `groq/<id>` (p. ej. `groq/openai/gpt-oss-120b`). Sin tarjeta: 30
    # peticiones/min y 1.000/día por modelo, medido en la doc el 2026-09-15.
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    # Cerebras: el tercer brazo del experimento en papel. Sirve Llama y Qwen por
    # el protocolo de OpenAI, así que trae una familia distinta a las otras dos
    # sin pedir una dependencia nueva.
    cerebras_api_key: str = Field(default="", alias="CEREBRAS_API_KEY")
    # Mismo razonamiento que `gemini_num_predict`: el pensamiento de gpt-oss
    # cuenta dentro del tope de salida.
    groq_num_predict: int = Field(default=8192, alias="BYTE_GROQ_NUM_PREDICT")
    cerebras_num_predict: int = Field(default=8192, alias="BYTE_CEREBRAS_NUM_PREDICT")
    # NVIDIA NIM: otro brazo de capa gratuita por el protocolo de OpenAI. Vale por
    # DeepSeek, familia que ni Gemini ni los gpt-oss de Groq traen.
    # ⚠ EL ALIAS ES `NVIDIA_NIM_API_KEY` porque es el nombre con el que la clave
    # está puesta en Railway. Con otro nombre el brazo arrancaría con la clave
    # vacía, que es el fallo del 2026-09-15 con `GEMINI_API_KEY`.
    nvidia_api_key: str = Field(default="", alias="NVIDIA_NIM_API_KEY")
    nvidia_num_predict: int = Field(default=8192, alias="BYTE_NVIDIA_NUM_PREDICT")
    # Mistral: capa gratuita por el protocolo de OpenAI. Trae la familia Mistral,
    # que no está en ningún otro brazo. Es la capa más estrecha de las cuatro.
    mistral_api_key: str = Field(default="", alias="MISTRAL_API_KEY")
    mistral_num_predict: int = Field(default=8192, alias="BYTE_MISTRAL_NUM_PREDICT")
    # OpenRouter: un intermediario, una clave para muchas familias. Sus ids gratis
    # llevan el sufijo `:free`, y ese sufijo es lo que decide si la petición cuesta.
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_num_predict: int = Field(default=8192, alias="BYTE_OPENROUTER_NUM_PREDICT")
    # ⚠ 60 SEGUNDOS, NO 45 NI 12. Medido el 2026-09-15: la capa gratuita de
    # Groq tiene 8.000 tokens POR MINUTO (gpt-oss) y cada llamada nuestra pesa
    # 4-8K —el mapa y el historial de la vuelta—: cuatro llamadas en 40 s y
    # llegó el 429 del minuto. Con 45 s parecía resuelto —una llamada por
    # minuto si los DOS modelos se turnan—, pero en cuanto UNO carga con todo
    # (el 120b agotó su cuota del día a las 17:34 y el 20b quedó solo) dos
    # llamadas de 4-6k caben en el mismo minuto y a las 20:06 volvió el 429 en
    # la cuarta llamada de la vuelta del cierre de 4h. Con 60 s es una por
    # minuto pase lo que pase; la vuelta tarda ~6 min, aún muy por debajo del
    # local. Una petición sola de más de 8K da 413 y no hay espera que la
    # arregle: ese es el techo del prompt para este brazo, y el relevo lo anota.
    groq_espera_s: float = Field(default=60.0, alias="BYTE_GROQ_ESPERA_S")

    # --- Persistencia ---
    database_url: str = Field(default="", alias="DATABASE_URL")
    storage: Literal["auto", "memory", "postgres"] = Field(default="auto", alias="BYTE_STORAGE")

    # --- RAG (Fase 2) ---
    # El modelo de embeddings está atado a la columna vector(768) de la migración
    # 002: cambiarlo obliga a migrar esa columna y reindexar todo.
    ollama_embed_model: str = Field(default="nomic-embed-text", alias="OLLAMA_EMBED_MODEL")
    # Indexar un documento entero puede ser lento en CPU: se vectoriza por lotes.
    embed_timeout_s: float = Field(default=60.0, alias="BYTE_EMBED_TIMEOUT_S")
    embed_batch_size: int = Field(default=16, alias="BYTE_EMBED_BATCH_SIZE")
    max_document_bytes: int = Field(default=20 * 1024 * 1024, alias="BYTE_MAX_DOCUMENT_BYTES")
    # Un PDF raro puede colgar al parser: se le pone plazo y se marca en error.
    document_parse_timeout_s: float = Field(default=30.0, alias="BYTE_DOC_PARSE_TIMEOUT_S")
    rag_top_k: int = Field(default=5, alias="BYTE_RAG_TOP_K")

    # --- Herramientas ---
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    sandbox_url: str = Field(default="", alias="SANDBOX_URL")

    # Carpeta que el agente puede leer con list_files/read_file/grep. Vacía —el
    # default— significa que esas herramientas no existen: dejar que un modelo
    # lea el disco tiene que ser una decisión explícita, no algo que se herede
    # del directorio donde alguien arrancó el proceso.
    project_root: str = Field(default="", alias="BYTE_PROJECT_ROOT")

    # Carpeta del CV (la que tiene `build/cv-{en,es}.html`). Vacía: el agente no
    # puede tocar el CV. Es opt-in como el resto: editar un CV y regenerar sus
    # PDF no es algo que deba poder hacer por venir instalado.
    cv_dir: str = Field(default="", alias="BYTE_CV_DIR")
    # Repo del portfolio, para dejar ahí los PDF nuevos. Sin esto se generan
    # igual, solo que no se copian.
    portfolio_dir: str = Field(default="", alias="BYTE_PORTFOLIO_DIR")
    # Carpeta de Drive donde viven los CV que se mandan desde el teléfono. Es la
    # copia que más importa que esté al día y la que más fácil queda vieja,
    # porque nadie copia un PDF a mano cada vez que cambia un número.
    drive_cv_dir: str = Field(default="", alias="BYTE_DRIVE_CV_DIR")
    # Herramientas de GitHub por `gh`. Opt-in: usan la sesión ya autenticada de
    # la máquina, y esa sesión puede tocar todos tus repos — que el agente pueda
    # usarla tiene que ser una decisión, no algo que venga de fábrica.
    github_tools: bool = Field(default=False, alias="BYTE_GITHUB_TOOLS")
    # Commit y push sobre BYTE_PROJECT_ROOT. Opt-in aparte de las de archivos:
    # escribir un archivo se deshace mirando el `.bak`, un push a un repo
    # público ya lo tiene GitHub.
    git_tools: bool = Field(default=False, alias="BYTE_GIT_TOOLS")
    # Un archivo corto que dice quién es el usuario. Entra en el prompt de cada
    # conversación, así que conviene que sea breve: el detalle vive en las
    # herramientas.
    perfil_file: str = Field(default="", alias="BYTE_PERFIL")
    # Abrir páginas y llamar APIs. Opt-in: trae contenido de terceros al prompt
    # y alcanza cualquier host público, así que es una decisión.
    web_fetch: bool = Field(default=False, alias="BYTE_WEB_FETCH")
    # Carpeta con instrucciones por tarea (`<carpeta>/<skill>/SKILL.md`), en el
    # formato que usan otras herramientas de agente.
    skills_dir: str = Field(default="", alias="BYTE_SKILLS")
    # Buscar trabajo: puntuar ofertas y traerlas de los feeds oficiales. Opt-in
    # porque trae contenido de terceros al prompt, igual que la búsqueda web.
    empleo_tools: bool = Field(default=False, alias="BYTE_EMPLEO_TOOLS")
    # Dónde está el criterio de búsqueda. Vacío = `perfil/busqueda.toml` del repo.
    empleo_criterio: str = Field(default="", alias="BYTE_EMPLEO_CRITERIO")
    # Paper trading: dónde vive el registro de operaciones. Necesita también
    # BYTE_PAPER_SCRIPTS, que apunta a los indicadores del repo de trading.
    paper_db: str = Field(default="", alias="BYTE_PAPER_DB")
    # Token interno compartido con el servicio sandbox. Sin él no se registra la
    # herramienta de ejecución: el sandbox rechaza todo pedido sin token.
    sandbox_token: str = Field(default="", alias="SANDBOX_TOKEN")
    # Servidores MCP, como `nombre=url,otro=url`. Es una lista blanca: el modelo
    # no elige a qué host se conecta Byte. Solo http(s) — stdio implicaría que
    # Byte lanza procesos, que es otra superficie de ataque y otra decisión.
    # Ojo con lo que se declara acá: las descripciones de sus herramientas las
    # lee el modelo (tool poisoning, docs/seguridad-byte.md).
    mcp_servers: str = Field(default="", alias="BYTE_MCP_SERVERS")
    mcp_timeout_s: float = Field(default=30.0, alias="BYTE_MCP_TIMEOUT_S")
    # Servidores MCP que Byte levanta al arrancar y apaga al salir, como
    # `nombre=puerto=comando` separados por `;`. Evita tener que dejar una
    # terminal abierta con el servidor corriendo.
    #
    # El comando lo escribe el usuario en su `.env`, fuera de la conversación:
    # el modelo no lo elige ni lo modifica, solo usa las herramientas que el
    # servidor ya levantado expone.
    mcp_locales: str = Field(default="", alias="BYTE_MCP_LOCALES")
    # Tokens Bearer por servidor, como `nombre=token`. En su propia variable y
    # no pegados a la URL: son secretos, y mezclarlos con la lista los dejaría a
    # la vista en cualquier log o captura de la configuración. n8n los pide.
    mcp_tokens: str = Field(default="", alias="BYTE_MCP_TOKENS")

    # --- Límites del agente (seguridad: costos y loops) ---
    max_iterations: int = Field(default=6, alias="BYTE_MAX_ITERATIONS")
    # 10 minutos, no 3: un run son varias llamadas al modelo, y en CPU un 7B
    # hace ~5-15 tokens por segundo. Con 3 minutos se cortan runs legítimos en
    # la primera prueba local. Sigue acotado, que es lo que pide el checklist.
    run_timeout_s: int = Field(default=600, alias="BYTE_RUN_TIMEOUT_S")
    max_message_chars: int = Field(default=8000, alias="BYTE_MAX_MESSAGE_CHARS")
    max_concurrent_runs: int = Field(default=2, alias="BYTE_MAX_CONCURRENT_RUNS")
    # Tamaño máximo de cada resultado de herramienta inyectado en el prompt.
    max_tool_result_chars: int = Field(default=4000, alias="BYTE_MAX_TOOL_RESULT_CHARS")
    max_search_query_chars: int = Field(default=200, alias="BYTE_MAX_SEARCH_QUERY_CHARS")

    # --- Rate limits (por credencial) ---
    rate_limit_general: str = Field(default="60/minute", alias="BYTE_RATE_LIMIT_GENERAL")
    rate_limit_runs: str = Field(default="10/minute", alias="BYTE_RATE_LIMIT_RUNS")

    # --- Web ---
    # NoDecode: sin esto pydantic-settings intenta leer el valor como JSON antes
    # de que corra _split_origins, y una lista separada por comas (o vacía) falla.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="BYTE_CORS_ORIGINS"
    )
    # Los events_token duran 60 s y son de un solo uso (contrato de la API).
    events_token_ttl_s: int = Field(default=60, alias="BYTE_EVENTS_TOKEN_TTL_S")
    session_ttl_s: int = Field(default=86400, alias="BYTE_SESSION_TTL_S")
    # 30 minutos: el contrato pide 15-60. Corto porque no hay revocación — un
    # token robado vale hasta que expire y no hay lista negra que lo corte.
    jwt_ttl_s: int = Field(default=1800, alias="BYTE_JWT_TTL_S")
    # 14 días. Es el que de verdad define cuánto dura una sesión: mientras se
    # use, se rota y no vence. Se revoca en el logout y cuando se detecta un
    # reuso, que es lo que el access token no puede hacer.
    refresh_ttl_s: int = Field(default=1_209_600, alias="BYTE_REFRESH_TTL_S")

    # --- Observabilidad (Fase 4). Las dos, apagadas si están vacías ---
    # Langfuse Cloud recibe los prompts, las respuestas y los resultados de las
    # herramientas: encenderlo manda tus conversaciones a un tercero, aunque
    # vayan redactadas. Vacío = no sale nada de tu máquina.
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")
    # Bugsink es self-hosted (un contenedor): los errores no salen de tu red.
    bugsink_dsn: str = Field(default="", alias="BUGSINK_DSN")

    # Cuántos días se guardan las conversaciones que entran por
    # `/v1/chat/completions`. Cada pedido crea una y el cliente no tiene dónde
    # guardar su id, así que sin purga la base crece sin techo. 0 la desactiva.
    openai_retencion_dias: int = Field(default=30, alias="BYTE_OPENAI_RETENCION_DIAS")
    # Del otro lado del modo seguro hay una persona decidiendo: el token dura
    # más que el del stream, y mientras tanto el run pausado no se descarta.
    resume_token_ttl_s: int = Field(default=3600, alias="BYTE_RESUME_TOKEN_TTL_S")

    @field_validator("max_message_chars")
    @classmethod
    def _cap_message_chars(cls, value: int) -> int:
        """El tope del contrato no se puede subir por configuración, solo bajar."""
        return max(1, min(value, HARD_MAX_MESSAGE_CHARS))

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Acepta "http://a,http://b" desde el entorno."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def use_postgres(self) -> bool:
        if self.storage == "postgres":
            return True
        if self.storage == "memory":
            return False
        return bool(self.database_url)

    @model_validator(mode="after")
    def _prod_exige_postgres(self) -> "Settings":
        """En producción nada queda en memoria.

        El plan es explícito: el checkpointer nunca va in-memory en producción.
        Sin Postgres se perderían las conversaciones y el hilo del agente en
        cada reinicio, así que se falla al arrancar en vez de avisar y seguir.
        """
        if self.env == "prod" and not self.use_postgres:
            raise ValueError(
                "BYTE_ENV=prod requiere DATABASE_URL (o BYTE_STORAGE=postgres): "
                "las conversaciones y el hilo del agente no pueden quedar en memoria"
            )
        return self


def modelos_disponibles(settings: "Settings") -> list[str]:
    """Los modelos entre los que se puede cambiar, con el activo primero.

    El de `OLLAMA_MODEL` va siempre, aunque no figure en la lista: es el que la
    app tiene cargado y sería raro no poder volver a él.
    """
    nombres = [settings.ollama_model]
    for crudo in settings.ollama_models.split(","):
        nombre = crudo.strip()
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    return nombres


@lru_cache
def get_settings() -> Settings:
    return Settings()
