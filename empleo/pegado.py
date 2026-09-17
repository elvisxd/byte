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
    repartir,
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

# Cuántos caracteres tiene que tener una tarjeta, en promedio, para que se
# pueda puntuar su contenido y no sólo su título. Por debajo, lo que hay es un
# encabezado: alcanza para ordenar, no para decidir.
MINIMO_PARA_DECIDIR = 220

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
        and not _FIN_DE_TARJETA.search(linea)
        and not _PLATA.search(linea)
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


def separar(texto: str) -> tuple[list[str], int]:
    """Los bloques que son ofertas, y cuántos se descartaron por no serlo."""
    limpio = _sin_ruido(texto)
    por_blanco = [b for b in _por_renglon_en_blanco(limpio) if _es_oferta(b)]
    por_tarjeta = [b for b in _por_inicio_de_tarjeta(limpio) if _es_oferta(b)]
    # Se elige el que más ofertas encuentra. Si la página vino con renglones en
    # blanco, el primero gana solo; si vino corrida, el primero devuelve uno o
    # dos bloques enormes y el segundo los separa de verdad.
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
    descartados: int
    connects_gastados: int = 0
    connects_disponibles: int = 0
    # True cuando las tarjetas vienen sin descripción. Pasa siempre en LinkedIn:
    # la pantalla de resultados muestra título, empresa, lugar, sueldo y cuánta
    # gente aplicó, y nada más. Con eso el ORDEN sigue valiendo —competencia y
    # frescura son datos duros— pero el veredicto "aplicá a esta" no, porque el
    # puntaje de stack se calcula sobre un título de seis palabras.
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


def analizar(texto: str, criterio: Criterio, connects: int = 0) -> Analisis:
    """Lo pegado, convertido en una lista ordenada de a cuáles aplicar."""
    bloques, descartados = separar(texto)
    sitio = detectar(texto)
    entradas = [_a_oferta(b, sitio) for b in bloques]
    veredictos = evaluar(entradas, criterio)
    promedio = sum(len(b) for b in bloques) / len(bloques) if bloques else 0
    poca_informacion = bool(bloques) and promedio < MINIMO_PARA_DECIDIR

    # Los Connects sólo existen en Upwork. En LinkedIn postular es gratis, así
    # que no hay presupuesto que repartir: entran todas las que pasen el mínimo.
    if poca_informacion:
        # Sin descripción no se elige ninguna: decir "aplicá a esta" con un
        # puntaje sacado de seis palabras sería inventar una certeza. El orden
        # queda, que es lo que de verdad se puede afirmar.
        elegidas: list[Veredicto] = []
    elif sitio == "upwork" and connects:
        elegidas = repartir(veredictos, connects, criterio.puntaje_minimo)
    else:
        elegidas = [v for v in veredictos if v.total >= criterio.puntaje_minimo]
    return Analisis(
        sitio=sitio,
        veredictos=veredictos,
        elegidas=elegidas,
        descartados=descartados,
        connects_gastados=sum(v.entrada.connects for v in elegidas) if sitio == "upwork" else 0,
        connects_disponibles=connects,
        poca_informacion=poca_informacion,
    )


def a_json(analisis: Analisis) -> dict:
    """La forma que consume la página."""

    def fila(veredicto: Veredicto, aplicar: bool) -> dict:
        entrada = veredicto.entrada
        return {
            "titulo": entrada.oferta.titulo,
            "empresa": entrada.oferta.empresa,
            "puntaje": veredicto.total,
            "aplicar": aplicar,
            "connects": entrada.connects if analisis.sitio == "upwork" else None,
            "competencia": entrada.propuestas,
            "horas": entrada.horas,
            "verificado": entrada.verificado,
            "senales": list(veredicto.puntaje.senales),
            "motivos": list(veredicto.puntaje.motivos) + list(veredicto.motivos),
        }

    ids = {id(v) for v in analisis.elegidas}
    return {
        "sitio": analisis.sitio,
        "descartados": analisis.descartados,
        "poca_informacion": analisis.poca_informacion,
        "connects_gastados": analisis.connects_gastados,
        "connects_disponibles": analisis.connects_disponibles,
        "ofertas": [fila(v, id(v) in ids) for v in analisis.veredictos],
    }
