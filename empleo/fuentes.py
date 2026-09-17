"""De dónde salen las ofertas. Una función por feed, todas devuelven `Oferta`.

**Acá no se scrapea nada.** Las cuatro fuentes abiertas publican API o RSS
oficial y eso es exactamente por qué son estas cuatro y no las grandes: Indeed y
LinkedIn no tienen feed público y prohíben el raspado, así que entrar ahí sería
cambiar una cuenta por unos links. Upwork es un caso aparte y está explicado en
`empleo/README.md`.

**Una fuente que falla no frena a las demás.** Cada adaptador atrapa sus propios
errores y devuelve lista vacía: que RemoteOK esté caído no puede dejarte sin el
aviso de hoy. Lo que sí hace es quedar registrado, para que "no llegó nada" se
distinga de "no había nada".

**Todo lo que entra por acá es contenido de terceros.** Una descripción de
trabajo la escribe cualquiera y puede traer instrucciones adentro: por eso, todo
lo que llegue al prompt del modelo pasa por `wrap_untrusted` en `tools/empleo.py`
y nada de acá se ejecuta ni se obedece.
"""

import json
import re
import xml.etree.ElementTree as ET  # noqa: S405 - ver _rss_a_ofertas
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from api.logging import get_logger
from empleo.oferta import Oferta

logger = get_logger("empleo.fuentes")

# Un agente que se identifica es un agente que el dueño del feed puede bloquear
# si molesta, en vez de uno que tiene que adivinar quién lo está golpeando.
AGENTE = "byte-cazador-de-ofertas/1.0 (+https://github.com/elvisxd/byte)"
TIMEOUT_S = 25.0
# Tope de descarga por fuente. RemoteOK devuelve el feed entero en una respuesta
# y puede pasar los pocos MB; más que esto no es un feed, es un volcado.
MAX_BYTES = 8_000_000

_ETIQUETAS = re.compile(r"<[^>]+>")
_ESPACIOS = re.compile(r"[ \t]+")


def _texto_plano(html: str) -> str:
    """El texto de una descripción de oferta.

    No se reusa `_texto_de` de `tools/web_fetch.py` a propósito: aquello recibe
    una página entera y tiene que sacarle el menú, el pie y los banners. Esto
    recibe un fragmento que ya es la descripción, y pasarlo por el mismo filtro
    —que borra `header` y `footer`— se comería secciones legítimas de la oferta.
    """
    if not html:
        return ""
    from selectolax.parser import HTMLParser

    try:
        texto = HTMLParser(html).text(separator="\n", strip=True)
    except Exception:  # noqa: BLE001 - un HTML roto no puede tirar el cazador
        texto = _ETIQUETAS.sub(" ", html)
    lineas = [_ESPACIOS.sub(" ", x).strip() for x in texto.splitlines()]
    return "\n".join(x for x in lineas if x)


def _texto(valor: object, tope: int = 20_000) -> str:
    """Lo que venga del feed, convertido a str y acotado.

    Los feeds mienten sobre sus tipos: un `salary` puede venir número, string o
    `null` en la misma respuesta. Convertir en el borde evita un `TypeError` a
    mitad de la puntuación, que es donde menos se entiende.
    """
    if valor is None:
        return ""
    if isinstance(valor, (list, tuple)):
        return ", ".join(_texto(v, tope) for v in valor)[:tope]
    return str(valor)[:tope]


async def _traer(cliente: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """Un GET con tope de tamaño. Devuelve None si algo salió mal."""
    try:
        respuesta = await cliente.get(url)
        respuesta.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("fuente_fallo", url=url[:120], error_type=type(exc).__name__)
        return None
    if len(respuesta.content) > MAX_BYTES:
        logger.warning("fuente_demasiado_grande", url=url[:120], bytes=len(respuesta.content))
        return None
    return respuesta


def _json_de(respuesta: httpx.Response | None) -> object | None:
    if respuesta is None:
        return None
    try:
        return respuesta.json()
    except (json.JSONDecodeError, ValueError):
        logger.warning("fuente_json_invalido", url=str(respuesta.url)[:120])
        return None


# --- RemoteOK ---


async def remoteok(cliente: httpx.AsyncClient) -> list[Oferta]:
    """https://remoteok.com/api — JSON abierto, sin credenciales.

    El primer elemento del array no es una oferta: es el aviso legal que pide
    enlazar de vuelta. Se descarta por forma (no tiene `id`), no por posición,
    porque el día que lo saquen esto seguiría andando.
    """
    crudo = _json_de(await _traer(cliente, "https://remoteok.com/api"))
    if not isinstance(crudo, list):
        return []

    ofertas: list[Oferta] = []
    for item in crudo:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ofertas.append(
            Oferta(
                fuente="remoteok",
                id_externo=_texto(item.get("id"), 64),
                titulo=_texto(item.get("position"), 300),
                empresa=_texto(item.get("company"), 200),
                url=_texto(item.get("url"), 600) or f"https://remoteok.com/l/{item.get('id')}",
                descripcion=_texto_plano(_texto(item.get("description"))),
                ubicacion=_texto(item.get("location"), 200),
                publicada=_texto(item.get("date"), 40),
                salario=_sueldo(item.get("salary_min"), item.get("salary_max")),
                etiquetas=tuple(_texto(t, 60) for t in (item.get("tags") or [])[:20]),
            )
        )
    return ofertas


def _sueldo(minimo: object, maximo: object) -> str:
    partes = [_texto(x, 20) for x in (minimo, maximo) if x]
    return " - ".join(partes)


# --- Remotive ---


async def remotive(cliente: httpx.AsyncClient) -> list[Oferta]:
    """https://remotive.com/api/remote-jobs — JSON abierto.

    Es la fuente que más sirve para el caso de este perfil porque trae
    `candidate_required_location` como campo propio: "Worldwide", "USA Only",
    "LATAM". Las demás obligan a sacar eso leyendo la descripción, que acierta
    menos.
    """
    crudo = _json_de(await _traer(cliente, "https://remotive.com/api/remote-jobs"))
    trabajos = crudo.get("jobs") if isinstance(crudo, dict) else None
    if not isinstance(trabajos, list):
        return []

    ofertas: list[Oferta] = []
    for item in trabajos:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ofertas.append(
            Oferta(
                fuente="remotive",
                id_externo=_texto(item.get("id"), 64),
                titulo=_texto(item.get("title"), 300),
                empresa=_texto(item.get("company_name"), 200),
                url=_texto(item.get("url"), 600),
                descripcion=_texto_plano(_texto(item.get("description"))),
                ubicacion=_texto(item.get("candidate_required_location"), 200),
                publicada=_texto(item.get("publication_date"), 40),
                salario=_texto(item.get("salary"), 120),
                etiquetas=tuple(_texto(t, 60) for t in (item.get("tags") or [])[:20]),
            )
        )
    return ofertas


# --- We Work Remotely ---

# Las categorías que tienen que ver con este perfil. El feed general trae también
# diseño, marketing y soporte, que acá solo serían ruido que hay que puntuar.
CATEGORIAS_WWR = (
    "remote-programming-jobs",
    "remote-devops-sysadmin-jobs",
    "remote-full-stack-programming-jobs",
)


async def weworkremotely(cliente: httpx.AsyncClient) -> list[Oferta]:
    """RSS oficial por categoría."""
    ofertas: list[Oferta] = []
    for categoria in CATEGORIAS_WWR:
        respuesta = await _traer(cliente, f"https://weworkremotely.com/categories/{categoria}.rss")
        if respuesta is not None:
            ofertas.extend(_rss_a_ofertas(respuesta.text))
    return ofertas


def _rss_a_ofertas(xml: str) -> list[Oferta]:
    """Parsea el RSS.

    `ET` marca S405/S314 por las entidades externas de XML. El riesgo real que
    queda en Python 3.12 es la bomba de entidades, y contra eso está el tope de
    `MAX_BYTES` que ya se aplicó al descargar: `ET` no expande entidades
    externas ni resuelve DTD remotos por su cuenta. No se agrega `defusedxml`
    como dependencia para un solo feed.
    """
    try:
        raiz = ET.fromstring(xml)  # noqa: S314 - ver el docstring
    except ET.ParseError as exc:
        logger.warning("wwr_rss_invalido", error_type=type(exc).__name__)
        return []

    ofertas: list[Oferta] = []
    for item in raiz.iter("item"):
        enlace = (item.findtext("link") or "").strip()
        if not enlace:
            continue
        # WWR pone "Empresa: Puesto" en el título, en un solo campo.
        crudo = (item.findtext("title") or "").strip()
        empresa, _, puesto = crudo.partition(":")
        if not puesto:
            empresa, puesto = "", crudo
        ofertas.append(
            Oferta(
                fuente="weworkremotely",
                # El id del feed es el link; la última parte es estable y corta.
                id_externo=enlace.rstrip("/").rsplit("/", 1)[-1][:120],
                titulo=puesto.strip()[:300],
                empresa=empresa.strip()[:200],
                url=enlace[:600],
                descripcion=_texto_plano(item.findtext("description") or ""),
                ubicacion=(item.findtext("region") or "").strip()[:200],
                publicada=(item.findtext("pubDate") or "").strip()[:60],
            )
        )
    return ofertas


# --- Hacker News: "Ask HN: Who is hiring?" ---

# "Who is hiring?" sí; "Who wants to be hired?" y "Freelancer? Seeking
# freelancer?" no. Va anclado al principio: buscar "who is hiring" suelto
# matchea también "Who wants to be hired, and who is hiring" —un título que
# empieza por el hilo equivocado— y cualquier "Tell HN:" que lo mencione al
# pasar. El `^` es lo que hace que el filtro signifique algo.
_ES_HILO_DE_OFERTAS = re.compile(r"^ask hn:\s*who\s+is\s+hiring", re.IGNORECASE)


async def hackernews(cliente: httpx.AsyncClient) -> list[Oferta]:
    """El hilo mensual, vía la API de Algolia que HN publica para esto.

    Es la fuente donde más aparecen las dos cosas que este perfil busca y los
    boards traen poco: "visa sponsorship" y "anywhere in the world". El precio es
    que cada oferta es un comentario escrito a mano, sin campos: la empresa sale
    de la primera línea y el resto se puntúa como texto.
    """
    hilos = _json_de(
        await _traer(
            cliente,
            "https://hn.algolia.com/api/v1/search_by_date"
            "?tags=story,author_whoishiring&hitsPerPage=20",
        )
    )
    aciertos = hilos.get("hits") if isinstance(hilos, dict) else None
    if not isinstance(aciertos, list) or not aciertos:
        return []

    # El mismo autor publica DOS hilos con un segundo de diferencia: "Who is
    # hiring?" —empresas contratando— y "Who wants to be hired?" —programadores
    # ofreciéndose—. Pedir uno solo y quedarse con el primero es jugarse a que
    # Algolia los devuelva siempre en el mismo orden; el día que no, el cazador
    # trae trescientos currículums ajenos y los puntúa como si fueran ofertas.
    id_hilo = ""
    for acierto in aciertos:
        if not isinstance(acierto, dict):
            continue
        if not _ES_HILO_DE_OFERTAS.search(_texto(acierto.get("title"), 200)):
            continue
        candidato = _texto(acierto.get("objectID"), 32)
        if candidato.isdigit():
            id_hilo = candidato
            break
    if not id_hilo:
        logger.warning("hn_sin_hilo", detail="ningún 'Who is hiring?' entre los últimos hilos")
        return []

    hilo = _json_de(await _traer(cliente, f"https://hn.algolia.com/api/v1/items/{id_hilo}"))
    hijos = hilo.get("children") if isinstance(hilo, dict) else None
    if not isinstance(hijos, list):
        return []

    ofertas: list[Oferta] = []
    for comentario in hijos:
        if not isinstance(comentario, dict) or comentario.get("type") != "comment":
            continue
        texto = _texto_plano(_texto(comentario.get("text")))
        # Los comentarios borrados llegan vacíos, y los de tres palabras son
        # charla del hilo, no una oferta.
        if len(texto) < 120:
            continue
        primera = texto.splitlines()[0][:200]
        ofertas.append(
            Oferta(
                fuente="hackernews",
                id_externo=_texto(comentario.get("id"), 32),
                # La primera línea del comentario es, por costumbre del hilo,
                # "Empresa | Puesto | Remoto | Stack". No siempre, pero lo
                # suficiente como para que sirva de título.
                titulo=primera,
                empresa=primera.split("|")[0].strip()[:200],
                url=f"https://news.ycombinator.com/item?id={comentario.get('id')}",
                descripcion=texto,
                publicada=_fecha_hn(comentario.get("created_at_i")),
            )
        )
    return ofertas


def _fecha_hn(marca: object) -> str:
    try:
        return datetime.fromtimestamp(int(_texto(marca, 20)), tz=UTC).date().isoformat()
    except (ValueError, TypeError, OSError):
        return ""


# --- Upwork ---


async def upwork(cliente: httpx.AsyncClient, token: str, consulta: str) -> list[Oferta]:
    """La API GraphQL oficial, y solo esa.

    Sin `token` no se llama a Upwork **en absoluto**: no hay modo degradado que
    raspe la web. Upwork prohíbe los bots y los scrapers, y contra el auto-envío
    de propuestas la sanción es suspensión permanente sin aviso; el feed RSS lo
    cerraron en agosto de 2024 justamente por eso. Traer los links con la API
    aprobada está permitido; postular sigue siendo a mano, acá y en cualquier
    versión futura de esto, porque la API ni siquiera expone una mutation para
    enviar una propuesta.

    ⚠ Sin key aprobada no se pudo ejercitar contra el servidor real, así que la
    forma de la respuesta está escrita según la documentación y no verificada.
    `python -m empleo.cazador --probar` imprime lo que devuelve para corregirla
    en una sola corrida.
    """
    if not token:
        logger.info("upwork_sin_token", detail="fuente apagada: requiere API key aprobada")
        return []

    consulta_gql = """
    query ($req: MarketplaceJobPostingsSearchFilter!) {
      marketplaceJobPostingsSearch(marketPlaceJobFilter: $req) {
        edges { node {
          id title description ciphertext
          amount { rawValue currency }
          createdDateTime
        } }
      }
    }
    """
    try:
        respuesta = await cliente.post(
            "https://api.upwork.com/graphql",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": consulta_gql,
                "variables": {
                    "req": {"searchExpression_eq": consulta, "pagination_eq": {"first": 50}}
                },
            },
        )
        respuesta.raise_for_status()
        crudo = respuesta.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("upwork_fallo", error_type=type(exc).__name__)
        return []

    bordes = (((crudo or {}).get("data") or {}).get("marketplaceJobPostingsSearch") or {}).get(
        "edges"
    )
    if not isinstance(bordes, list):
        logger.warning("upwork_forma_inesperada", detail="usar --probar para ver la respuesta")
        return []

    ofertas: list[Oferta] = []
    for borde in bordes:
        nodo = (borde or {}).get("node") if isinstance(borde, dict) else None
        if not isinstance(nodo, dict) or not nodo.get("id"):
            continue
        cifrado = _texto(nodo.get("ciphertext"), 64)
        ofertas.append(
            Oferta(
                fuente="upwork",
                id_externo=_texto(nodo.get("id"), 64),
                titulo=_texto(nodo.get("title"), 300),
                empresa="",
                url=f"https://www.upwork.com/jobs/{cifrado}" if cifrado else "",
                descripcion=_texto_plano(_texto(nodo.get("description"))),
                publicada=_texto(nodo.get("createdDateTime"), 40),
                salario=_texto((nodo.get("amount") or {}).get("rawValue"), 40),
            )
        )
    return ofertas


# --- Orquestación ---

Adaptador = Callable[[httpx.AsyncClient], Awaitable[list[Oferta]]]


def cliente_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": AGENTE, "Accept": "application/json, application/rss+xml, */*"},
    )
