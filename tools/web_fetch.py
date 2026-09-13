"""Abrir una página y leerla, y llamar una API para ver qué devuelve.

`web_search` trae títulos y extractos: alcanza para saber que algo existe, no
para entenderlo. Esto es el paso siguiente — abrir la página que interesa y leer
el texto — y el que permite investigar en vez de solo buscar.

**Todo lo que entra por acá es contenido de terceros**, así que va envuelto como
no confiable igual que la búsqueda web. Un artículo puede traer instrucciones
metidas adentro, y una API puede devolver lo que quiera en un campo de texto.

**Lo que no se puede alcanzar.** Se bloquean las direcciones privadas —
`localhost`, `127.x`, `10.x`, `192.168.x`, `169.254.x` (los metadatos de la
nube)— porque el agente corre en la máquina del usuario y una URL que el modelo
eligió a partir de una página que leyó es exactamente el camino de un SSRF: la
página dice "consultá http://localhost:8000/admin" y el agente, que sí puede
llegar ahí, lo hace.
"""

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.web_fetch")

TIMEOUT_S = 20.0
# Cuánto HTML se descarga antes de cortar. Una página de más de esto es un
# volcado de datos, no algo para leer.
MAX_BYTES = 2_000_000
# Etiquetas cuyo contenido no es texto para leer.
BASURA = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form")


class UrlNoPermitida(ValueError):
    """La URL apunta a algo que el agente no debería alcanzar."""


def _verificar(url: str) -> str:
    """Devuelve la URL si es segura de pedir; si no, explica por qué no.

    Se resuelve el nombre a IP **antes** de pedir: un dominio puede apuntar a
    `127.0.0.1`, y mirar solo el texto de la URL no lo detectaría.
    """
    partes = urlparse(url)
    if partes.scheme not in ("http", "https"):
        raise UrlNoPermitida("solo http y https")
    if not partes.hostname:
        raise UrlNoPermitida("esa URL no tiene dominio")

    try:
        _, _, direcciones = socket.gethostbyname_ex(partes.hostname)
    except OSError as exc:
        raise UrlNoPermitida(f"no se pudo resolver el dominio: {exc.strerror}") from exc

    for cruda in direcciones:
        ip = ipaddress.ip_address(cruda)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise UrlNoPermitida(
                f"'{partes.hostname}' resuelve a una dirección interna ({cruda}): "
                "el agente no alcanza la red local ni los metadatos de la nube"
            )
    return url


def _texto_de(html: str) -> str:
    """El texto legible de una página, sin menús ni scripts."""
    from selectolax.parser import HTMLParser

    arbol = HTMLParser(html)
    for etiqueta in BASURA:
        for nodo in arbol.css(etiqueta):
            nodo.decompose()
    cuerpo = arbol.body or arbol.root
    if cuerpo is None:
        return ""
    # `separator` con salto: sin eso los párrafos quedan pegados en una línea.
    texto = cuerpo.text(separator="\n", strip=True)
    # Colapsar los saltos de más que deja el HTML mal formado.
    lineas = [x.strip() for x in texto.splitlines()]
    return "\n".join(x for x in lineas if x)


# --- Leer una página ---


class LeerWebArgs(BaseModel):
    url: str = Field(description="La dirección completa de la página, con http:// o https://")


async def _leer_web(args: LeerWebArgs, max_chars: int) -> ToolResult:
    try:
        url = _verificar(args.url)
    except UrlNoPermitida as exc:
        return ToolResult(content=str(exc), summary={"error": str(exc)}, ok=False)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as cliente:
            respuesta = await cliente.get(url, headers={"User-Agent": "Byte/0.1 (agente local)"})
    except httpx.HTTPError as exc:
        return ToolResult(
            content=f"no se pudo abrir la página: {type(exc).__name__}",
            summary={"error": "inalcanzable"},
            ok=False,
        )

    if respuesta.status_code >= 400:
        return ToolResult(
            content=f"la página respondió {respuesta.status_code}",
            summary={"error": respuesta.status_code},
            ok=False,
        )

    tipo = respuesta.headers.get("content-type", "")
    crudo = respuesta.text[:MAX_BYTES]
    texto = _texto_de(crudo) if "html" in tipo else crudo
    if not texto.strip():
        return ToolResult(
            content="la página no tiene texto legible (puede ser una app que se arma con JS)",
            summary={"error": "sin texto"},
            ok=False,
        )

    logger.info("web_leida", url=url[:120], chars=len(texto))
    return ToolResult(
        content=wrap_untrusted(f"PÁGINA {url[:120]}", texto, max_chars),
        summary={"url": url[:200], "chars": len(texto)},
        sources=[{"url": url, "title": str(respuesta.headers.get("title", "")) or url[:80]}],
    )


# --- Llamar una API ---


class LlamarApiArgs(BaseModel):
    url: str = Field(description="El endpoint completo, con http:// o https://")
    metodo: str = Field(default="GET", description="GET o POST")
    cuerpo: str = Field(default="", description="JSON a mandar, solo con POST")


async def _llamar_api(args: LlamarApiArgs, max_chars: int) -> ToolResult:
    """Llama un endpoint y muestra qué devolvió, con su código y su forma.

    Solo GET y POST: son los que sirven para explorar. PUT, PATCH y DELETE
    modifican cosas del otro lado, y un modelo probando endpoints no debería
    poder borrar nada de nadie.
    """
    metodo = args.metodo.upper()
    if metodo not in ("GET", "POST"):
        return ToolResult(
            content="solo GET y POST: los que modifican del otro lado no se usan para explorar",
            summary={"error": "método no permitido"},
            ok=False,
        )
    try:
        url = _verificar(args.url)
    except UrlNoPermitida as exc:
        return ToolResult(content=str(exc), summary={"error": str(exc)}, ok=False)

    cuerpo: Any = None
    if args.cuerpo.strip():
        try:
            cuerpo = json.loads(args.cuerpo)
        except json.JSONDecodeError as exc:
            return ToolResult(
                content=f"el cuerpo no es JSON válido: {exc}",
                summary={"error": "json inválido"},
                ok=False,
            )

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as cliente:
            respuesta = await cliente.request(
                metodo, url, json=cuerpo, headers={"User-Agent": "Byte/0.1 (agente local)"}
            )
    except httpx.HTTPError as exc:
        return ToolResult(
            content=f"no respondió: {type(exc).__name__}",
            summary={"error": "inalcanzable"},
            ok=False,
        )

    tipo = respuesta.headers.get("content-type", "")
    cuerpo_texto = respuesta.text[:MAX_BYTES]
    if "json" in tipo:
        try:
            # Reformateado: un JSON en una línea es ilegible y el modelo se
            # pierde la estructura, que es lo que se quiere ver de una API.
            cuerpo_texto = json.dumps(json.loads(cuerpo_texto), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass

    partes = [f"{metodo} {url}", f"status: {respuesta.status_code}", f"content-type: {tipo}", ""]
    partes.append(cuerpo_texto)
    logger.info("api_llamada", url=url[:120], status=respuesta.status_code)
    return ToolResult(
        content=wrap_untrusted("RESPUESTA DE LA API", "\n".join(partes), max_chars),
        summary={"status": respuesta.status_code, "url": url[:200]},
        ok=respuesta.status_code < 400,
    )


# --- Armado ---


def build_web_fetch_tools(max_chars: int) -> list[Tool]:
    async def leer(args: BaseModel) -> ToolResult:
        return await _leer_web(args, max_chars)  # type: ignore[arg-type]

    async def api(args: BaseModel) -> ToolResult:
        return await _llamar_api(args, max_chars)  # type: ignore[arg-type]

    return [
        Tool(
            name="leer_web",
            description=(
                "Abre una página web y devuelve su texto. Usala después de web_search para "
                "leer de verdad un resultado, o cuando el usuario te dé una URL. Es lo que "
                "convierte una búsqueda en investigación."
            ),
            args_model=LeerWebArgs,
            run=leer,
        ),
        Tool(
            name="llamar_api",
            description=(
                "Llama un endpoint HTTP (GET o POST) y muestra qué devuelve, con su código "
                "y su estructura. Usala para explorar una API y ver su forma real."
            ),
            args_model=LlamarApiArgs,
            run=api,
        ),
    ]
