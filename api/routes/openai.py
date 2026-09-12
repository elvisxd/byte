"""Compatibilidad con la API de OpenAI: `/v1/chat/completions` y `/v1/models`.

Para que Open WebUI, Continue.dev o cualquier SDK de OpenAI usen a Byte como
backend sin escribir una línea. No es una API nueva: por dentro crea una
conversación y un run, igual que `POST /conversations/{id}/messages`.

Va montado en `/v1` y no en `/api/v1` porque los clientes arman la URL pegando
`/chat/completions` a la base que uno configura: con `/api/v1` habría que poner
la base a mano y la mitad de los clientes no lo permite.

Seguridad (docs/seguridad-byte.md):

- **`model` contra lista blanca.** Un nombre arbitrario no llega nunca a Ollama:
  sería descarga y ejecución de un modelo cualquiera pedida por el cliente.
- **Misma credencial que el resto de la API.** Los clientes de OpenAI mandan
  `Authorization: Bearer`, que `credential_from_request` acepta desde esta fase.
- El contenido de los mensajes pasa por los mismos límites que el endpoint
  nativo (`BYTE_MAX_MESSAGE_CHARS`).
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agent.events import AGUI
from api.deps import Context, CredentialId, limiter, runs_limit
from api.errors import ByteError
from api.logging import get_logger
from db.repository import SIN_USUARIO, title_from_content
from models.schemas import ChatCompletionRequest, OpenAIModel, OpenAIModelList

logger = get_logger("api.openai")

router = APIRouter(tags=["compatibilidad openai"])

# Nombre que Byte publica en `/v1/models`. Se acepta también el nombre real del
# modelo de Ollama, para quien lo tenga escrito en la configuración del cliente.
MODELO_PUBLICO = "byte"

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _modelos_permitidos(ctx: Context) -> set[str]:
    """La lista blanca: lo que Byte acepta en el campo `model`.

    Es cerrada a propósito. El `model` de un pedido no puede decidir qué corre
    Ollama: sería dejar que un cliente pida la descarga y ejecución de cualquier
    modelo (docs/seguridad-byte.md).
    """
    return {MODELO_PUBLICO, ctx.settings.ollama_model}


def _ultimo_del_usuario(pedido: ChatCompletionRequest) -> str:
    """El texto que Byte va a responder.

    Solo se usa el último turno del usuario: el historial lo maneja Byte con su
    propio checkpointer y su compactación, así que reenviar la conversación
    entera duplicaría lo que el agente ya tiene. Un cliente que arranca una
    conversación nueva en cada pedido sigue funcionando igual.
    """
    for mensaje in reversed(pedido.messages):
        if mensaje.role == "user":
            texto = mensaje.texto().strip()
            if texto:
                return texto
    return ""


def _fragmento(id_respuesta: str, modelo: str, delta: dict[str, Any], fin: str | None) -> str:
    """Un `chat.completion.chunk` en el formato que espera un cliente de OpenAI."""
    cuerpo = {
        "id": id_respuesta,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": modelo,
        "choices": [{"index": 0, "delta": delta, "finish_reason": fin, "logprobs": None}],
    }
    return f"data: {json.dumps(cuerpo, ensure_ascii=False)}\n\n"


async def _cerrar_pausado(ctx: Context, run: Any) -> None:
    """Cierra un run que quedó esperando aprobación, como si se hubiera rechazado.

    `RunManager.cancel` no sirve acá: `Run.finished` incluye "paused", así que
    sale sin hacer nada y el run queda vivo con su interrupt de LangGraph
    colgando hasta que lo purgue el TTL. `resume(run, False)` es el camino que
    ya existe para decir que no, y deja el hilo del agente consistente.

    Si algo falla al cerrarlo no se propaga: quien llamó ya tiene su respuesta y
    el run igual se purga solo.
    """
    try:
        await ctx.runs.resume(run, False)
        await ctx.runs.wait(run)
    except Exception:  # noqa: BLE001 - cerrar es best-effort, el TTL es la red
        logger.warning("openai_run_pausado_sin_cerrar", run_id=run.id)


@router.get("/models", response_model=OpenAIModelList)
async def list_models(ctx: Context, _credential: CredentialId) -> OpenAIModelList:
    """Lo que los clientes llaman para poblar su selector de modelos."""
    ahora = int(time.time())
    return OpenAIModelList(
        data=[OpenAIModel(id=nombre, created=ahora) for nombre in sorted(_modelos_permitidos(ctx))]
    )


@router.post("/chat/completions")
@limiter.limit(runs_limit)
async def chat_completions(
    request: Request,
    pedido: ChatCompletionRequest,
    ctx: Context,
    credential: CredentialId,
) -> Any:
    """Un pedido de OpenAI, respondido por el agente de Byte.

    Con `stream: true` devuelve los mismos `chat.completion.chunk` que OpenAI,
    traducidos desde los eventos AG-UI del run.
    """
    if pedido.model not in _modelos_permitidos(ctx):
        # 404 y no 400: es lo que devuelve OpenAI para un modelo que no existe,
        # y los clientes lo muestran como "modelo no disponible".
        raise ByteError(
            "model_not_found",
            f"El modelo '{pedido.model}' no está disponible. Usá '{MODELO_PUBLICO}'.",
            status_code=404,
        )

    contenido = _ultimo_del_usuario(pedido)
    if not contenido:
        raise ByteError("validation_error", "No hay ningún mensaje del usuario", status_code=422)
    if len(contenido) > ctx.settings.max_message_chars:
        raise ByteError(
            "payload_too_large",
            f"El mensaje supera los {ctx.settings.max_message_chars} caracteres",
            status_code=413,
        )

    # Una conversación por pedido: el cliente de OpenAI manda su historial
    # completo cada vez y no tiene dónde guardar un id de Byte. Quedan en la
    # base como cualquier otra, así que se ven en la web y en `byte conversations`.
    # El título se arma como en el endpoint nativo (`title_from_content`): aplana
    # espacios y corta con elipsis. Lleva el prefijo para distinguir de dónde
    # vino, y el mismo largo que cualquier otra conversación — este campo se
    # lista en la web y en el CLI, y la Fase 4 lo va a exportar a Langfuse.
    conversacion = await ctx.repository.create_conversation(
        f"[openai] {title_from_content(contenido)}", SIN_USUARIO
    )
    # Sin `preparar` acá: `RunManager.start` ya lo llama, y la conversación
    # recién creada no puede tener un run en curso ni una aprobación pendiente.
    # Llamarlo dos veces duplicaba un viaje al checkpointer por pedido.
    await ctx.repository.add_message(conversacion.id, "user", contenido)
    run = await ctx.runs.start(conversacion.id, credential, contenido, safe_mode=False)

    id_respuesta = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if pedido.stream:
        return StreamingResponse(
            _stream(ctx, run, id_respuesta, pedido.model),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    await ctx.runs.wait(run)
    if run.status == "paused":
        # El modo seguro no tiene forma de expresarse en el formato de OpenAI:
        # no hay a quién preguntarle del otro lado. Se cierra el run como un
        # rechazo y se dice por qué, en vez de dejar al cliente esperando una
        # respuesta que no va a llegar.
        #
        # `resume(run, False)` y no `cancel`: `cancel` no hace nada sobre un run
        # pausado (`Run.finished` incluye "paused" y sale temprano), así que el
        # run quedaba vivo con su interrupt de LangGraph colgando hasta que lo
        # purgara el TTL, una hora después.
        await _cerrar_pausado(ctx, run)
        raise ByteError(
            "approval_required",
            "El run necesita una aprobación humana, que este endpoint no puede pedir. "
            "Usá la API de Byte o la web para este pedido.",
            status_code=409,
        )
    if run.status == "error":
        raise ByteError("run_failed", "El run terminó con error", status_code=500)

    mensaje = (
        await ctx.repository.get_message(run.message_id, SIN_USUARIO) if run.message_id else None
    )
    if mensaje is None:
        raise ByteError("run_failed", "El run no produjo respuesta", status_code=500)

    return {
        "id": id_respuesta,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": pedido.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": mensaje.content},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        # Byte no cuenta tokens (Ollama los reporta, pero el run pasa por varias
        # llamadas y sumarlas sería inventar). Va en cero y explícito: varios
        # clientes rompen si el campo falta.
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def _stream(ctx: Context, run: Any, id_respuesta: str, modelo: str) -> AsyncIterator[str]:
    """Traduce los eventos AG-UI del run a `chat.completion.chunk`.

    Se suscribe a los eventos crudos en vez de reusar `RunManager.stream`, que
    ya los sirve como SSE con el formato de Byte: acá hacen falta los objetos
    para traducirlos, no su texto.
    """
    cola = run.add_subscriber()
    ultimo = 0
    fallo = False
    try:
        yield _fragmento(id_respuesta, modelo, {"role": "assistant", "content": ""}, None)

        # Lo que ya salió antes de suscribirse (el run arranca en `start`).
        for evento in run.events_after(0):
            ultimo = evento.seq
            if evento.type == AGUI.TEXT_MESSAGE_CONTENT and evento.data.get("delta"):
                yield _fragmento(id_respuesta, modelo, {"content": evento.data["delta"]}, None)

        while True:
            if run.finished and cola.empty():
                break
            try:
                evento = await asyncio.wait_for(cola.get(), timeout=15.0)
            except TimeoutError:
                # Comentario SSE: mantiene viva la conexión sin ensuciar el
                # stream, que para el cliente son solo objetos JSON.
                yield ": keepalive\n\n"
                continue
            if evento.seq <= ultimo:
                continue
            ultimo = evento.seq
            if evento.type == AGUI.TEXT_MESSAGE_CONTENT and evento.data.get("delta"):
                yield _fragmento(id_respuesta, modelo, {"content": evento.data["delta"]}, None)
            elif evento.type == AGUI.RUN_ERROR:
                logger.warning("openai_stream_run_error", run_id=run.id)
                fallo = True
                break

        fin = "stop"
        if fallo or run.status == "error":
            # Con el stream ya abierto no se puede cambiar el código HTTP, así
            # que el error se dice donde el usuario lo va a leer. Sin esto el
            # cliente recibe un 200 con la burbuja vacía y parece que el modelo
            # no tuvo nada que decir.
            fin = "stop"
            yield _fragmento(
                id_respuesta,
                modelo,
                {"content": "\n\n[El run falló del lado de Byte: revisá sus logs.]"},
                None,
            )
        elif run.status == "paused":
            # Igual que sin streaming: no hay a quién preguntarle. El run se
            # cierra como rechazo y el cliente ve una respuesta terminada por
            # longitud, que es lo más cercano que el formato permite decir.
            await _cerrar_pausado(ctx, run)
            fin = "length"
            yield _fragmento(
                id_respuesta,
                modelo,
                {
                    "content": "\n\n[Byte necesita una aprobación humana para seguir: "
                    "usá la API de Byte o la web para este pedido.]"
                },
                None,
            )
        yield _fragmento(id_respuesta, modelo, {}, fin)
        yield "data: [DONE]\n\n"
    finally:
        run.remove_subscriber(cola)
