"""Levantar servidores MCP locales que el usuario declaró, y apagarlos al salir.

Byte habla MCP por HTTP, no por stdio: lanzar el proceso de un servidor y
hablarle por sus tuberías es otra superficie de ataque. Pero servidores como
Playwright MCP aceptan `--port` y corren como servidor HTTP, así que se puede
tener lo cómodo sin ceder eso — **Byte lanza el proceso, y le habla por HTTP
como a cualquier otro**.

**La diferencia con dejar que el modelo lance procesos.** Lo que se ejecuta acá
lo escribió el usuario en su `.env`, una vez, fuera de la conversación. El
modelo no elige el comando, no lo modifica y no puede agregar otro: solo usa las
herramientas que el servidor ya levantado expone. Es la misma confianza que se
le da al `docker compose up` del proyecto.

Los procesos mueren cuando Byte muere. Un servidor MCP con un navegador adentro
que sobrevive a su dueño es un proceso huérfano consumiendo memoria, y el puerto
ocupado hace fallar el arranque siguiente con un error que no dice por qué.
"""

import asyncio
import contextlib
import shutil
import time
from dataclasses import dataclass

import httpx

from api.logging import get_logger

logger = get_logger("mcp_client.local")

# Cuánto se espera a que un servidor conteste antes de darlo por perdido.
ARRANQUE_S = 45.0
# Cada cuánto se le pregunta si ya está.
SONDEO_S = 0.5


@dataclass(frozen=True, slots=True)
class ServidorLocal:
    """Un servidor MCP que Byte levanta: su nombre, su comando y su URL."""

    nombre: str
    comando: list[str]
    url: str


def parsear(declarados: str) -> list[ServidorLocal]:
    """`nombre=puerto=comando; otro=puerto=comando` → servidores locales.

    El puerto va explícito y no se adivina: el comando tiene que recibirlo como
    argumento, y hacer que Byte lo infiera del comando sería adivinar el formato
    de cada servidor.
    """
    servidores: list[ServidorLocal] = []
    for entrada in declarados.split(";"):
        partes = [x.strip() for x in entrada.split("=", 2)]
        if len(partes) != 3 or not all(partes):
            if entrada.strip():
                logger.warning("mcp_local_mal_declarado", entrada=entrada[:80])
            continue
        nombre, puerto, comando = partes
        if not puerto.isdigit():
            logger.warning("mcp_local_puerto_invalido", servidor=nombre, puerto=puerto[:20])
            continue
        servidores.append(
            ServidorLocal(
                nombre=nombre,
                comando=comando.split(),
                # `localhost` y no `127.0.0.1`: algunos servidores validan el
                # header Host y los tratan como distintos —Playwright MCP
                # responde 403 al segundo aunque sea la misma máquina.
                url=f"http://localhost:{puerto}/mcp",
            )
        )
    return servidores


async def _esperar(url: str, proceso: asyncio.subprocess.Process) -> bool:
    """Espera a que el servidor conteste, o a que el proceso se muera intentando."""
    limite = time.monotonic() + ARRANQUE_S
    async with httpx.AsyncClient(timeout=3.0) as cliente:
        while time.monotonic() < limite:
            if proceso.returncode is not None:
                return False  # se murió al arrancar: no tiene sentido seguir esperando
            with contextlib.suppress(httpx.HTTPError):
                # Cualquier respuesta sirve: un 400 o un 406 significa que el
                # servidor está vivo y contestando, que es lo que se espera.
                await cliente.get(url)
                return True
            await asyncio.sleep(SONDEO_S)
    return False


async def levantar(servidor: ServidorLocal) -> asyncio.subprocess.Process | None:
    """Lanza el servidor y espera a que conteste. None si no se pudo."""
    binario = shutil.which(servidor.comando[0])
    if not binario:
        logger.warning(
            "mcp_local_sin_binario",
            servidor=servidor.nombre,
            binario=servidor.comando[0],
            detail="el agente arranca sin las herramientas de este servidor",
        )
        return None

    proceso = await asyncio.create_subprocess_exec(
        binario,
        *servidor.comando[1:],
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await _esperar(servidor.url, proceso):
        logger.info("mcp_local_arriba", servidor=servidor.nombre, url=servidor.url)
        return proceso

    logger.warning(
        "mcp_local_no_arranca",
        servidor=servidor.nombre,
        detail=f"no contestó en {ARRANQUE_S:.0f}s",
    )
    await apagar(proceso)
    return None


async def apagar(proceso: asyncio.subprocess.Process) -> None:
    """Termina el proceso, con SIGKILL si no se va por las buenas.

    Un servidor con un navegador adentro puede ignorar el SIGTERM mientras
    cierra pestañas; sin el KILL el apagado de Byte se quedaría esperándolo.
    """
    if proceso.returncode is not None:
        return
    proceso.terminate()
    try:
        await asyncio.wait_for(proceso.wait(), timeout=5.0)
    except TimeoutError:
        proceso.kill()
        with contextlib.suppress(Exception):
            await proceso.wait()
