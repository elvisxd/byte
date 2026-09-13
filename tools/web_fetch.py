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
import time
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


# Cuánto esperar entre dos pedidos al mismo dominio. No es por cortesía
# abstracta: el agente puede encadenar cinco lecturas en segundos, y eso desde
# una IP doméstica se parece a un scraper y termina en un bloqueo.
ESPERA_POR_DOMINIO_S = 1.5

# Cuándo se vuelve a leer el robots.txt de un dominio.
ROBOTS_VIGENCIA_S = 1800

_ultimo_pedido: dict[str, float] = {}
_robots: dict[str, tuple[float, Any]] = {}


class UrlNoPermitida(ValueError):
    """La URL apunta a algo que el agente no debería alcanzar."""


async def _esperar_turno(host: str) -> None:
    """Espacia los pedidos a un mismo dominio.

    El agente puede pedir cinco páginas del mismo sitio en dos segundos, y eso
    desde una IP doméstica se ve como un scraper: primero llegan los 429 y
    después el bloqueo. Un segundo y medio entre pedidos al mismo host alcanza
    para no parecerlo, y no se nota cuando se lee una sola página.
    """
    import asyncio

    ahora = time.monotonic()
    ultimo = _ultimo_pedido.get(host, 0.0)
    faltan = ESPERA_POR_DOMINIO_S - (ahora - ultimo)
    if faltan > 0:
        await asyncio.sleep(faltan)
    _ultimo_pedido[host] = time.monotonic()


async def _robots_permite(url: str) -> bool:
    """Si el `robots.txt` del sitio deja leer esa ruta.

    Un sitio que pide no ser recorrido está diciendo algo, y respetarlo es la
    diferencia entre un agente que lee y uno que raspa. Si el archivo no existe
    o no se puede leer, se asume que sí: la ausencia de reglas no es una
    prohibición.
    """
    from urllib.robotparser import RobotFileParser

    partes = urlparse(url)
    base = f"{partes.scheme}://{partes.netloc}"
    guardado = _robots.get(base)
    if guardado and time.monotonic() - guardado[0] < ROBOTS_VIGENCIA_S:
        parser = guardado[1]
    else:
        parser = RobotFileParser()
        try:
            async with httpx.AsyncClient(timeout=8.0) as cliente:
                respuesta = await cliente.get(f"{base}/robots.txt")
            parser.parse(respuesta.text.splitlines() if respuesta.status_code < 400 else [])
        except httpx.HTTPError:
            parser.parse([])
        _robots[base] = (time.monotonic(), parser)
    return parser.can_fetch("Byte", url)


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

    if not await _robots_permite(url):
        return ToolResult(
            content="el robots.txt de ese sitio pide no leer esa ruta",
            summary={"error": "robots"},
            ok=False,
        )
    await _esperar_turno(urlparse(url).hostname or "")

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
                "leer de verdad un resultado, o cuando el usuario te dé una URL. "
                "Para investigar bien, abrí DOS O TRES resultados distintos y compará: una "
                "sola fuente puede estar desactualizada o equivocada, y con dos que "
                "coincidan la respuesta vale mucho más. Citá de cuál sacaste cada cosa."
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
