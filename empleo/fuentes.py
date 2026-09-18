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

import email
import html
import imaplib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET  # noqa: S405 - ver _rss_a_ofertas
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header

import httpx

from api.logging import get_logger
from empleo.oferta import Oferta
from empleo.postulaciones import Respuesta, clasificar

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


async def _traer(
    cliente: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES
) -> httpx.Response | None:
    """Un GET con tope de tamaño. Devuelve None si algo salió mal.

    El tope se puede subir por fuente: el board de una empresa grande con las
    descripciones incluidas es legítimamente enorme —Anthropic devuelve 8,7 MB—
    y no es el volcado de datos contra el que existe el tope general.
    """
    try:
        respuesta = await cliente.get(url)
        respuesta.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("fuente_fallo", url=url[:120], error_type=type(exc).__name__)
        return None
    if len(respuesta.content) > max_bytes:
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


# --- Get on Board: el board latinoamericano ---

# Cuántas búsquedas se hacen por vuelta. La API pide `query` obligatorio —sin él
# devuelve `unprocessable_content`—, así que hay que elegir con qué preguntar.
# Cada término es una llamada; seis alcanzan para cubrir el perfil sin convertir
# una vuelta del cazador en veinte pedidos a un board que no cobra por esto.
TOPE_CONSULTAS_GETONBRD = 6
POR_CONSULTA_GETONBRD = 30


async def getonbrd(cliente: httpx.AsyncClient, consultas: tuple[str, ...]) -> list[Oferta]:
    """https://www.getonbrd.com/api/v0/search/jobs — JSON público, sin key.

    Es la única de las fuentes que nace en la región: las empresas que publican
    acá ya contratan latinoamericanos, así que una oferta suya no necesita que
    nadie patrocine nada. Las otras cuatro son boards globales donde "LATAM"
    aparece si la empresa se acordó de escribirlo.

    `query` es obligatorio, así que las búsquedas salen de los términos
    `fuerte` del TOML: el criterio de qué se busca vive en un solo lugar y
    cambiarlo ahí cambia esto, sin tocar código.

    Los duplicados entre términos —una oferta que menciona "rag" y "llm"— se
    sacan acá por id, antes de que el cazador los vea: deduplicar dentro de una
    fuente es asunto de la fuente.
    """
    if not consultas:
        return []

    ofertas: dict[str, Oferta] = {}
    for consulta in consultas[:TOPE_CONSULTAS_GETONBRD]:
        respuesta = await _traer(
            cliente,
            "https://www.getonbrd.com/api/v0/search/jobs"
            f"?query={urllib.parse.quote(consulta)}&per_page={POR_CONSULTA_GETONBRD}",
        )
        crudo = _json_de(respuesta)
        datos = crudo.get("data") if isinstance(crudo, dict) else None
        if not isinstance(datos, list):
            continue
        for item in datos:
            if not isinstance(item, dict):
                continue
            clave = _texto(item.get("id"), 120)
            atributos = item.get("attributes")
            if not clave or clave in ofertas or not isinstance(atributos, dict):
                continue
            ofertas[clave] = _oferta_getonbrd(clave, atributos, item.get("links"))
    return list(ofertas.values())


def _oferta_getonbrd(clave: str, atributos: dict[str, object], enlaces: object = None) -> Oferta:
    """Una oferta de Get on Board, con los campos que el puntuador sabe leer."""
    # `links.public_url` es la URL que publica el board. Se prefiere a armarla
    # con el slug: si mañana cambian el formato, el link sigue llevando a la
    # oferta en vez de a un 404.
    url_publica = ""
    if isinstance(enlaces, dict):
        url_publica = _texto(enlaces.get("public_url"), 600)
    # La empresa se deja VACÍA a propósito. `company` solo trae
    # `{"data": {"id": N}}` y resolver el nombre costaría una llamada por cada
    # una de las treinta ofertas de cada búsqueda. Sacarlo del slug —que
    # termina en "<algo>-remote"— se probó y acierta 1 de 5: los slugs terminan
    # en país, ciudad o un hash, así que "Us", "Ai" y "42C5" se leían como el
    # nombre de la empresa. Un nombre inventado en la línea del aviso es peor
    # que ninguno: el título ya dice qué es y el link dice quién.
    #
    # `Oferta.huella` usa empresa+puesto para no repetir la misma búsqueda
    # publicada en dos boards; con la empresa vacía la huella queda en el
    # puesto, que para esta fuente alcanza.
    empresa = ""

    # La ubicación se arma con lo que el board separa en campos: `remote_zone`
    # dice desde dónde se puede trabajar —lo que decide si la oferta sirve— y
    # `countries` trae "Remote" o la lista de países. Juntarlos deja que las
    # señales de `criterio.py` (latam, remoto_global) los encuentren donde ya
    # las buscan, sin inventar un campo nuevo.
    partes = [_texto(atributos.get("remote_zone"), 100), _modalidad_getonbrd(atributos)]
    paises = atributos.get("countries")
    if isinstance(paises, list):
        partes += [_texto(pais, 60) for pais in paises[:6]]
    ubicacion = ", ".join(p for p in partes if p)

    return Oferta(
        fuente="getonbrd",
        id_externo=clave,
        titulo=_texto(atributos.get("title"), 300),
        empresa=empresa,
        url=url_publica or f"https://www.getonbrd.com/jobs/{clave}"[:600],
        descripcion=_texto_plano(_texto(atributos.get("description"))),
        ubicacion=ubicacion[:200],
        publicada=_fecha_getonbrd(atributos.get("published_at")),
        salario=_sueldo(atributos.get("min_salary"), atributos.get("max_salary")),
        etiquetas=tuple(_texto(t, 60) for t in (atributos.get("tags") or [])[:20])
        if isinstance(atributos.get("tags"), list)
        else (),
    )


# Las claves donde puede venir la modalidad —presencial, híbrido, remoto local,
# remoto total—. Son varias porque la documentación del board está detrás de un
# dominio que no se pudo alcanzar desde donde se escribió esto, así que el
# nombre exacto del campo no está verificado.
#
# Probar varias y quedarse con la primera que traiga texto es más barato que
# acertar: si ninguna existe, el valor queda vacío y la clasificación la hace
# igual la señal de texto de `criterio.py` sobre el título y la descripción,
# que es donde "híbrido" aparece de todos modos. `--probar` imprime la oferta
# entera para fijar la clave correcta en una sola corrida.
CLAVES_MODALIDAD = ("modality", "remote_modality", "remote_kind", "work_mode", "modalidad")


def _modalidad_getonbrd(atributos: dict[str, object]) -> str:
    """La modalidad declarada por el board, si alguna de las claves la trae.

    Va a `ubicacion`, que ya entra en `Oferta.buscable()`: así la señal de
    `criterio.py` la encuentra donde ya mira, sin agregar un campo que sólo una
    fuente sabría llenar.
    """
    for clave in CLAVES_MODALIDAD:
        valor = atributos.get(clave)
        # El board podría mandarlo anidado como {"data": {...}}; sólo interesa
        # cuando es un texto suelto que se pueda leer.
        if isinstance(valor, str) and valor.strip():
            return valor.strip()[:60]
    return ""


def _fecha_getonbrd(marca: object) -> str:
    """`published_at` llega como marca de tiempo Unix, no como fecha ISO."""
    try:
        return datetime.fromtimestamp(int(_texto(marca, 20)), tz=UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


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


# --- LinkedIn: las alertas que llegan al correo ---

# LinkedIn no publica feed y prohíbe el raspado, pero manda las alertas de
# empleo por mail: eso es correo propio y leerlo no es scraping. La dirección
# es fija desde hace años; `linkedin@em.linkedin.com` y `notifications-` son
# encuestas y avisos de perfil, no ofertas.
REMITENTE_LINKEDIN = "jobalerts-noreply@linkedin.com"
IMAP_GMAIL = "imap.gmail.com"
# Cuántos correos se leen por vuelta. Las alertas llegan una o dos veces al día
# por búsqueda guardada; treinta cubre varios días de varias alertas sin que una
# vuelta del cazador se convierta en una descarga de medio buzón.
TOPE_CORREOS_LINKEDIN = 30

# El cuerpo en texto plano viene en bloques de tres líneas separados por una
# raya: título, empresa, ubicación, y una línea "View job: <url>". La ubicación
# a veces falta —cuando la oferta no la declara— y a veces hay líneas sueltas
# entre medio ("This company is actively hiring", "Apply with resume").
_SEPARADOR_LINKEDIN = re.compile(r"^-{10,}$", re.MULTILINE)
_VER_OFERTA = re.compile(r"View job:\s*(https://\S+)")
_ID_OFERTA = re.compile(r"/jobs/view/(\d+)")
# Las líneas de adorno que LinkedIn intercala y que no son ni empresa ni lugar.
_RUIDO_LINKEDIN = re.compile(
    r"^(this company is actively hiring|apply with resume|be an early applicant"
    r"|easy apply|promoted|view job:|see all jobs|your job alert|new jobs match)",
    re.IGNORECASE,
)


def ofertas_de_alerta_linkedin(cuerpo: str, fecha: str = "") -> list[Oferta]:
    """Las ofertas de UN correo de alerta, ya en texto plano.

    Se separa del acceso al buzón a propósito: parsear el formato de LinkedIn
    es lo que se rompe cuando ellos cambian la plantilla, y así se prueba con
    un correo pegado en un test en vez de con una cuenta de verdad.
    """
    ofertas: list[Oferta] = []
    for bloque in _SEPARADOR_LINKEDIN.split(cuerpo):
        enlace = _VER_OFERTA.search(bloque)
        if not enlace:
            continue
        url = enlace.group(1)
        identificador = _ID_OFERTA.search(url)
        if not identificador:
            continue

        # Las líneas útiles del bloque, en orden, sin el adorno de LinkedIn.
        lineas = [
            linea.strip()
            for linea in bloque.splitlines()
            if linea.strip() and not _RUIDO_LINKEDIN.match(linea.strip())
        ]
        if not lineas:
            continue
        titulo = lineas[0]
        empresa = lineas[1] if len(lineas) > 1 else ""
        ubicacion = lineas[2] if len(lineas) > 2 else ""

        ofertas.append(
            Oferta(
                fuente="linkedin",
                id_externo=identificador.group(1)[:64],
                titulo=titulo[:300],
                empresa=empresa[:200],
                # El link de seguimiento lleva un token de sesión larguísimo y
                # personal. Se queda la URL canónica, que es la misma oferta sin
                # el rastreo — y entra en el aviso sin comerse el mensaje.
                url=f"https://www.linkedin.com/jobs/view/{identificador.group(1)}",
                # El correo no trae la descripción, solo el encabezado. El
                # puntuador va a tener menos texto donde buscar el stack; a
                # cambio, estas ofertas no llegan por ningún otro lado.
                descripcion=f"{titulo}\n{empresa}\n{ubicacion}",
                ubicacion=ubicacion[:200],
                publicada=fecha[:60],
            )
        )
    return ofertas


def linkedin_por_imap(usuario: str, clave: str, dias: int = 3) -> list[Oferta]:
    """Las alertas de los últimos `dias`, leídas del buzón por IMAP.

    **Esto no es scraping**: LinkedIn manda estos correos al usuario, y leer el
    propio buzón con una contraseña de aplicación es lo que esa contraseña
    existe para hacer. Es también la única vía legítima a LinkedIn e Indeed, que
    no publican feed y prohíben el raspado.

    Es síncrona —`imaplib` lo es— y por eso el cazador la corre en un hilo: una
    conexión IMAP bloqueando el bucle dejaría a las otras cinco fuentes
    esperando.

    Nunca lanza: sin credenciales, con la clave vencida o con Gmail caído, el
    cazador sigue con las demás fuentes.
    """
    if not usuario or not clave:
        logger.info("linkedin_sin_credenciales", detail="fuente apagada: falta GMAIL_APP_PASSWORD")
        return []

    desde = (datetime.now(tz=UTC) - timedelta(days=dias)).strftime("%d-%b-%Y")
    try:
        with imaplib.IMAP4_SSL(IMAP_GMAIL) as buzon:
            buzon.login(usuario, clave)
            # Solo lectura: esto no marca como leído ni mueve nada de tu correo.
            buzon.select("INBOX", readonly=True)
            estado, respuesta = buzon.search(None, f'(FROM "{REMITENTE_LINKEDIN}" SINCE "{desde}")')
            if estado != "OK" or not respuesta or not respuesta[0]:
                return []
            identificadores = respuesta[0].split()[-TOPE_CORREOS_LINKEDIN:]

            ofertas: dict[str, Oferta] = {}
            for identificador in identificadores:
                estado, datos = buzon.fetch(identificador, "(RFC822)")
                if estado != "OK" or not datos or not isinstance(datos[0], tuple):
                    continue
                for oferta in _ofertas_del_correo(datos[0][1]):
                    # La misma oferta aparece en varias alertas: es una sola.
                    ofertas.setdefault(oferta.id_externo, oferta)
            return list(ofertas.values())
    except (OSError, imaplib.IMAP4.error) as exc:
        logger.warning("linkedin_imap_fallo", error_type=type(exc).__name__)
        return []


def _ofertas_del_correo(crudo: bytes) -> list[Oferta]:
    """Saca el texto plano de un correo MIME y lo pasa al parser."""
    mensaje = email.message_from_bytes(crudo)
    fecha = _texto(mensaje.get("Date"), 60)
    cuerpo = ""
    for parte in mensaje.walk() if mensaje.is_multipart() else [mensaje]:
        if parte.get_content_type() != "text/plain":
            continue
        carga = parte.get_payload(decode=True)
        if isinstance(carga, bytes):
            cuerpo = carga.decode(parte.get_content_charset() or "utf-8", errors="replace")
            break
    return ofertas_de_alerta_linkedin(cuerpo, fecha) if cuerpo else []


# --- Empresas, por su propio sistema de postulación ---

# Las tres plataformas que usan casi todas las empresas de software para su
# página de "Careers", y que publican el listado como JSON **sin autenticación**:
# es la API que alimenta su propia página, no un raspado.
#
# Sirve para lo que los boards no dan: llegar a una empresa concreta el día que
# abre el puesto, sin esperar a que lo publique en un agregador — muchas grandes
# nunca lo hacen.
# Un board grande con `content=true` pasa con holgura el tope general: medido,
# el de Anthropic devuelve 8,7 MB de puro JSON legítimo. Con el tope de 8 MB esa
# empresa se caía entera y en silencio.
MAX_BYTES_EMPRESA = 30_000_000

ATS_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}


async def empresas(
    cliente: httpx.AsyncClient, listado: tuple[tuple[str, str, str], ...]
) -> list[Oferta]:
    """Los puestos abiertos de cada empresa de la lista. `(nombre, ats, token)`.

    Una empresa que falla no arrastra a las demás: un token equivocado o una
    empresa que se cambió de plataforma es lo más común acá, y tiene que costar
    esa empresa y no el aviso entero.
    """
    salida: list[Oferta] = []
    for nombre, ats, token in listado:
        plantilla = ATS_URL.get(ats.lower())
        if not plantilla or not token:
            logger.warning("empresa_mal_configurada", empresa=nombre[:80], ats=ats[:40])
            continue
        crudo = _json_de(
            await _traer(
                cliente,
                plantilla.format(token=urllib.parse.quote(token)),
                max_bytes=MAX_BYTES_EMPRESA,
            )
        )
        if crudo is None:
            continue
        try:
            salida.extend(_LECTORES_ATS[ats.lower()](nombre, crudo))
        except (TypeError, ValueError, AttributeError) as exc:
            # La forma cambió: cuesta esa empresa, no la corrida.
            logger.warning(
                "empresa_forma_inesperada", empresa=nombre[:80], error_type=type(exc).__name__
            )
    return salida


def _greenhouse(nombre: str, crudo: object) -> list[Oferta]:
    """Greenhouse manda la descripción como HTML **escapado** dentro del JSON."""
    puestos = crudo.get("jobs") if isinstance(crudo, dict) else None
    if not isinstance(puestos, list):
        return []
    salida = []
    for item in puestos:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        lugar = item.get("location")
        salida.append(
            Oferta(
                fuente="empresas",
                id_externo=f"greenhouse:{_texto(item.get('id'), 40)}",
                titulo=_texto(item.get("title"), 300),
                empresa=nombre,
                url=_texto(item.get("absolute_url"), 600),
                # Sin `unescape` la descripción llega como "&lt;p&gt;" literal y
                # ni las señales ni la brecha contra el CV encuentran nada.
                descripcion=_texto_plano(html.unescape(_texto(item.get("content")))),
                ubicacion=_texto(lugar.get("name"), 200) if isinstance(lugar, dict) else "",
                publicada=_texto(item.get("updated_at"), 40),
            )
        )
    return salida


def _lever(nombre: str, crudo: object) -> list[Oferta]:
    """Lever devuelve la lista pelada, sin envoltorio."""
    if not isinstance(crudo, list):
        return []
    salida = []
    for item in crudo:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        categorias = item.get("categories")
        categorias = categorias if isinstance(categorias, dict) else {}
        salida.append(
            Oferta(
                fuente="empresas",
                id_externo=f"lever:{_texto(item.get('id'), 80)}",
                titulo=_texto(item.get("text"), 300),
                empresa=nombre,
                url=_texto(item.get("hostedUrl"), 600),
                descripcion=_texto_plano(_texto(item.get("descriptionPlain"))),
                # `commitment` entra a la ubicación para que "Full-time" y
                # "Contract" los vean las señales donde ya miran.
                ubicacion=", ".join(
                    p
                    for p in (
                        _texto(categorias.get("location"), 120),
                        _texto(categorias.get("commitment"), 60),
                        _texto(categorias.get("workplaceType"), 40),
                    )
                    if p
                )[:200],
                publicada=_fecha_lever(item.get("createdAt")),
            )
        )
    return salida


def _fecha_lever(marca: object) -> str:
    """Lever manda epoch en MILISEGUNDOS: leerlo como segundos da el año 1970."""
    try:
        return datetime.fromtimestamp(int(_texto(marca, 20)) / 1000, tz=UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _ashby(nombre: str, crudo: object) -> list[Oferta]:
    puestos = crudo.get("jobs") if isinstance(crudo, dict) else None
    if not isinstance(puestos, list):
        return []
    salida = []
    for item in puestos:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        # `isRemote` es un booleano propio de Ashby: se traduce a texto para que
        # lo lea la misma señal que lee la prosa de las demás fuentes.
        remoto = "remote" if item.get("isRemote") is True else ""
        salida.append(
            Oferta(
                fuente="empresas",
                id_externo=f"ashby:{_texto(item.get('id'), 80)}",
                titulo=_texto(item.get("title"), 300),
                empresa=nombre,
                url=_texto(item.get("jobUrl"), 600),
                descripcion=_texto_plano(_texto(item.get("descriptionPlain"))),
                ubicacion=", ".join(
                    p
                    for p in (
                        _texto(item.get("location"), 120),
                        _texto(item.get("employmentType"), 40),
                        remoto,
                    )
                    if p
                )[:200],
                publicada=_texto(item.get("publishedAt"), 40),
            )
        )
    return salida


# --- Workday ---

# ⚠ ESTA ES LA EXCEPCIÓN A LA REGLA DEL MÓDULO, Y ESTÁ ACÁ PARA QUE SE VEA.
#
# Greenhouse, Lever y Ashby **documentan** su API pública de empleos. Workday
# no: esto es el endpoint que su propia página de Careers llama por dentro. No
# pide autenticación y devuelve JSON —no se raspa HTML—, pero tampoco hay una
# promesa pública de que siga existiendo ni de que se pueda usar así. Se agregó
# a pedido explícito, sabiendo eso, porque es la única vía a empresas grandes
# que no publican en ningún agregador.
#
# Consecuencias prácticas de que no esté documentado, las tres medidas:
#
# 1. El listado NO trae la descripción. Sin ella, la señal de híbrido y la
#    brecha contra el CV quedan casi ciegas: por eso se pide el detalle, pero
#    sólo de las ofertas cuyo título ya coincide con algo que buscás y con un
#    tope duro. Pedir 500 detalles sería golpear su servidor por nada.
# 2. `postedOn` no es una fecha: es texto relativo ("Posted Today", "Posted 30+
#    Days Ago"). Se traduce a horas aproximadas, y se dice que es aproximado.
# 3. La forma puede cambiar sin aviso. Todo se lee defensivamente y, si no se
#    entiende, esa empresa aporta cero en vez de tirar la corrida.

# Cuántas ofertas se piden en el listado y cuántos detalles se buscan después.
POR_PAGINA_WORKDAY = 20
TOPE_DETALLES_WORKDAY = 15

_HOST_WORKDAY = re.compile(r"^https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[\w-]+/)?([\w-]+)")

# "Posted 30+ Days Ago" y compañía. Workday no publica la fecha real en el
# listado, así que esto es una aproximación declarada, no una medición.
_HACE_DIAS = re.compile(r"(\d+)\+?\s*days?", re.I)


def partes_de_workday(url: str) -> tuple[str, str, str] | None:
    """`(tenant, shard, site)` a partir de la URL de su página de empleos.

    Se pide la URL entera y no un token corto porque Workday necesita tres
    datos, y los tres están a la vista en esa dirección: nadie los adivina, y
    copiarla del navegador no se equivoca.
    """
    encontrado = _HOST_WORKDAY.match(url.strip())
    return (encontrado.group(1), encontrado.group(2), encontrado.group(3)) if encontrado else None


def _antiguedad_workday(texto: str) -> str:
    """El texto relativo de Workday, como fecha ISO aproximada."""
    bajo = texto.lower()
    if "today" in bajo or "hoy" in bajo:
        dias = 0
    elif "yesterday" in bajo or "ayer" in bajo:
        dias = 1
    else:
        encontrado = _HACE_DIAS.search(bajo)
        if not encontrado:
            return ""
        dias = int(encontrado.group(1))
    return (datetime.now(tz=UTC) - timedelta(days=dias)).isoformat()


async def workday(
    cliente: httpx.AsyncClient,
    listado: tuple[tuple[str, str, str], ...],
    consultas: tuple[str, ...],
) -> list[Oferta]:
    """Los puestos de las empresas en Workday. `token` es la URL de sus empleos."""
    salida: list[Oferta] = []
    for nombre, _ats, url in listado:
        partes = partes_de_workday(url)
        if partes is None:
            logger.warning("workday_url_invalida", empresa=nombre[:80])
            continue
        salida.extend(await _una_empresa_workday(cliente, nombre, partes, consultas))
    return salida


async def _una_empresa_workday(
    cliente: httpx.AsyncClient,
    nombre: str,
    partes: tuple[str, str, str],
    consultas: tuple[str, ...],
) -> list[Oferta]:
    tenant, shard, site = partes
    base = f"https://{tenant}.{shard}.myworkdayjobs.com"
    crudo = _json_de(
        await _traer_post(
            cliente,
            f"{base}/wday/cxs/{tenant}/{site}/jobs",
            {"appliedFacets": {}, "limit": POR_PAGINA_WORKDAY, "offset": 0, "searchText": ""},
        )
    )
    puestos = crudo.get("jobPostings") if isinstance(crudo, dict) else None
    if not isinstance(puestos, list):
        logger.warning("workday_forma_inesperada", empresa=nombre[:80])
        return []

    ofertas: list[Oferta] = []
    detalles_pedidos = 0
    for item in puestos:
        if not isinstance(item, dict):
            continue
        ruta = _texto(item.get("externalPath"), 300)
        titulo = _texto(item.get("title"), 300)
        if not ruta or not titulo:
            continue

        # El detalle cuesta una llamada, así que sólo se pide cuando el título
        # ya dice que la oferta puede interesar. El resto entra sin descripción:
        # se ve en el digest, puntúa bajo, y no se gastó nada en traerla.
        descripcion = ""
        interesa = any(c in titulo.lower() for c in consultas)
        if interesa and detalles_pedidos < TOPE_DETALLES_WORKDAY:
            detalles_pedidos += 1
            descripcion = await _detalle_workday(cliente, base, tenant, site, ruta)

        ofertas.append(
            Oferta(
                fuente="workday",
                id_externo=f"{tenant}:{ruta}"[:200],
                titulo=titulo,
                empresa=nombre,
                url=f"{base}/en-US/{site}{ruta}"[:600],
                descripcion=descripcion,
                ubicacion=_texto(item.get("locationsText"), 200),
                publicada=_antiguedad_workday(_texto(item.get("postedOn"), 80)),
            )
        )
    return ofertas


async def _detalle_workday(
    cliente: httpx.AsyncClient, base: str, tenant: str, site: str, ruta: str
) -> str:
    crudo = _json_de(await _traer(cliente, f"{base}/wday/cxs/{tenant}/{site}{ruta}"))
    info = crudo.get("jobPostingInfo") if isinstance(crudo, dict) else None
    if not isinstance(info, dict):
        return ""
    return _texto_plano(_texto(info.get("jobDescription")))


async def _traer_post(cliente: httpx.AsyncClient, url: str, cuerpo: dict) -> httpx.Response | None:
    """Como `_traer`, pero POST: el listado de Workday no se pide con GET."""
    try:
        respuesta = await cliente.post(url, json=cuerpo)
        respuesta.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("fuente_fallo", url=url[:120], error_type=type(exc).__name__)
        return None
    return respuesta


_LECTORES_ATS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


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


def identificar_empresa(url: str) -> tuple[str, str] | None:
    """`(ats, token)` a partir de la URL de la página de empleos de una empresa.

    Existe porque el token NO se adivina. Sourcegraph es `sourcegraph91`, con un
    número pegado que nadie deduce del nombre; y una empresa que se cambió de
    plataforma responde 404 sin decir por qué. La URL, en cambio, está a la vista
    en el navegador y trae el dato exacto.
    """
    limpia = url.strip()
    patrones = (
        ("greenhouse", r"(?:job-)?boards\.greenhouse\.io/([\w-]+)"),
        ("greenhouse", r"greenhouse\.io/embed/job_board\?for=([\w-]+)"),
        ("lever", r"jobs\.lever\.co/([\w-]+)"),
        ("ashby", r"jobs\.ashbyhq\.com/([\w-]+)"),
    )
    for ats, patron in patrones:
        encontrado = re.search(patron, limpia, re.I)
        if encontrado:
            return ats, encontrado.group(1)
    # Workday necesita los tres datos de la URL, así que su "token" es la URL.
    if partes_de_workday(limpia) is not None:
        return "workday", limpia
    return None


# --- Orquestación ---

Adaptador = Callable[[httpx.AsyncClient], Awaitable[list[Oferta]]]


def cliente_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": AGENTE, "Accept": "application/json, application/rss+xml, */*"},
    )


# --- Respuestas a tus postulaciones ---

# Cuántos correos se miran por vuelta. El buzón trae de todo; lo que importa son
# los últimos días, y pedir más es tiempo de IMAP por correos ya clasificados.
TOPE_CORREOS_POSTULACIONES = 60


def respuestas_por_imap(usuario: str, clave: str, dias: int = 14) -> list[Respuesta]:
    """Las respuestas a tus postulaciones, leídas del buzón.

    Mismo camino y misma contraseña de aplicación que las alertas de LinkedIn, y
    **solo lectura**: no marca como leído, no mueve nada, no borra nada.

    Se buscan los correos del período entero y no sólo los no leídos: que hayas
    abierto el mail no significa que hayas hecho lo que pedía —el caso que
    originó esto es un "falta el video" leído y olvidado once días—.

    Nunca lanza: sin credenciales o con Gmail caído, la vuelta sigue.
    """
    if not usuario or not clave:
        logger.info("postulaciones_sin_credenciales", detail="falta GMAIL_APP_PASSWORD")
        return []

    desde = (datetime.now(tz=UTC) - timedelta(days=dias)).strftime("%d-%b-%Y")
    try:
        with imaplib.IMAP4_SSL(IMAP_GMAIL) as buzon:
            buzon.login(usuario, clave)
            buzon.select("INBOX", readonly=True)
            estado, respuesta = buzon.search(None, f'(SINCE "{desde}")')
            if estado != "OK" or not respuesta or not respuesta[0]:
                return []
            identificadores = respuesta[0].split()[-TOPE_CORREOS_POSTULACIONES:]

            encontradas: list[Respuesta] = []
            for identificador in identificadores:
                estado, datos = buzon.fetch(identificador, "(RFC822)")
                if estado != "OK" or not datos or not isinstance(datos[0], tuple):
                    continue
                hallada = _respuesta_del_correo(datos[0][1])
                if hallada is not None:
                    encontradas.append(hallada)
            return encontradas
    except (OSError, imaplib.IMAP4.error) as exc:
        logger.warning("postulaciones_imap_fallo", error_type=type(exc).__name__)
        return []


def _respuesta_del_correo(crudo: bytes) -> Respuesta | None:
    """Clasifica un correo MIME. `None` si no es una respuesta de postulación."""
    mensaje = email.message_from_bytes(crudo)
    asunto = _texto(str(make_header(decode_header(mensaje.get("Subject", "")))), 300)
    cuerpo = ""
    for parte in mensaje.walk() if mensaje.is_multipart() else [mensaje]:
        if parte.get_content_type() != "text/plain":
            continue
        carga = parte.get_payload(decode=True)
        if isinstance(carga, bytes):
            cuerpo = carga.decode(parte.get_content_charset() or "utf-8", errors="replace")
            break
    # Sólo el principio: las plantillas dicen lo importante arriba y el pie trae
    # enlaces de baja y avisos legales que sólo agregan falsos positivos.
    estado = clasificar(asunto, cuerpo[:4000])
    if not estado:
        return None
    return Respuesta(
        id_mensaje=_texto(mensaje.get("Message-ID"), 200) or asunto,
        remitente=_texto(mensaje.get("From"), 200),
        asunto=asunto,
        fecha=_texto(mensaje.get("Date"), 60),
        estado=estado,
    )
