"""Trazas del agente (Langfuse) y errores (Bugsink). Los dos, opcionales.

**Apagados por defecto, y a propósito.** Byte corre local: el modelo, la base y
el sandbox están en tu máquina, y lo único que sale a internet es una búsqueda
web que vos pediste. Langfuse Cloud rompería eso sin avisar —le manda los
prompts, las respuestas y los resultados de herramientas a un tercero— así que
sin `LANGFUSE_PUBLIC_KEY` no se manda nada, y el README lo dice con todas las
letras. Lo mismo con Bugsink: sin `BUGSINK_DSN`, ningún error sale de acá.

Cuando se encienden, **todo pasa por `api/redaccion.py` antes de salir**. En
Langfuse va como el `mask` del cliente, que el SDK aplica sobre el input, el
output y la metadata de cada observación: ponerlo ahí y no en cada punto de
instrumentación es lo que hace que no dependa de acordarse. En Sentry va como
`before_send`, que es el equivalente.

Ninguno de los dos puede tirar abajo un run: si el SDK no está instalado, si las
claves están mal o si el servicio no responde, se registra el aviso y Byte sigue
sin trazas. Observar no es una funcionalidad por la que valga la pena fallar.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from api.logging import get_logger
from api.redaccion import mask_langfuse, redactar_dato

logger = get_logger("api.observabilidad")


class Trazas:
    """Envoltorio de Langfuse que también funciona apagado.

    Apagado, cada método es un no-op: las rutas y el runner lo llaman igual y no
    se llenan de `if trazas is not None`.
    """

    def __init__(self, cliente: Any = None) -> None:
        self._cliente = cliente

    @property
    def activas(self) -> bool:
        return self._cliente is not None

    @contextmanager
    def run(self, nombre: str, **datos: Any) -> Iterator[None]:
        """Envuelve un run del agente. Con Langfuse apagado no hace nada.

        El `try` cubre **solo abrir la observación**, nunca el cuerpo. Envolver
        el `yield` hacía que cualquier excepción del run entrara acá: se
        registraba como `langfuse_traza_fallo` —culpando a Langfuse por un bug
        ajeno— y el segundo `yield` la convertía en
        `RuntimeError: generator didn't stop after throw()`, perdiendo la causa
        original. Peor: lo que escapara del `try` de `_correr` —el `finally` que
        hace `run.done.set()`— dejaba de correr, y quien esperaba ese run se
        colgaba para siempre.
        """
        if self._cliente is None:
            yield
            return
        try:
            observacion = self._cliente.start_as_current_observation(
                name=nombre, as_type="agent", input=redactar_dato(datos)
            )
        except Exception as exc:  # noqa: BLE001 - observar no puede romper el run
            logger.warning("langfuse_traza_fallo", error_type=type(exc).__name__)
            yield
            return

        with observacion:
            yield

    def trace_id(self) -> str | None:
        """El id de la traza en curso, para guardarlo en `MESSAGES`.

        Es lo que permite saltar de un mensaje guardado a su traza: sin esto,
        las trazas y los mensajes son dos listas que no se cruzan.
        """
        if self._cliente is None:
            return None
        try:
            return self._cliente.get_current_trace_id()
        except Exception:  # noqa: BLE001
            return None

    def flush(self) -> None:
        """Manda lo pendiente. Se llama al apagar, para no perder el último run."""
        if self._cliente is None:
            return
        try:
            self._cliente.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("langfuse_flush_fallo", error_type=type(exc).__name__)


def build_trazas(settings: Any) -> Trazas:
    """El cliente de Langfuse si está configurado; si no, uno apagado."""
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return Trazas()

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning(
            "langfuse_sin_instalar",
            detail="hay claves configuradas pero falta el paquete: uv sync --extra observabilidad",
        )
        return Trazas()

    try:
        cliente = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            # Todo lo que el SDK esté por mandar pasa por acá.
            mask=mask_langfuse,
        )
    except Exception as exc:  # noqa: BLE001 - arrancar sin trazas es válido
        logger.warning("langfuse_no_arranca", error_type=type(exc).__name__)
        return Trazas()

    logger.info(
        "langfuse_activo",
        host=settings.langfuse_host,
        detail="las trazas del agente salen hacia un tercero, redactadas",
    )
    return Trazas(cliente)


def init_errores(settings: Any) -> bool:
    """Arranca Sentry apuntando a Bugsink, si hay DSN. Devuelve si quedó activo.

    Bugsink habla el protocolo de Sentry, así que el SDK es el mismo. Se manda
    `send_default_pii=False` y además se redacta en `before_send`: lo primero
    saca lo que el SDK agrega solo (headers, cookies, IP), lo segundo saca lo
    que viene en el propio mensaje de error.
    """
    if not settings.bugsink_dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "bugsink_sin_instalar",
            detail="hay DSN configurado pero falta el paquete: uv sync --extra observabilidad",
        )
        return False

    def antes_de_enviar(evento: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
        return redactar_dato(evento)

    try:
        sentry_sdk.init(
            dsn=settings.bugsink_dsn,
            environment=settings.env,
            release=settings.version,
            # Sin PII automática: el SDK manda headers, cookies e IP si se le
            # deja, y eso incluye la credencial con la que alguien llamó.
            send_default_pii=False,
            before_send=antes_de_enviar,
            # Solo errores: Byte ya tiene sus propias trazas del agente y no
            # necesita que Sentry muestree performance.
            traces_sample_rate=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - arrancar sin tracking es válido
        logger.warning("bugsink_no_arranca", error_type=type(exc).__name__)
        return False

    logger.info("bugsink_activo", detail="los errores salen hacia el DSN configurado, redactados")
    return True
