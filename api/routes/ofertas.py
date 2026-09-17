"""Pegar una página de ofertas y que diga a cuáles aplicar.

Existe como endpoint y no sólo como comando porque el flujo real es copiar de
una pestaña y pegar en otra, y para eso una página gana a una terminal: el
portapapeles va de navegador a navegador sin pasar por ningún lado.

El texto pegado es contenido de terceros y acá **no se ejecuta ni se obedece**:
se parsea, se cuenta y se puntúa. Lo único que sale de vuelta son datos
calculados por el código.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.deps import Context, CredentialId
from empleo.criterio import cargar_criterio
from empleo.pegado import a_json, analizar

router = APIRouter(tags=["ofertas"])

# Más que esto no es una página de resultados pegada: es un archivo. El tope
# está porque el parseo recorre el texto varias veces y una pegada enorme
# bloquearía el proceso entero, que es de un solo usuario.
MAX_CARACTERES = 400_000


class PegadoIn(BaseModel):
    texto: str = Field(description="La página de resultados, copiada y pegada tal cual")
    connects: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="Connects disponibles. Sólo se usa si lo pegado es de Upwork.",
    )


def _trabajo(texto: str, criterio_configurado: str, connects: int) -> dict:
    """Leer el TOML y parsear. Va en un hilo: ver el endpoint."""
    ruta = (
        Path(criterio_configurado).expanduser()
        if criterio_configurado
        else Path(__file__).resolve().parent.parent.parent / "perfil" / "busqueda.toml"
    )
    return a_json(analizar(texto, cargar_criterio(ruta), connects))


@router.post("/ofertas/pegado")
async def pegado(cuerpo: PegadoIn, ctx: Context, _credential: CredentialId) -> dict:
    """Analiza lo pegado en un hilo aparte.

    No es por el linter: el parseo recorre el texto varias veces y leer el TOML
    toca el disco. Con una pegada grande, hacerlo en el bucle de eventos deja a
    la API entera —incluido el streaming de un run en curso— esperando a que
    termine.
    """
    return await asyncio.to_thread(
        _trabajo,
        cuerpo.texto[:MAX_CARACTERES],
        ctx.settings.empleo_criterio,
        cuerpo.connects,
    )
