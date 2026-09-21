"""Qué tan cerca está una oferta de lo que buscás, y por qué.

**El puntaje lo calcula el código, nunca el modelo.** Es la misma regla que
gobierna `paper/`: el modelo sirve para explicar y redactar, no para decir un
número. Un 7B pidiéndole "puntuá esta oferta del 1 al 100" devuelve algo
plausible y distinto cada vez; acá cada punto tiene una línea que lo justifica y
se puede discutir mirando el archivo `perfil/busqueda.toml`.

**Ninguna señal descarta sola.** Una oferta que dice "US only" o "no visa
sponsorship" baja de puesto, pero aparece con la señal a la vista. Filtrar en
silencio es cómo un criterio equivocado se vuelve invisible: no verías las
ofertas que te estás perdiendo, verías menos ofertas y nada más.
"""

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from empleo.oferta import Oferta

# `logging` de la biblioteca estándar y no `api.logging`: este módulo lo usa
# también `ofertas-service`, cuya imagen instala tres paquetes y no trae `api/`.
# Importar de `api` acá rompería ese servicio al arrancar.
logger = logging.getLogger(__name__)

# Lo que se busca en el texto de la oferta. Cada patrón se probó contra la forma
# en que estas frases aparecen de verdad, no contra la forma "correcta": nadie
# escribe "visa sponsorship is not available", escriben "we can't sponsor".
# Frases que dicen explícitamente que NO hay oficina. Si alguna aparece, las
# señales `hibrido` y `presencial` se descartan aunque sus palabras estén en el
# texto: "100% remote, no hybrid" las contiene a las dos y significa lo opuesto.
NIEGA_OFICINA = re.compile(
    r"\b((no|sin|not)\s*(a\s*)?(hybrid|h[ií]brido|on[ -]?site|in[ -]?office|office|oficina)"
    r"|fully remote|100% remote|remote[ -]first|totalmente remoto|remoto total)\b"
)

# Frases que dicen que NO hay reubicación. Mismo problema que la oficina, y peor
# consecuencia: "no relocation assistance is provided" contiene todas las
# palabras de una buena noticia y significa exactamente lo contrario. Sin esto,
# una oferta que te avisa que te mudás por tu cuenta sumaba como si te pagaran
# el pasaje.
NIEGA_REUBICACION = re.compile(
    r"\b(no|not|without|sin|nunca)\b[^.!?]{0,30}\brelocat\w*"
    r"|\brelocat\w*[^.!?]{0,30}\b(not|no longer|unavailable|isn't|is not)\b"
)

SENALES: dict[str, re.Pattern[str]] = {
    "latam": re.compile(
        r"\b(lat(?:in)?[ -]?am(?:erica)?n?|south america|central america|the americas"
        r"|argentina|colombia|mexico|m[eé]xico|brazil|brasil|venezuela|chile|per[uú]"
        r"|uruguay|ecuador|costa rica)\b"
    ),
    "remoto_global": re.compile(
        # "Remote only, Everywhere" lo escribe Wellfound como CAMPO, no como
        # prosa: es su forma de decir "remoto sin restricción de país", que es
        # exactamente lo que mide esta señal. Se agregó al leer su primer
        # digest de verdad — la oferta traía la mejor noticia posible y salía
        # con cero señales.
        r"\b(anywhere in the world|work from anywhere|worldwide|globally distributed"
        r"|fully distributed|any time ?zone|100% remote, anywhere"
        r"|remote only, everywhere)\b"
    ),
    # Reubicación. El patrón viejo pedía casi la frase exacta y se perdía la
    # mitad de las formas reales de decirlo: "we offer relocation", "includes
    # relocation", "we sponsor visas and pay for relocation". Medido sobre doce
    # frases sacadas de ofertas, seis no se detectaban.
    "reubicacion": re.compile(
        r"\b(relocat\w*[ ,:-]{0,3}(package|assistance|support|bonus|allowance|stipend"
        r"|provided|offered|included|available|paid|covered|yes)"
        r"|(offer|provide|includ|cover|pay|fund|sponsor|assist|help|handl)\w*"
        r"[^.!?]{0,30}\brelocat\w*"
        r"|full relocation|relocation and visa|visa[^.!?]{0,30}relocat\w*)"
    ),
    "contractor": re.compile(
        r"\b(contractor|c2c|corp[ -]to[ -]corp|employer of record|eor|1099|deel"
        r"|remote\.com|independent contractor)\b"
    ),
    "freelance": re.compile(r"\b(freelance|per[ -]project|hourly rate|short[ -]term contract)\b"),
    # Trabajo que sigue después del primer entregable. En Upwork esto es lo que
    # separa un contrato de una changa: el costo de conseguir al cliente se paga
    # una vez —los Connects, la propuesta, las llamadas— y se amortiza sobre lo
    # que dure. Un fijo de $750 que sigue vale más que uno de $1.500 que no.
    #
    # ⚠ Se niega antes de afirmarse, como el patrocinio: "not a long-term role"
    # y "this is not an ongoing position" tienen todas las palabras buenas y
    # dicen lo contrario. Por eso `sin_largo_plazo` se evalúa y gana.
    "sin_largo_plazo": re.compile(
        r"\b(not?\s+(?:a\s+)?(?:long[ -]?term|ongoing|recurring)"
        r"|one[ -](?:off|time)\s+(?:project|job|task|gig)"
        r"|single\s+project\s+only|no\s+(?:ongoing|follow[ -]?up)\s+work)\b"
    ),
    # La DURACIÓN declarada, que es un campo del formulario y no prosa. Upwork la
    # pone en la tarjeta como "Est. Time: More than 6 months".
    #
    # Vale más que `largo_plazo` —que busca promesas en el texto— porque el
    # cliente la eligió de una lista al publicar: "long-term opportunity" lo
    # escribe cualquiera en el título, "More than 6 months" hay que tildarlo.
    # El nivel que pide la oferta. Es un campo del formulario, no prosa: el
    # cliente lo eligió de tres opciones al publicar.
    #
    # Dato de mercado (Vibeworker, n=127.607 publicaciones de junio de 2026):
    # Entry 8,3% · Intermediate 67,4% · Expert 24,4%. O sea que "Expert" no es
    # una etiqueta que reparten: es uno de cada cuatro.
    "pide_experto": re.compile(r"(?:^|[-|·•]\s*)expert\b(?!\s*(?:level\s*)?not)", re.I),
    "duracion_larga": re.compile(r"est\.?\s*time:\s*(more than 6 months|3 to 6 months)", re.I),
    "duracion_corta": re.compile(
        r"est\.?\s*time:\s*(less than 1 (?:week|month)|1 to 3 months)", re.I
    ),
    # Jornada completa ("30+ hrs/week").
    #
    # ⚠ Se detecta y se MUESTRA, pero no suma ni resta: no tiene peso en
    # `DEFECTOS_PREFERENCIAS` a propósito. Que una oferta pida 30+ horas no es
    # mejor ni peor, depende de cuánto tiempo tengas esa semana — y eso el
    # código no lo sabe. Ponerle un signo sería decidir por vos algo que cambia
    # de mes a mes; mostrarlo te deja decidir a vos en un segundo.
    "jornada_completa": re.compile(r"\b\d{2,}\+?\s*hrs?/week", re.I),
    "largo_plazo": re.compile(
        r"\b(long[ -]?term|ongoing (?:work|collaboration|basis|support|partnership)"
        r"|on ?going relationship|more than 6 months|3 to 6 months|6\+? months"
        r"|retainer|monthly milestones?|month[ -]to[ -]month"
        r"|potential for (?:more|additional|ongoing|future)"
        r"|room to grow|first (?:of|phase of a) (?:many|larger)"
        r"|technical partner|not a one[ -]off)\b"
    ),
    # Híbrido y presencial: el caso que originó esto es una oferta de Santiago
    # marcada "híbrida" a la que no se puede aplicar desde Estados Unidos. No
    # es un puesto peor, es un puesto imposible, y encima los boards de la
    # región están llenos.
    #
    # Se busca la negación PRIMERO y gana: "fully remote, no hybrid" y "no
    # on-site requirement" dicen lo contrario de lo que sus palabras sugieren,
    # y son frases comunes justo en las ofertas que sí sirven.
    "hibrido": re.compile(
        r"\b(h[ií]brid[oa]|hybrid|"
        r"\d+\s*(days?|d[ií]as?)\s*(a|per|por)\s*(week|semana)\s*(in|at|en)\s*"
        r"(the\s*)?(office|oficina)|"
        r"(some|algunos)\s*(days?|d[ií]as?)\s*(in|at|en)\s*(the\s*)?(office|oficina))\b"
    ),
    "presencial": re.compile(
        r"\b(presencial|on[ -]?site|in[ -]?office|in[ -]?person|"
        r"work from (our|the) office|desde (la|nuestra) oficina)\b"
    ),
    "junior": re.compile(
        r"\b(junior|jr\.?|entry[ -]level|intern(ship)?|new grad|graduate program"
        r"|0[ -–-]2 years|1[ -–-]2 years)\b"
    ),
    # El orden importa: `sin_patrocinio` se evalúa antes que `patrocinio`, porque
    # "we do not offer visa sponsorship" contiene la frase positiva adentro.
    "sin_patrocinio": re.compile(
        r"\b((can ?not|cannot|can't|do not|don't|unable to|not able to|won't|will not)"
        r"\s+(currently\s+)?(provide|offer|support)?\s*(visa\s+)?sponsor\w*"
        r"|no (visa )?sponsorship|without sponsorship|sponsorship is not"
        # "Tenés que ser residente permanente" es un NO a sponsorizar, dicho como
        # requisito. Sin esto, "permanent resident" en una oferta canadiense se
        # confundía con la buena noticia de abajo.
        r"|must (be|have|hold) (a |an )?(canadian |u\.?s\.? |american )?"
        r"(citizen|permanent resident)"
        r"|only (canadian |u\.?s\.? )?(citizens|permanent residents)"
        r"|permanent residen\w* (is )?(required|mandatory))"
    ),
    # Patrocinio. Las frases de Canadá son otras y ninguna estaba: medido sobre
    # nueve formas reales, siete no se detectaban. LMIA es el instrumento con el
    # que un empleador canadiense contrata a alguien de afuera, y Global Talent
    # Stream es la vía rápida para puestos de tecnología.
    "patrocinio": re.compile(
        r"\b(visa sponsorship|we sponsor|sponsorship (is )?(available|provided|offered)"
        r"|h-?1b|green card|work permit"
        r"|lmia|global talent stream"
        r"|open to (international|foreign) (candidates|applicants)"
        # Como lo dice Job Bank, palabra por palabra, en el pie de la alerta:
        # es el filtro `fglo=1` del portal, el que deja pasar sólo las ofertas
        # cuyo empleador declaró que considera candidatos de afuera.
        r"|canadians and international candidates|candidats internationaux"
        r"|immigration (support|assistance|sponsorship)"
        r"|(path|pathway) to (permanent residency|pr)"
        r"|we (will )?(support|sponsor|handle|cover)[^.!?]{0,25}"
        r"(work permit|visa|immigration|relocation to))\b"
    ),
    # De dónde es el CLIENTE que publica, no dónde hay que estar para trabajar
    # —eso es `solo_us`, que es otra cosa y ya existe—. En Upwork la pantalla de
    # búsqueda lo pone como última línea de la tarjeta.
    #
    # Se buscan las dos listas por separado y ninguna descarta sola: una oferta
    # de un país castigado que sea excepcional en todo lo demás todavía puede
    # asomar, con el país a la vista. Filtrar en silencio es cómo un criterio
    # equivocado se vuelve invisible.
    "cliente_norteamerica": re.compile(
        r"^\s*(united states|u\.?s\.?a?\.?|usa|america|canada|canad[aá])\s*$", re.I | re.M
    ),
    "cliente_bloqueado": re.compile(
        r"^\s*(india|bharat|spain|espa[nñ]a|pakistan|bangladesh)\s*$", re.I | re.M
    ),
    "solo_us": re.compile(
        r"\b(u\.?s\.?a?[ -]only|united states only|us[ -]based (only|candidates)"
        r"|must (be |reside |live ).{0,30}(united states|u\.?s\.?a?\b)"
        r"|authorized to work in the (us|u\.?s\.?a?|united states))\b"
    ),
}


# --- En qué país está el puesto ---------------------------------------------
#
# Se mira `oferta.ubicacion` y NADA MÁS, y ésa es la decisión importante de todo
# este bloque. Buscar el país en el texto entero es el error que ya costó caro
# una vez: «some of our engineers are based in India and Spain» lo escribe una
# empresa de EE.UU. contratando afuera —o sea lo contrario de lo que parece— y
# «we serve customers across Canada» no vuelve canadiense a un puesto de Berlín.
# El campo de ubicación es el único lugar donde el país es un dato y no prosa.
#
# Sin ubicación no se adivina: el puesto queda sin país y no suma ni resta, por
# la misma razón por la que una oferta sin fecha no se castiga. Que un feed no
# mande el campo no es información sobre la oferta.

# Los códigos de dos letras se buscan EN MAYÚSCULAS y detrás de una coma:
# "Toronto, ON" es una provincia y "hands on" no. Sin esas dos condiciones, ON,
# IN, OR, OK, ME, DE, HI, LA, MS, PA y CO son palabras inglesas comunes.
_PROVINCIAS_CA = "ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU"
_ESTADOS_US = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO"
    "|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)

# El nombre del PAÍS. "USA", "US" y "U.S." van en mayúsculas a propósito: en
# minúsculas "us" es el pronombre —"join us", "work with us"— y hay feeds que
# meten media frase en el campo de ubicación.
_PAIS_CANADA = (re.compile(r"\bcanad[aá]\b", re.I),)
# El nombre largo no distingue mayúsculas; la sigla SÍ, y por eso van separados:
# "united states" escrito como sea es el país, pero "us" en minúscula es el
# pronombre y "Remote US" es el país.
_PAIS_EEUU = (re.compile(r"\bunited states\b", re.I), re.compile(r"\bU\.?S\.?A?\b"))

# La provincia o el estado. Son pistas más débiles que el nombre del país, y por
# eso se resuelven aparte: ver `pais_de`.
_PROVINCIA = re.compile(
    r"\b(ontario|quebec|qu[eé]bec|british columbia|alberta|manitoba|saskatchewan"
    r"|nova scotia|new brunswick|newfoundland|prince edward island"
    r"|yukon|northwest territories|nunavut)\b",
    re.I,
)
_PROVINCIA_CODIGO = re.compile(rf",\s*({_PROVINCIAS_CA})\b")
_ESTADO = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware"
    r"|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana"
    r"|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana"
    r"|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina"
    r"|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina"
    r"|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia"
    r"|wisconsin|wyoming|district of columbia)\b",
    re.I,
)
_ESTADO_CODIGO = re.compile(rf",\s*({_ESTADOS_US})\b")

NORTEAMERICA = ("estados_unidos", "canada")


def _primera(texto: str, patrones: tuple[re.Pattern[str], ...]) -> int:
    """Dónde empieza el PRIMER acierto de cualquiera de los patrones, o -1."""
    posiciones = [m.start() for p in patrones if (m := p.search(texto))]
    return min(posiciones) if posiciones else -1


def _ultima(texto: str, patrones: tuple[re.Pattern[str], ...]) -> int:
    """Dónde empieza el ÚLTIMO acierto de cualquiera de los patrones, o -1."""
    posiciones = [m.start() for p in patrones for m in p.finditer(texto)]
    return max(posiciones) if posiciones else -1


def pais_de(oferta: Oferta) -> str:
    """`"estados_unidos"`, `"canada"` o `""` si la ubicación no lo dice.

    Se resuelve en dos pasos porque los dos países comparten nombres de
    subdivisión y eso produce errores en las dos direcciones:

    1. **El nombre del país gana.** "Toronto, ON, Canada" es Canadá aunque ON
       aparezca antes.
    2. **Si no hay país, gana la subdivisión que se nombra ÚLTIMA.** Las
       direcciones van de lo chico a lo grande —ciudad, estado, país— así que
       la última es la más amplia. Sin esta regla, "Ontario, California" —una
       ciudad real de EE.UU.— se leía como Canadá.

    ⚠ Queda un caso que se decide a favor de EE.UU. y no se puede resolver con
    la ubicación sola: "Georgia" es un estado y también un país. Un puesto en
    Tbilisi va a contarse como estadounidense. Se acepta a ojos abiertos: el
    estado aparece en los feeds cientos de veces más que el país, y el error
    cuesta un puesto mal etiquetado, no uno perdido.
    """
    lugar = oferta.ubicacion.strip()
    if not lugar:
        return ""

    # El nombre del país gana, y entre dos nombres gana el que aparece primero:
    # "Remote — Canada / United States" es una oferta que sirve por los dos lados.
    pais_ca = _primera(lugar, _PAIS_CANADA)
    pais_us = _primera(lugar, _PAIS_EEUU)
    if pais_ca >= 0 or pais_us >= 0:
        if pais_ca < 0:
            return "estados_unidos"
        if pais_us < 0:
            return "canada"
        return "canada" if pais_ca < pais_us else "estados_unidos"

    # Sin país, gana la subdivisión nombrada última: la más amplia.
    donde_ca = _ultima(lugar, (_PROVINCIA, _PROVINCIA_CODIGO))
    donde_us = _ultima(lugar, (_ESTADO, _ESTADO_CODIGO))
    if donde_ca < 0 and donde_us < 0:
        return ""
    return "canada" if donde_ca > donde_us else "estados_unidos"


# --- De qué oficio es el puesto ---------------------------------------------
#
# El canal de LMIA sirve —es la vía por la que un empleador canadiense contrata
# a alguien de afuera— pero trae con él todo lo que se contrata por esa vía, y
# la gastronomía es lo que más volumen tiene. Medido el 21/09/2026 con la
# oferta real que llegó al buzón: "Line Cook (LMIA/PNP Available)" sacaba 40
# puntos contra un mínimo de 25 y entraba al aviso.
#
# La aritmética de por qué entraba importa, porque no es obvia: `patrocinio`
# suma 15, eso pone el total en positivo, y con el total en positivo se
# desbloquean los 25 de `hasta_24h`. O sea que la señal que hace útil a Job
# Bank es la misma que dejaba pasar al cocinero.
#
# No se arregla quitándole peso al patrocinio: las fichas de Job Bank no traen
# descripción, así que el patrocinio es lo único que tienen, y gatearlo dejaría
# esa fuente en cero. Se arregla mirando de qué OFICIO es el puesto, que es la
# pregunta que de verdad separa un cocinero de un ingeniero.

# Se mira el TÍTULO y nada más. En la descripción, "restaurant" es el cliente
# —"we build software for restaurants"— y "kitchen" puede ser el nombre de un
# producto. En el título es el trabajo.
#
# Y el oficio técnico gana siempre: "Software Engineer, Restaurant Platform" y
# "Kitchen Display Systems Developer" son puestos de programación en empresas
# de gastronomía, que es justo lo contrario de lo que se quiere frenar. Toast,
# Olo y Lightspeed contratan ingenieros todo el tiempo.
_OFICIO_TECNICO = re.compile(
    r"\b(engineer|engineering|developer|programmer|programador|software|devops"
    r"|data|scientist|analyst|architect|administrator|sysadmin|sre"
    r"|full[ -]?stack|back[ -]?end|front[ -]?end|mobile|web|cloud|platform"
    r"|machine learning|\bml\b|\bai\b|qa|tester|designer|ux|ui"
    r"|desarrollador|ingenier[oa]|arquitect[oa]|t[eé]cnico)\b",
    re.I,
)

# Gastronomía. La lista es corta y concreta a propósito: son los puestos que de
# verdad aparecen en las alertas de LMIA, no una enciclopedia de oficios.
#
# "server" y "host" quedan AFUERA aunque sean puestos de restaurante: los dos
# son palabras de informática —"SQL Server", "hosting"— y el riesgo de comerse
# una oferta buena es peor que el de dejar pasar un mesero, que igual no suma
# nada por ningún otro lado. "hostess" sí, que no es ambigua.
_OFICIO_GASTRONOMIA = re.compile(
    r"\b(cook|chef|sous[ -]chef|kitchen (helper|assistant|staff|porter)|dishwasher"
    r"|food (service|preparation|counter|prep)|fast food|restaurant (manager|supervisor)"
    r"|waiter|waitress|hostess|busser|barista|bartender|baker|butcher|meat cutter"
    r"|cociner[oa]|ayudante de cocina|mesero|meser[ao]|camarer[oa]|pastelero|carnicero)\b",
    re.I,
)


def fuera_de_oficio(oferta: Oferta) -> bool:
    """¿El título nombra un oficio que no es el tuyo?

    Devuelve `False` en cuanto el título nombra un puesto técnico, aunque
    también nombre gastronomía: la empresa puede ser de restaurantes y el
    puesto de programación.
    """
    titulo = oferta.titulo
    if _OFICIO_TECNICO.search(titulo):
        return False
    return bool(_OFICIO_GASTRONOMIA.search(titulo))


@dataclass(frozen=True, slots=True)
class Criterio:
    """Lo que dice `perfil/busqueda.toml`, ya validado."""

    stack: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pesos_stack: dict[str, int] = field(default_factory=dict)
    preferencias: dict[str, int] = field(default_factory=dict)
    penalizaciones: dict[str, int] = field(default_factory=dict)
    necesita_patrocinio: bool = False
    # Países donde un puesto presencial o híbrido SÍ sirve. Vacío = ninguno.
    presencial_aceptable_en: tuple[str, ...] = ()
    fuentes: dict[str, bool] = field(default_factory=dict)
    tope_por_aviso: int = 8
    puntaje_minimo: int = 25
    # Cuántas ofertas de la MISMA empresa entran en un aviso. Los marketplaces
    # de talento —Lemon.io, Toptal y parecidos— republican su catálogo entero
    # todo el tiempo: sin esto, una sola empresa se lleva cinco de los ocho
    # lugares y las ofertas frescas del resto no llegan al teléfono. Las que
    # sobran no se pierden, quedan en el digest.
    tope_por_empresa: int = 2
    # Días a partir de los cuales una oferta no entra al aviso, por buena que
    # sea. Restar puntos no alcanza: una oferta con el stack entero absorbe la
    # penalización de frescura y sigue arriba, y a las cuatro semanas ya
    # entrevistaron a alguien. Las viejas siguen en el digest. 0 lo apaga.
    descartar_despues_de_dias: int = 0
    # Cuántas horas de silencio se aguantan antes de mandar una línea de
    # "sigo vivo". Sin esto, "no había nada" y "el cron está muerto" se ven
    # exactamente igual desde el teléfono: los dos son no recibir nada. 0 lo
    # apaga y el silencio pasa a ser total.
    horas_sin_aviso: int = 24
    # Cuánto puede aportar el stack como máximo. Sin tope, una oferta que lista
    # treinta tecnologías en un párrafo de "nice to have" le gana a una que pide
    # exactamente lo que hacés.
    tope_stack: int = 60
    frescura: dict[str, int] = field(default_factory=dict)
    # `(nombre, ats, token)` por empresa, del `[[empresas]]` del TOML.
    empresas: tuple[tuple[str, str, str], ...] = ()


# Cuánto vale encontrar un término de cada grupo.
PESOS = {"fuerte": 12, "medio": 6, "leve": 2}

DEFECTOS_PREFERENCIAS = {
    # Dónde está el PUESTO. Distinto de `cliente_norteamerica`, que es de dónde
    # es quien publica en Upwork: una cosa es el trabajo y la otra el que paga.
    "estados_unidos": 15,
    "canada": 15,
    "cliente_norteamerica": 15,
    # Menos que `largo_plazo` (20) a propósito: son la misma idea medida dos
    # veces, y sumar los dos pesos completos le daría 40 a una oferta que sólo
    # dijo una cosa de dos maneras. Ver la nota de `duracion_larga` arriba.
    "duracion_larga": 12,
    # Que pidan experto no te hace ganar la oferta, pero dice que el trabajo no
    # es de los que se resuelven con el primero que conteste — que con Connects
    # escasos es donde tenés ventaja. Peso chico: es contexto, no decisión.
    "pide_experto": 8,
    "largo_plazo": 20,
    "latam": 30,
    "remoto_global": 20,
    "reubicacion": 20,
    "patrocinio": 15,
    "contractor": 12,
    "freelance": 5,
}
DEFECTOS_PENALIZACIONES = {
    # Un oficio que no es el tuyo. Pesa más que ninguna otra: un cocinero con
    # LMIA no es un puesto peor, es otro trabajo, y la aritmética que lo dejaba
    # entrar —patrocinio 15 más frescura 25— llega a 40. Con 60 queda en -20 y
    # deja de competir, sin dejar de aparecer en el digest.
    "fuera_de_oficio": 60,
    "junior": 40,
    "sin_patrocinio": 25,
    "solo_us": 0,
    "hibrido": 45,
    "presencial": 45,
}

# Cuánto vale llegar temprano. Los tramos salen de que el reclutador no lee las
# 300 postulaciones: lee las primeras 20 o 40 de la cola, y el ATS ordena esa
# cola en vez de rechazar por su cuenta. Llegar tarde no es que te filtren, es
# que te leen después de que ya entrevistaron a alguien.
#
# Las 96 horas son el umbral que aparece medido (TalentWorks, 1.600
# postulaciones); las magnitudes que publican las empresas del rubro —"8 veces
# más entrevistas"— son de blogs con interés en venderte urgencia, así que acá
# se usa la DIRECCIÓN del hallazgo, que es sólida, y no el número.
DEFECTOS_FRESCURA = {
    "hasta_24h": 25,
    "hasta_48h": 15,
    "hasta_96h": 8,
    "hasta_7d": 0,
    "mas_vieja": -10,
}

# Los tramos, en horas, de más nuevo a más viejo. El último atrapa todo lo demás.
TRAMOS_FRESCURA: tuple[tuple[str, float], ...] = (
    ("hasta_24h", 24),
    ("hasta_48h", 48),
    ("hasta_96h", 96),
    ("hasta_7d", 168),
    ("mas_vieja", float("inf")),
)


def _privado_del_entorno() -> dict:
    """El overlay privado servido como variable de entorno, en TOML.

    Un TOML mal escrito acá no puede tumbar la corrida: el cazador es un cron y
    el síntoma sería "hoy no llegó ningún aviso", que desde el teléfono se ve
    igual que "hoy no había nada". Se avisa al log y se sigue con el público.
    """
    crudo = os.environ.get("BYTE_PERFIL_PRIVADO", "").strip()
    if not crudo:
        return {}
    try:
        return tomllib.loads(crudo)
    except tomllib.TOMLDecodeError:
        logger.warning("perfil_privado_invalido")
        return {}


def cargar_criterio(ruta: Path) -> Criterio:
    """Lee el TOML. Si no está, devuelve el criterio por defecto.

    Que falte el archivo no puede ser un error: el cazador tiene que poder correr
    recién clonado el repo y que el usuario lo ajuste después de ver el primer
    aviso, no antes.
    """
    crudo = _leer_toml(ruta)
    # `privado.toml` se superpone al público y NO se versiona. Existe porque
    # este repo es público: algunas preferencias delatan cosas que no tienen por
    # qué leerse —dónde vas a estar viviendo, por ejemplo— y ponerlas en
    # `busqueda.toml` sería publicarlas. Las secciones se mezclan clave a clave,
    # así que el privado puede pisar un solo valor sin repetir el archivo.
    privado = _leer_toml(ruta.parent / "privado.toml")
    # Y el mismo overlay, pero desde el entorno. Hace falta para desplegar: si el
    # servicio se construye desde este repo, `privado.toml` no viaja —está en el
    # `.gitignore`, que es donde tiene que estar— y sin él lo presencial en el
    # país al que te mudás vuelve a restar 45 y se hunde, en silencio. Con esto
    # el dato viaja como variable del servicio y no como archivo publicado.
    privado = {**privado, **_privado_del_entorno()}
    for seccion, valores in privado.items():
        if isinstance(valores, dict) and isinstance(crudo.get(seccion), dict):
            crudo[seccion] = {**crudo[seccion], **valores}
        else:
            crudo[seccion] = valores

    situacion = crudo.get("situacion") or {}
    stack_crudo = crudo.get("stack") or {}
    stack = {
        grupo: tuple(str(t).lower() for t in (stack_crudo.get(grupo) or [])) for grupo in PESOS
    }
    preferencias = {**DEFECTOS_PREFERENCIAS, **_enteros(crudo.get("preferencias"))}
    penalizaciones = {**DEFECTOS_PENALIZACIONES, **_enteros(crudo.get("penalizaciones"))}
    aviso = crudo.get("aviso") or {}
    frescura = {**DEFECTOS_FRESCURA, **_enteros(crudo.get("frescura"))}
    empresas = _empresas(crudo.get("empresas"))
    return Criterio(
        stack=stack,
        pesos_stack=dict(PESOS),
        preferencias=preferencias,
        penalizaciones=penalizaciones,
        necesita_patrocinio=bool(situacion.get("necesita_patrocinio", False)),
        presencial_aceptable_en=tuple(
            str(x).strip().lower()
            for x in (situacion.get("presencial_aceptable_en") or [])
            if str(x).strip()
        ),
        fuentes={k: bool(v) for k, v in (crudo.get("fuentes") or {}).items()},
        frescura=frescura,
        empresas=empresas,
        tope_por_aviso=int(aviso.get("tope_por_aviso", 8)),
        puntaje_minimo=int(aviso.get("puntaje_minimo", 25)),
        tope_por_empresa=int(aviso.get("tope_por_empresa", 2)),
        descartar_despues_de_dias=int(aviso.get("descartar_despues_de_dias", 0)),
        horas_sin_aviso=int(aviso.get("horas_sin_aviso", 24)),
    )


def _leer_toml(ruta: Path) -> dict:
    """El TOML, o vacío. Que falte o esté roto no puede frenar la búsqueda."""
    if not ruta.is_file():
        return {}
    try:
        return tomllib.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _empresas(seccion: object) -> tuple[tuple[str, str, str], ...]:
    """El `[[empresas]]` del TOML, saltando las entradas incompletas.

    Una fila a la que le falta el token es una empresa que no se va a poder
    consultar; dejarla entrar sólo produce un error por corrida que no dice
    nada nuevo.
    """
    if not isinstance(seccion, list):
        return ()
    salida = []
    for fila in seccion:
        if not isinstance(fila, dict):
            continue
        nombre = str(fila.get("nombre", "")).strip()
        ats = str(fila.get("ats", "")).strip().lower()
        token = str(fila.get("token", "")).strip()
        if nombre and ats and token:
            salida.append((nombre, ats, token))
    return tuple(salida)


def _enteros(seccion: object) -> dict[str, int]:
    if not isinstance(seccion, dict):
        return {}
    salida: dict[str, int] = {}
    for clave, valor in seccion.items():
        try:
            salida[str(clave)] = int(valor)
        except (TypeError, ValueError):
            continue
    return salida


def _patron(termino: str) -> re.Pattern[str]:
    """Un término del stack, como expresión que no pesque pedazos de palabra.

    Los términos con puntuación —`.net`, `c#`, `next.js`— no llevan borde por la
    izquierda a propósito: con él, `.net` no encontraría "ASP.NET", que es donde
    aparece la mitad de las veces.
    """
    escapado = re.escape(termino)
    izquierda = r"(?<![a-z0-9])" if termino[0].isalnum() else ""
    return re.compile(rf"{izquierda}{escapado}(?![a-z0-9])")


_CACHE_PATRONES: dict[str, re.Pattern[str]] = {}


def patron_de(termino: str) -> re.Pattern[str]:
    if termino not in _CACHE_PATRONES:
        _CACHE_PATRONES[termino] = _patron(termino)
    return _CACHE_PATRONES[termino]


@dataclass(frozen=True, slots=True)
class Puntaje:
    """El número y las líneas que lo explican."""

    total: int
    motivos: tuple[str, ...]
    senales: tuple[str, ...]
    terminos: tuple[str, ...]
    antiguedad_horas: float | None = None


def detectar_senales(oferta: Oferta, aceptable_en: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Qué señales trae el texto. `sin_patrocinio` anula a `patrocinio`."""
    texto = oferta.buscable()
    encontradas = [nombre for nombre, patron in SENALES.items() if patron.search(texto)]
    if "sin_patrocinio" in encontradas and "patrocinio" in encontradas:
        encontradas.remove("patrocinio")
    # Misma regla para el largo plazo: "this is not a long-term role" contiene
    # la frase positiva adentro, así que la negación manda.
    if "sin_largo_plazo" in encontradas and "largo_plazo" in encontradas:
        encontradas.remove("largo_plazo")
    # Una oferta que declara que no hay oficina no es híbrida ni presencial por
    # nombrar esas palabras para negarlas.
    # Lo mismo con la reubicación: nombrarla para negarla no es ofrecerla.
    if "reubicacion" in encontradas and NIEGA_REUBICACION.search(texto):
        encontradas.remove("reubicacion")
    if NIEGA_OFICINA.search(texto):
        encontradas = [s for s in encontradas if s not in ("hibrido", "presencial")]
    # Ir a una oficina sólo es un problema si la oficina está donde no vas a
    # estar. Un presencial en Caracas no es el mismo puesto que uno en Santiago
    # para alguien que se muda a Venezuela: el primero es aplicable y el segundo
    # no, y sin esto los dos se hundían igual.
    elif aceptable_en and any(pais in texto for pais in aceptable_en):
        encontradas = [s for s in encontradas if s not in ("hibrido", "presencial")]
    # El país del puesto entra como una señal más, con el nombre del país y no
    # con un "norteamerica" genérico: en el aviso se lee "[canada]", que dice
    # algo, y cada uno tiene su peso en el TOML por si alguna vez dejan de valer
    # lo mismo.
    pais = pais_de(oferta)
    if pais:
        encontradas.append(pais)
    # De qué oficio es el puesto. Se mira el título y va como señal para que se
    # vea en el aviso —"[fuera_de_oficio]"— en vez de que la oferta desaparezca
    # sin explicación.
    if fuera_de_oficio(oferta):
        encontradas.append("fuera_de_oficio")
    # Y si te reubican, la oficina deja de ser el problema. Un presencial en
    # Toronto que paga la mudanza es aplicable; hasta ahora se hundía los mismos
    # -45 que uno en Santiago, que no lo es.
    #
    # Sólo vale en los países que buscás, y eso es deliberado: "relocation
    # assistance available" en un híbrido de Bangalore no lo vuelve tomable, y
    # sin esta condición el perdón se lo llevaban todos.
    if pais in NORTEAMERICA and "reubicacion" in encontradas:
        encontradas = [s for s in encontradas if s not in ("hibrido", "presencial")]
    # Upwork es freelance por definición: el texto de la oferta no tiene por qué
    # decirlo y perderíamos la señal.
    if oferta.fuente == "upwork" and "freelance" not in encontradas:
        encontradas.append("freelance")
    return tuple(encontradas)


def tramo_de_frescura(horas: float | None) -> str | None:
    """En qué tramo cae una oferta. `None` si el feed no mandó fecha."""
    if horas is None:
        return None
    return next(nombre for nombre, tope in TRAMOS_FRESCURA if horas <= tope)


def puntuar(oferta: Oferta, criterio: Criterio, ahora: datetime | None = None) -> Puntaje:
    """El puntaje de una oferta, con el detalle de de dónde salió cada punto."""
    texto = oferta.buscable()
    motivos: list[str] = []

    del_stack = 0
    encontrados: list[str] = []
    for grupo, peso in criterio.pesos_stack.items():
        for termino in criterio.stack.get(grupo, ()):
            if patron_de(termino).search(texto):
                del_stack += peso
                encontrados.append(termino)
    if del_stack > criterio.tope_stack:
        motivos.append(f"+{criterio.tope_stack} stack (tope; sumaba {del_stack})")
        del_stack = criterio.tope_stack
    elif del_stack:
        motivos.append(f"+{del_stack} stack: {', '.join(encontrados[:8])}")

    total = del_stack
    senales = detectar_senales(oferta, criterio.presencial_aceptable_en)

    for senal in senales:
        puntos = criterio.preferencias.get(senal, 0)
        # El país ORDENA, no ADMITE: sólo cuenta si la oferta ya sumó algo por
        # el stack. Sin esta condición, "canada" (15) más "hasta_48h" (15)
        # llegaban a los 25 del mínimo con CERO coincidencias de stack, y las
        # fichas de Job Bank —que no traen descripción, son cuatro renglones—
        # aterrizaban en el teléfono por estar en Canadá y ser de ayer. Medido
        # con la oferta real de Omnissa: pasaba de 15 puntos a 30.
        #
        # Es el reverso de la regla que gobierna las penalizaciones. Ninguna
        # señal descarta sola; ninguna señal admite sola tampoco.
        if senal in NORTEAMERICA and not del_stack:
            motivos.append(f"+0 {senal} (no coincide nada del stack)")
            continue
        if puntos:
            total += puntos
            motivos.append(f"+{puntos} {senal}")

    for senal in senales:
        castigo = criterio.penalizaciones.get(senal, 0)
        # "no patrocina" solo importa si hoy necesitás que alguien patrocine.
        # Para alguien que ya puede trabajar donde está, es una línea de más en
        # el aviso, no un problema.
        if senal == "sin_patrocinio" and not criterio.necesita_patrocinio:
            continue
        if castigo:
            total -= castigo
            motivos.append(f"-{castigo} {senal}")

    # La frescura va al final para que quede última en los motivos: es la que
    # se mira primero cuando una oferta buena aparece baja en la lista.
    horas = oferta.antiguedad_horas(ahora)
    tramo = tramo_de_frescura(horas)
    if tramo is not None:
        puntos = criterio.frescura.get(tramo, 0)
        # La frescura también ORDENA y no ADMITE, por la misma razón que el país
        # y con un agujero bastante más grande: `hasta_24h` vale 25 y el mínimo
        # del aviso es 25, así que CUALQUIER oferta publicada hoy pasaba el
        # corte con cero de todo lo demás. Medido: "Cocinero de línea —
        # Parrilla, Madrid", publicada hace una hora, juntaba los 25 justos y
        # entraba al teléfono.
        #
        # Llegar temprano vale sobre una oferta que ya vale algo. Sobre una que
        # no vale nada, no vale nada. La penalización de `mas_vieja` sí se
        # aplica siempre: eso no admite a nadie, sólo hunde.
        if puntos > 0 and total <= 0:
            motivos.append(f"+0 {tramo} ({horas:.0f} h; no suma nada más)")
        elif puntos:
            total += puntos
            motivos.append(f"{puntos:+d} {tramo} ({horas:.0f} h)")
    else:
        # Sin fecha no se premia ni se castiga. Castigar convertiría "este feed
        # no manda la fecha" en "esta oferta es vieja", que son cosas distintas.
        motivos.append("sin fecha de publicación")

    return Puntaje(
        total=total,
        motivos=tuple(motivos),
        senales=senales,
        terminos=tuple(encontrados),
        antiguedad_horas=horas,
    )


@dataclass(frozen=True, slots=True)
class Brecha:
    """Qué pide la oferta, qué de eso está en tu CV y qué no.

    Es una diferencia de conjuntos, no una opinión del modelo. Sirve para lo
    único que se decide al postular: qué va en las dos primeras líneas —lo que
    piden y tenés— y qué conviene nombrar aunque sea de refilón, porque lo piden
    y tu CV hoy no lo dice.

    No mide si sabés algo: mide si tu CV lo **dice**. Un reclutador busca
    términos en un buscador, y lo que no está escrito no aparece.
    """

    pide: tuple[str, ...]
    tenes: tuple[str, ...]
    faltan: tuple[str, ...]

    @property
    def cobertura(self) -> float:
        """Qué proporción de lo que pide la oferta figura en tu CV."""
        return len(self.tenes) / len(self.pide) if self.pide else 0.0


def comparar_con_cv(oferta: Oferta, cv: str, vocabulario: tuple[str, ...]) -> Brecha:
    """Cruza los términos de la oferta contra el texto del CV."""
    texto_oferta = oferta.buscable()
    texto_cv = cv.lower()
    pide = [t for t in vocabulario if patron_de(t).search(texto_oferta)]
    tenes = [t for t in pide if patron_de(t).search(texto_cv)]
    faltan = [t for t in pide if t not in tenes]
    return Brecha(pide=tuple(pide), tenes=tuple(tenes), faltan=tuple(faltan))
