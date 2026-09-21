"""Pegás una página entera y esto saca las ofertas de adentro.

La diferencia con `empleo/upwork.py` es el ruido. Ahí se asumía que pegabas las
tarjetas; acá se asume que pegás **todo**: el menú, los filtros, el pie, los
"Saltar al contenido principal" y los banners de cookies. Es lo que sale de
apretar Ctrl-A en la página de resultados, que es lo único que nadie se
equivoca en hacer.

Tres problemas distintos, tres soluciones:

1. **De qué sitio es.** Upwork y LinkedIn muestran cosas que el otro no tiene
   —propuestas contra postulantes, pago verificado contra Easy Apply—, y esas
   son justo las que deciden si vale la pena. Se detecta por marcas del texto.
2. **Dónde empieza y termina cada oferta.** Copiar y pegar casi siempre deja un
   renglón en blanco entre tarjetas, pero no siempre: LinkedIn a veces sale
   corrido. Cuando el corte por renglón en blanco no da, se corta por el final
   de cada tarjeta, que sí es reconocible ("hace 2 días", "40 postulantes").
3. **Qué de lo pegado no es una oferta.** Un bloque que no tiene ni plata, ni
   tiempo, ni postulantes, ni una sola tecnología conocida, no es una oferta:
   es el menú. Se descarta, y se dice cuántos se descartaron — si ese número es
   enorme, el parseo salió mal y hay que verlo, no confiar en el resultado.

**Nada de esto raspa nada.** Copiar lo que estás mirando y pegarlo lo hace una
persona, que es exactamente lo que permiten los sitios que prohíben el raspado.
"""

import re
from dataclasses import dataclass

from empleo.criterio import Criterio, patron_de
from empleo.oferta import Oferta
from empleo.upwork import (
    CONNECTS_POR_DEFECTO,
    OfertaUpwork,
    Veredicto,
    _gastado,
    _propuestas,
    evaluar,
)
from empleo.upwork import (
    _a_horas as _antiguedad,
)
from empleo.vocabulario import TERMINOS

# --- De qué sitio viene ---

_MARCAS = {
    "upwork": re.compile(r"\bproposals?\s*:|payment (un)?verified|\bconnects?\b|upwork\.com", re.I),
    "linkedin": re.compile(
        r"easy apply|solicitud sencilla|\bapplicants?\b|postulantes|linkedin\.com|promoted",
        re.I,
    ),
}


def detectar(texto: str) -> str:
    """De qué sitio es lo pegado. `generico` si no se reconoce.

    Se cuenta cuántas marcas tiene cada uno en vez de quedarse con la primera:
    una oferta de Upwork puede nombrar LinkedIn al pasar, y al revés.
    """
    conteo = {sitio: len(patron.findall(texto)) for sitio, patron in _MARCAS.items()}
    sitio = max(conteo, key=lambda s: conteo[s])
    return sitio if conteo[sitio] else "generico"


# --- Dónde termina una oferta ---

# El final de una tarjeta: casi siempre lo último es cuándo se publicó o cuánta
# gente aplicó. Sirve para cortar cuando no hay renglones en blanco.
_FIN_DE_TARJETA = re.compile(
    r"(hace\s+\d+|\d+\s*(minutes?|hours?|days?|weeks?|minutos?|horas?|d[ií]as?|semanas?)\s+ago"
    r"|\bago\b|applicants?|postulantes|easy apply|solicitud sencilla|proposals?\s*:)",
    re.I,
)

# Lo que aparece en cualquier página y nunca es una oferta.
_CHROME = re.compile(
    r"^(skip to|saltar al|sign in|iniciar sesi[oó]n|log in|home|inicio|jobs?|empleos?|"
    r"search|buscar|filters?|filtros?|sort by|ordenar|my network|mensajes|messages|"
    r"notifications|notificaciones|cookies?|privacy|privacidad|©|\d+$"
    r"|linkedin\s*$|upwork\s*$|about\s*$|accessibility\s*$)",
    re.I,
)

# Cuántos caracteres de PROSA tiene que traer una tarjeta, en promedio, para que
# se pueda puntuar su contenido y no sólo su título.
#
# ⚠ Se mide la descripción, no la tarjeta entera. Medir el bloque completo
# contaba los metadatos —"Posted 6 days ago", "Proposals: 50+", "Payment
# unverified", "$0 spent"— como si fueran texto del puesto, y en Upwork esos
# renglones son la mitad de la tarjeta. Dos ofertas CON descripción daban 213 de
# promedio y caían bajo el umbral, así que la página decía "estas tarjetas no
# traen la descripción" con la descripción delante, y escondía el mensaje que
# correspondía: que ninguna llegaba al mínimo.
# 60 = una sola oración de descripción. Es el piso deliberadamente bajo: lo que
# se quiere distinguir no es "poca descripción" de "mucha", sino que HAYA
# descripción. Una tarjeta de LinkedIn sin cuerpo mide 0 y una oferta con un
# párrafo mide varios cientos; entre esas dos no hay casos borde que valga la
# pena afinar, y subirlo sólo agrega falsos "no traen descripción".
MINIMO_PARA_DECIDIR = 60

# Cuántas se muestran como máximo, ya ordenadas de mejor a peor.
#
# Cinco porque es lo que se lee de una pasada y lo que se puede convertir en
# propuestas escritas a mano en un día. Subirlo devuelve el problema que el tope
# resuelve: con una pantalla de ofertas buenas, "las que pasan el mínimo" fueron
# 6 de 6 y la lista dejó de decidir nada.
TOPE = 5


def _prosa(bloque: str) -> int:
    """Cuánto texto del PUESTO trae el bloque, sin contar los metadatos.

    Una línea de prosa es la que no reconocemos como dato de la tarjeta y tiene
    largo de oración. No hace falta que sea exacto: se usa para distinguir "hay
    descripción" de "esto es un encabezado", y esas dos cosas no se parecen.
    """
    total = 0
    for bruto in bloque.splitlines():
        linea = bruto.strip()
        if len(linea) < 40:
            continue
        if _METADATO.match(linea) or _POSTED.match(linea) or _PIE_DEL_CLIENTE.match(linea):
            continue
        if _ENCABEZADO_DE_LISTA.match(linea) or _FIN_DE_TARJETA.match(linea):
            continue
        total += len(linea)
    return total


# Señales de que un bloque sí es una oferta.
_PLATA = re.compile(r"\$\s?[\d.,]+|\bUSD\b|/\s?(yr|hr|año|hora)", re.I)

# Líneas que son la COLA de una tarjeta, no el principio de la siguiente. En
# LinkedIn "Easy Apply" viene DESPUÉS de los postulantes, así que cortar por
# postulantes dejaba esa línea encabezando la oferta siguiente — y como tiene
# más de ocho caracteres, se elegía a sí misma como título. Pasó de verdad.
#
# Tiene que ser la línea ENTERA. "Promoted" estuvo acá un rato y rompía todo: en
# LinkedIn no va solo, encabeza la línea de metadatos —"Promoted · 2 days ago ·
# 12 applicants"—, así que suprimía el corte de esa tarjeta y dos ofertas
# quedaban pegadas en una, con el puntaje de un Frankenstein.
_COLA = re.compile(r"^(easy apply|solicitud sencilla)\s*$", re.I)


def _es_oferta(bloque: str) -> bool:
    """Si el bloque parece una oferta y no parte del sitio.

    Alcanza con UNA señal. Pedir dos descartaba ofertas reales que sólo traen
    el título y la empresa, que en LinkedIn son muchas.
    """
    lineas = [x.strip() for x in bloque.splitlines() if x.strip()]
    if len(lineas) < 2 or _CHROME.match(lineas[0]):
        return False
    if _PLATA.search(bloque) or _FIN_DE_TARJETA.search(bloque):
        return True
    bajo = bloque.lower()
    return any(patron_de(t).search(bajo) for t in TERMINOS)


def _sin_ruido(texto: str) -> str:
    """Saca las líneas que son del sitio y no de ninguna oferta.

    Va **antes** de cortar y no después: el menú viene pegado arriba de la
    primera tarjeta, sin renglón en blanco en el medio. Filtrando después, ese
    bloque empieza con "Saltar al contenido principal", se lo toma por menú y se
    pierde la primera oferta entera — que suele ser la más nueva.
    """
    return "\n".join(x for x in texto.splitlines() if not _CHROME.match(x.strip()))


def _por_renglon_en_blanco(texto: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", texto.strip()) if b.strip()]


# Las líneas de datos de una tarjeta que no son ni plata ni tiempo ni
# competencia, y que sin esto se confundían con títulos porque son largas.
# "Payment verified" tiene diecisiete caracteres y empezaba una oferta nueva
# llamada así, partiendo en dos la que venía arriba.
#
# Esto es conocimiento de cómo se ven las tarjetas de estos dos sitios, no una
# heurística general: si un sitio nuevo trae otras, se agregan acá.
_METADATO = re.compile(
    r"^(payment\s+(un)?verified|pago\s+(no\s+)?verificado"
    r"|\$[\d.,]+\s*[km]?\+?\s*spent|hourly|fixed[- ]price|est\.?\s*budget"
    r"|\d+\s*connects?"
    r"|remote|remoto|h[ií]brido|hybrid|on[- ]?site|presencial"
    r"|full[- ]time|part[- ]time|contract|internship|tiempo completo)\b",
    re.I,
)


# Encabezados de las listas que Upwork pone debajo de la oferta. No son
# metadatos con formato reconocible —son la palabra sola— pero tampoco son
# títulos, y sin esto "Skills" abría una oferta nueva.
_ENCABEZADO_DE_LISTA = re.compile(r"^(skills?|skip skills|more\s?about)\b", re.I)

# El pie del cliente: reputación y país. "Rating is 5.0 out of 5." termina en
# punto y ya caía, pero "Payment verified" y el país no, y se llevaban el
# título. Se listan porque son de Upwork, no porque sea una regla general.
_PIE_DEL_CLIENTE = re.compile(r"^(verified|unverified|rating is)\b", re.I)


def _parece_titulo(linea: str) -> bool:
    """Si la línea puede ser el título de una oferta y no un dato de la tarjeta."""
    # El tope de largo y el punto final son lo que separa un título de la
    # DESCRIPCIÓN. En Upwork el resumen del puesto viene después de los datos,
    # así que sin esto arrancaba una oferta nueva titulada con el primer párrafo
    # de la anterior. Un título es corto y no es una oración.
    return (
        12 < len(linea) <= 120
        and not linea.endswith((".", "!", "?"))
        and not _COLA.match(linea)
        and not _METADATO.match(linea)
        and not _POSTED.match(linea)
        and not _ENCABEZADO_DE_LISTA.match(linea)
        and not _PIE_DEL_CLIENTE.match(linea)
        and not _FIN_DE_TARJETA.search(linea)
        # ⚠ Que la línea NOMBRE plata no la descalifica; que EMPIECE por plata,
        # sí. Los títulos de Upwork llevan el presupuesto adentro más seguido de
        # lo que parece —"Paid $500–$750 Trial Milestone With Long-Term
        # Opportunity"— y descartarlos dejaba a la oferta titulada con el
        # renglón siguiente, que era "Full-Stack Development" de la lista de
        # skills. Una línea de tarifa, en cambio, abre con el número o con
        # "Hourly"/"Fixed-price", y eso ya lo ataja `_METADATO`.
        and not _PLATA.match(linea)
    )


def _por_inicio_de_tarjeta(texto: str) -> list[str]:
    """Corta cuando EMPIEZA la tarjeta siguiente, no cuando termina la anterior.

    La primera versión cortaba después de cualquier marca —"hace 2 días",
    "Proposals:"— y eso está mal de raíz, porque cada sitio las pone en otro
    orden. En LinkedIn la última línea es el tiempo y los postulantes; en Upwork
    el tiempo va en el medio y después vienen las propuestas, el pago y las
    tecnologías. Cortando por marca, una oferta de Upwork se partía en dos y la
    segunda mitad se llamaba "Payment verified".

    Lo que sí es igual en los dos: una tarjeta nueva empieza con algo que parece
    un título, y sólo después de que la anterior ya mostró algún dato. Esa es la
    regla, y no depende del orden de nadie.
    """
    bloques: list[str] = []
    actual: list[str] = []
    tiene_datos = False

    def cerrar() -> None:
        nonlocal actual, tiene_datos
        if len(actual) >= 2:
            bloques.append("\n".join(actual))
        actual, tiene_datos = [], False

    for bruto in texto.splitlines():
        linea = bruto.strip()
        if not linea:
            continue
        # Una cola sin tarjeta abierta pertenece a la anterior, no a la próxima.
        if _COLA.match(linea) and not actual and bloques:
            bloques[-1] += "\n" + linea
            continue
        if tiene_datos and _parece_titulo(linea):
            cerrar()
        actual.append(linea)
        if _FIN_DE_TARJETA.search(linea) or _PLATA.search(linea):
            tiene_datos = True
    cerrar()
    return bloques


# El encabezado con el que Upwork abre CADA tarjeta de la búsqueda: "Posted 50
# minutes ago", "Posted yesterday". Aparece una vez por oferta y siempre
# primero, lo que lo vuelve el único corte que no depende del maquetado.
_POSTED = re.compile(r"^\s*(?:posted|publicado)\b.*$", re.I | re.M)


def _por_posted(texto: str) -> list[str]:
    """Corta el pegado de Upwork usando el "Posted …" como ancla de cada tarjeta.

    ⚠ Las otras dos estrategias fallan con la pantalla real de Upwork, y hay que
    saber por qué antes de tocar esto:

    - **Por renglón en blanco**: Upwork deja renglones vacíos DENTRO de la
      tarjeta —antes de "Skills", antes del pie del cliente—, así que parte cada
      oferta en tres. El pedazo con "$300K+ spent" queda sin el título.
    - **Por inicio de tarjeta**: busca algo que parezca título, y las listas de
      skills de Upwork son justamente renglones cortos sin punto final. Abre una
      oferta nueva llamada "API Development" o "Adobe Illustrator".

    Medido contra un pegado real de 6 ofertas: la primera devolvía 12 bloques y
    la segunda 15. Esta devuelve 6.

    ⚠⚠ El "Posted …" NO siempre abre la tarjeta. Hay dos maquetados y el corte
    tiene que sobrevivir a los dos:

        Posted 50 minutes ago          Senior AI Engineer for RAG
        Proposals: 20 to 50            Hourly: $60-$90
        WhatsApp API Consultant        Posted 25 minutes ago

    A la izquierda el título va después del ancla; a la derecha, antes. Cortando
    en el "Posted" a secas, el segundo caso le arranca el título a cada oferta y
    se lo pega a la anterior. Por eso el corte no se hace en el ancla sino en el
    primer renglón de su tarjeta: desde el ancla se sube mientras las líneas
    sigan siendo parte de la misma oferta.
    """
    lineas = texto.splitlines()
    anclas = [i for i, x in enumerate(lineas) if _POSTED.match(x.strip())]
    if not anclas:
        return []

    def principio(ancla: int, piso: int) -> int:
        """Desde dónde empieza de verdad la tarjeta de este "Posted".

        Se sube mientras haya título o datos —tarifa, propuestas— y se frena en
        el renglón vacío o en la tarjeta anterior. `piso` es dónde terminó la
        oferta de arriba: sin él, subir se comería la tarjeta previa entera.
        """
        i = ancla
        while i > piso:
            previa = lineas[i - 1].strip()
            if not previa or not (_parece_titulo(previa) or _METADATO.match(previa)):
                break
            i -= 1
        return i

    cortes: list[int] = []
    piso = 0
    for ancla in anclas:
        inicio = principio(ancla, piso)
        cortes.append(inicio)
        piso = ancla + 1
    # Lo de antes del primer corte es el menú de la página, no una oferta.
    limites = cortes + [len(lineas)]
    bloques = ["\n".join(lineas[i:j]).strip() for i, j in zip(limites, limites[1:], strict=False)]
    return [b for b in bloques if b]


def separar(texto: str) -> tuple[list[str], int]:
    """Los bloques que son ofertas, y cuántos se descartaron por no serlo."""
    limpio = _sin_ruido(texto)
    por_blanco = [b for b in _por_renglon_en_blanco(limpio) if _es_oferta(b)]
    por_tarjeta = [b for b in _por_inicio_de_tarjeta(limpio) if _es_oferta(b)]
    por_posted = [b for b in _por_posted(limpio) if _es_oferta(b)]

    # ⚠ El "Posted …" GANA cuando está, aunque encuentre MENOS bloques.
    #
    # Las otras dos se comparan entre sí por cantidad, y para ellas está bien:
    # ahí más bloques significa "separó de verdad lo que vino corrido". Pero
    # cuando el problema es el contrario —una oferta partida en tres— la
    # cantidad premia exactamente al que está fallando. Con el pegado real,
    # elegir por cantidad se quedaba con 15 fragmentos en vez de 6 ofertas.
    #
    # El encabezado no es una heurística: es el único renglón que Upwork pone
    # una vez por tarjeta. Si está, sabe más que cualquier conteo.
    if por_posted:
        elegidos = por_posted
    else:
        elegidos = por_tarjeta if len(por_tarjeta) > len(por_blanco) else por_blanco
    descartados = len(_por_renglon_en_blanco(texto)) - len(elegidos)
    return elegidos, max(0, descartados)


# --- Lo propio de LinkedIn ---

# "40 applicants", "Over 100 applicants", "Sé de los primeros 25 solicitantes".
_POSTULANTES = re.compile(
    r"(?:over|m[aá]s de|primeros?)?\s*(\d+)\s*\+?\s*(?:applicants?|postulantes|solicitantes)", re.I
)
_PROMOVIDO = re.compile(r"\bpromoted\b|\bpromocionado\b", re.I)


def _postulantes(bloque: str) -> int | None:
    encontrado = _POSTULANTES.search(bloque)
    return int(encontrado.group(1)) if encontrado else None


# --- Armar las ofertas ---


@dataclass(frozen=True, slots=True)
class Analisis:
    """Lo que se le devuelve a la página."""

    sitio: str
    veredictos: list[Veredicto]
    elegidas: list[Veredicto]
    # Cuántos bloques se descartaron por no parecer ofertas. NO viaja al
    # navegador: cuenta renglones en blanco del pegado, no tarjetas, así que con
    # la pantalla de Upwork —que trae varios por tarjeta— daba números que se
    # leían como ofertas perdidas sin serlo. Queda para depurar el corte.
    descartados: int
    # True cuando las tarjetas vienen sin descripción: se pegó la lista pero sin
    # el cuerpo de cada oferta. Con eso el puntaje de stack se calcula sobre un
    # título de seis palabras, así que no se elige ninguna — decir "aplicá a
    # esta" con esa base sería inventar una certeza.
    poca_informacion: bool = False


def _a_oferta(bloque: str, sitio: str) -> OfertaUpwork:
    lineas = [x.strip() for x in bloque.splitlines() if x.strip()]
    # El título es la primera línea que no sea plata, tiempo, postulantes ni
    # cola: todas esas superan los ocho caracteres y se elegían a sí mismas.
    titulo = next((x for x in lineas if _parece_titulo(x)), lineas[0])
    # La empresa es la línea SIGUIENTE al título, no la segunda del bloque: el
    # bloque puede arrancar con una línea que no era el título, y ahí la empresa
    # salía siendo el título mismo. En Upwork la pantalla de búsqueda no muestra
    # empresa, así que queda vacía.
    siguiente = lineas.index(titulo) + 1
    empresa = lineas[siguiente][:120] if sitio == "linkedin" and siguiente < len(lineas) else ""
    horas = _antiguedad(bloque)

    from datetime import UTC, datetime, timedelta

    return OfertaUpwork(
        oferta=Oferta(
            fuente=sitio,
            id_externo="",
            titulo=titulo[:300],
            empresa=empresa,
            url="",
            descripcion=bloque,
            publicada=(
                (datetime.now(tz=UTC) - timedelta(hours=horas)).isoformat()
                if horas is not None
                else ""
            ),
        ),
        # En LinkedIn los postulantes cumplen el papel de las propuestas: las
        # primeras se leen y las demás se hojean. Se guardan en el mismo campo
        # para que el puntaje no tenga que saber de qué sitio vino.
        propuestas=_propuestas(bloque) if sitio == "upwork" else _postulantes(bloque),
        verificado=(
            True
            if re.search(r"payment\s+verified", bloque, re.I)
            else (False if re.search(r"payment\s+unverified", bloque, re.I) else None)
        ),
        gastado_usd=_gastado(bloque),
        connects=CONNECTS_POR_DEFECTO,
        horas=horas,
    )


def analizar(texto: str, criterio: Criterio) -> Analisis:
    """Lo pegado, convertido en la lista de a cuáles aplicar.

    **No reparte presupuesto.** Entran las que pasan `puntaje_minimo` del TOML y
    nada más. El reparto por Connects existió acá y se sacó: obligaba a declarar
    cuántos te quedan antes de ver nada, y con el criterio afinado el corte por
    puntaje ya deja pocas. Si hace falta volver, está en `upwork.repartir()`,
    que sigue siendo lo que usa el CLI.
    """
    bloques, descartados = separar(texto)
    sitio = detectar(texto)
    entradas = [_a_oferta(b, sitio) for b in bloques]
    veredictos = evaluar(entradas, criterio)
    promedio = sum(_prosa(b) for b in bloques) / len(bloques) if bloques else 0
    poca_informacion = bool(bloques) and promedio < MINIMO_PARA_DECIDIR

    # Sin descripción no se elige ninguna: el puntaje saldría de un título de
    # seis palabras. Es el único caso en que la lista sale vacía teniendo
    # ofertas buenas, y la página lo dice con todas las letras.
    pasan = (
        [] if poca_informacion else [v for v in veredictos if v.total >= criterio.puntaje_minimo]
    )

    # ⚠ El mínimo solo NO alcanza, y se vio con un pegado real: una pantalla de
    # ofertas buenas dio 6 de 6 elegidas. Una lista que no descarta nada no
    # responde "¿a cuáles aplico?", que es la única pregunta de esta página.
    #
    # El tope es lo que la vuelve una respuesta. Las que quedan afuera no es que
    # sean malas —pasaron el mínimo— es que hay cinco mejores, y con el tiempo
    # de escribir propuestas que hay en un día, esa distinción es la que importa.
    elegidas = pasan[:TOPE]
    return Analisis(
        sitio=sitio,
        veredictos=veredictos,
        elegidas=elegidas,
        descartados=descartados,
        poca_informacion=poca_informacion,
    )


def a_json(analisis: Analisis) -> dict:
    """La forma que consume la página: **sólo las elegidas**.

    Las descartadas no viajan. Mandarlas para que la página las esconda sería
    poner en el navegador una lista que nadie va a mirar, y la razón de que no
    se muestren es justamente que no aportan a la decisión.

    Sí viaja `analizadas`: cuántas se leyeron en total. Es lo que convierte un
    "0 elegidas" en información —"miré 18 y ninguna vale"— en vez de dejarlo
    indistinguible de "no entendí lo que pegaste".
    """

    def fila(veredicto: Veredicto) -> dict:
        entrada = veredicto.entrada
        return {
            "titulo": entrada.oferta.titulo,
            "empresa": entrada.oferta.empresa,
            "puntaje": veredicto.total,
            "competencia": entrada.propuestas,
            "horas": entrada.horas,
            "verificado": entrada.verificado,
            "gastado": entrada.gastado_usd,
            "senales": list(veredicto.puntaje.senales),
            "motivos": list(veredicto.puntaje.motivos) + list(veredicto.motivos),
        }

    return {
        "sitio": analisis.sitio,
        "analizadas": len(analisis.veredictos),
        "poca_informacion": analisis.poca_informacion,
        "ofertas": [fila(v) for v in analisis.elegidas],
    }
