"""La página de ofertas, sola, para desplegarla en cualquier lado.

**Por qué existe separada de `api/`.** La API de Byte necesita Ollama, Postgres
y el sandbox: correrla en la nube cuesta ~$180/mes de RAM y por eso el despliegue
está pausado a propósito. Pero el camino de `/ofertas` —parsear lo pegado,
puntuarlo contra el TOML y ordenar— no usa **nada de eso**: sólo la biblioteca
estándar y `empleo/`. Se puede comprobar:

    python -c "import ast,pathlib; ..."   # ver el PR que agregó este archivo

Así que esto es un servicio de centavos que hace la parte que hay que poder usar
desde el teléfono, mientras el agente completo sigue corriendo en la Mac.

**Sirve las mismas rutas que la API grande** —`/` con la página y
`POST /api/v1/ofertas/pegado`— para que `ofertas.js` sea el mismo archivo en los
dos lados. Dos copias divergen; una sola no.

**Arranca sólo con `OFERTAS_CLAVE` puesta.** Sin clave se niega a levantar, en
vez de quedar abierto: esto va a tener una URL pública, y un endpoint que parsea
texto arbitrario sin credencial es una invitación. Fallar cerrado y ruidoso es
mejor que andar callado y abierto.
"""

import asyncio
import hmac
import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from empleo.alertas import a_json as alertas_json
from empleo.criterio import cargar_criterio
from empleo.pegado import a_json, analizar

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
CRITERIO = Path(os.environ.get("BYTE_EMPLEO_CRITERIO") or RAIZ / "perfil" / "busqueda.toml")

# Igual que en la API grande: más que esto no es una página pegada, es un archivo.
MAX_CARACTERES = 400_000


class SinClave(RuntimeError):
    """Falta `OFERTAS_CLAVE`. Se levanta al construir, no al primer pedido."""


class PegadoIn(BaseModel):
    texto: str = Field(description="La página de resultados, copiada y pegada tal cual")
    connects: int = Field(default=0, ge=0, le=1000)


def _autorizado(recibida: str, esperada: str) -> bool:
    """Comparación en tiempo constante: con `==` el largo del prefijo correcto
    se puede medir por el tiempo de respuesta."""
    return hmac.compare_digest(recibida.strip(), esperada)


def crear_app(clave: str | None = None) -> FastAPI:
    clave = clave if clave is not None else os.environ.get("OFERTAS_CLAVE", "")
    if not clave:
        raise SinClave(
            "Falta OFERTAS_CLAVE. Esta página va a tener una URL pública: sin "
            "clave no arranca. Generá una con `openssl rand -hex 32`."
        )

    app = FastAPI(title="Byte — Ofertas", docs_url=None, redoc_url=None)
    router = APIRouter()

    def _trabajo(texto: str, connects: int) -> dict:
        return a_json(analizar(texto, cargar_criterio(CRITERIO), connects))

    @router.post("/ofertas/pegado")
    async def pegado(cuerpo: PegadoIn, authorization: str = Header(default="")) -> dict:
        prefijo = "Bearer "
        recibida = authorization[len(prefijo) :] if authorization.startswith(prefijo) else ""
        if not _autorizado(recibida, clave):
            # 401 pelado: describir qué falta le diría a quien tantea en qué
            # parte estuvo cerca.
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="no autorizado")
        # En un hilo: el parseo recorre el texto varias veces y leer el TOML toca
        # el disco. Este servicio tiene un proceso, así que bloquear el bucle
        # deja esperando a cualquier otro pedido.
        return await asyncio.to_thread(_trabajo, cuerpo.texto[:MAX_CARACTERES], cuerpo.connects)

    @router.get("/ofertas/alertas")
    async def alertas(authorization: str = Header(default="")) -> dict:
        """Qué pegar en cada plataforma para crear una alerta guardada.

        La misma ruta que sirve la API grande, para que `ofertas.js` siga siendo
        un solo archivo. Va aparte del pegado porque no depende de lo pegado: se
        muestra al abrir la página, que es cuando sirve.
        """
        prefijo = "Bearer "
        recibida = authorization[len(prefijo) :] if authorization.startswith(prefijo) else ""
        if not _autorizado(recibida, clave):
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="no autorizado")
        return {"alertas": await asyncio.to_thread(lambda: alertas_json(cargar_criterio(CRITERIO)))}

    app.include_router(router, prefix="/api/v1")

    if (WEB / "static").is_dir():
        app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    @app.get("/salud", include_in_schema=False)
    async def salud() -> dict:
        """Para que Railway sepa si el contenedor está vivo. Sin credencial: no
        dice nada que no se sepa por el hecho de que la URL responde."""
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def pagina() -> HTMLResponse:
        archivo = WEB / "templates" / "ofertas.html"
        if not archivo.is_file():
            return HTMLResponse("<h1>Byte</h1><p>Falta web/templates/ofertas.html</p>")
        return HTMLResponse(archivo.read_text(encoding="utf-8"))

    return app
