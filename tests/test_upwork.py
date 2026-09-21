"""Upwork: pegar la búsqueda y repartir los Connects.

Lo que se cuida acá es que el reparto no regale Connects. En el plan Basic son
10 gratis por mes y una propuesta cuesta 6: cada error son treinta días.
"""

from dataclasses import replace
from pathlib import Path

from empleo.criterio import cargar_criterio
from empleo.upwork import evaluar, informe, parsear, repartir

CRITERIO = cargar_criterio(Path(__file__).resolve().parent.parent / "perfil" / "busqueda.toml")

BUENA = """Senior AI Engineer for LangGraph RAG pipeline
Hourly: $60.00-$90.00
Posted 25 minutes ago
Proposals: Less than 5
Payment verified
$50K+ spent
Python FastAPI pgvector"""

FLOJA = """WordPress theme tweak
Fixed-price - Est. Budget: $50
Posted 6 days ago
Proposals: 20 to 50
Payment unverified"""


def test_lee_lo_que_la_pantalla_de_upwork_muestra() -> None:
    """Los cuatro datos que deciden si vale gastar —propuestas, verificación,
    gasto del cliente y antigüedad— están en la tarjeta de búsqueda y en ningún
    otro board. Sin leerlos, el reparto sería a ciegas."""
    entrada = parsear(BUENA)[0]
    assert entrada.propuestas == 5
    assert entrada.verificado is True
    assert entrada.gastado_usd == 50_000
    assert entrada.horas is not None and entrada.horas < 1
    # La antigüedad va al campo estándar para que la puntúe el mismo código que
    # todo lo demás, en vez de tener dos frescuras que se contradicen.
    assert entrada.oferta.publicada


def test_un_rango_de_propuestas_se_lee_por_el_lado_pesimista() -> None:
    """Upwork da rangos: "5 to 10". Leerlo como 5 es gastar Connects en una
    oferta que ya tenía cola; leerlo como 10 sólo cuesta saltearse una dudosa.
    Con uno o dos tiros por mes, el pesimismo es barato."""
    assert parsear("Algo\nProposals: 5 to 10\nx")[0].propuestas == 10
    assert parsear("Algo\nProposals: Less than 5\nx")[0].propuestas == 5
    assert parsear("Algo\nProposals: 50+\nx")[0].propuestas == 50


def test_el_pago_sin_verificar_hunde_la_oferta() -> None:
    """Un cliente que nunca verificó el pago puede no contratar jamás, y la
    propuesta ya se pagó. Cuando los Connects son escasos, eso pesa."""
    sin_verificar = evaluar(parsear(FLOJA), CRITERIO)[0]
    assert any("sin verificar" in m for m in sin_verificar.motivos)
    assert sin_verificar.total < CRITERIO.puntaje_minimo


def test_el_presupuesto_se_respeta_aunque_sobre_una_buena() -> None:
    """Con 10 Connects y propuestas de 6, entra UNA. La segunda no entra aunque
    supere el mínimo: gastar de más hoy es no tener con qué postular mañana."""
    veredictos = evaluar(parsear(f"{BUENA}\n\n{BUENA}"), CRITERIO)
    assert all(v.total > CRITERIO.puntaje_minimo for v in veredictos)
    assert len(repartir(veredictos, presupuesto=10, minimo=CRITERIO.puntaje_minimo)) == 1
    assert len(repartir(veredictos, presupuesto=12, minimo=CRITERIO.puntaje_minimo)) == 2


def test_no_gastar_es_un_resultado_valido() -> None:
    """Si nada llega al mínimo, la respuesta correcta es no gastar. Un repartidor
    que siempre elige algo convierte el presupuesto en ruido."""
    salida = informe(FLOJA, CRITERIO, presupuesto=10)
    assert "GASTAR" not in salida
    assert "no gastar hoy es una decisión" in salida


def test_lo_pegado_que_no_se_entiende_lo_dice_en_vez_de_inventar() -> None:
    """Pegar mal es lo más común. Devolver cero ofertas en silencio se lee como
    "no hay nada bueno hoy", que es una conclusión distinta y falsa."""
    assert "No reconocí ninguna oferta" in informe("una línea suelta", CRITERIO, 10)


def test_una_oferta_sin_metadatos_se_puntua_igual_por_su_texto() -> None:
    """Copiar y pegar a veces se lleva sólo el título y la descripción. Eso no
    puede romper nada: se puntúa por el stack, sin los ajustes de Upwork."""
    entrada = parsear("Senior Python Engineer for RAG system\nWe use LangGraph and pgvector.")
    assert len(entrada) == 1
    assert entrada[0].propuestas is None
    assert evaluar(entrada, CRITERIO)[0].total > 0


def test_el_minimo_del_reparto_sale_del_mismo_criterio_que_el_aviso() -> None:
    """Para que subir el listón en el TOML valga para todo, no sólo para el
    cazador: dos umbrales distintos se desincronizan y nadie se entera."""
    duro = replace(CRITERIO, puntaje_minimo=10_000)
    assert repartir(evaluar(parsear(BUENA), CRITERIO), 10, duro.puntaje_minimo) == []


# El pegado tal como sale de la pantalla de búsqueda: con los renglones en
# blanco que Upwork mete DENTRO de la tarjeta, antes de "Skills" y antes del pie
# del cliente. Es lo que rompía el corte por línea en blanco.
PANTALLA_REAL = """Posted 50 minutes ago
•
Proposals: 20 to 50
WhatsApp API Consultant
Hourly: $75-$200 - Expert - Est. Time: Less than 1 month
We need an expert on the Meta WhatsApp Cloud API. Node/TypeScript, Postgres.

Skills
WhatsApp
API Integration

Verified 
Payment verified
 
Rating is 5.0 out of 5.
 $300K+ spent 
  United States

Posted yesterday
•
Proposals: 5 to 10
AI/ML Engineer for Image Generation
Hourly: $19-$40 - Intermediate - Est. Time: More than 6 months
This is a part-time, long-term role with potential for ongoing collaboration.

Skills
Adobe Photoshop

Unverified 
Payment unverified
 
Rating is 0 out of 5.
 $0 spent 
  India"""


def test_los_renglones_en_blanco_de_la_tarjeta_no_parten_la_oferta() -> None:
    """Upwork deja renglones vacíos DENTRO de cada tarjeta. Cortando ahí, una
    oferta se parte en tres: el título queda en un pedazo y "$300K+ spent" en
    otro, así que el historial del cliente le suma puntos a un fragmento sin
    texto. Medido contra un pegado real: 6 ofertas salían como 12 bloques."""
    entradas = parsear(PANTALLA_REAL)
    assert len(entradas) == 2
    # Y el dato del cliente quedó pegado a SU oferta, no suelto.
    assert entradas[0].gastado_usd == 300_000
    assert entradas[0].verificado is True
    assert entradas[1].gastado_usd == 0


def test_el_titulo_es_el_titulo_y_no_la_linea_de_presupuesto() -> None:
    """Elegir "el primer renglón largo" devolvía la línea de tarifa, que suele
    ser la más larga de la tarjeta. Sin el título, el aviso no se puede leer."""
    entradas = parsear(PANTALLA_REAL)
    assert entradas[0].oferta.titulo == "WhatsApp API Consultant"
    assert entradas[1].oferta.titulo == "AI/ML Engineer for Image Generation"


def test_el_trabajo_que_sigue_vale_mas_que_la_changa() -> None:
    """El costo de conseguir al cliente se paga una vez y se amortiza sobre lo
    que dure el contrato. Un fijo que sigue vale más que uno más grande que no."""
    largo = parsear("Senior Python Engineer\nThis is a long-term role, ongoing collaboration.")
    assert "largo_plazo" in evaluar(largo, CRITERIO)[0].puntaje.senales


def test_negar_el_largo_plazo_no_cuenta_como_largo_plazo() -> None:
    """ "This is not a long-term role" tiene todas las palabras buenas adentro y
    dice lo contrario. La negación gana, igual que con el patrocinio."""
    negado = parsear("Senior Python Engineer\nThis is not a long-term role, one-off project.")
    senales = evaluar(negado, CRITERIO)[0].puntaje.senales
    assert "largo_plazo" not in senales
    assert "sin_largo_plazo" in senales


def test_el_cliente_que_nunca_contrato_no_es_neutro() -> None:
    """$0 gastados es el que todavía no contrató a nadie: la propuesta puede no
    llegar a competir nunca. Pero "sin dato" es otra cosa —el pegado se cortó—,
    y castigar eso convertiría un copiado incompleto en un cliente malo."""
    cero = parsear("Algo\nProposals: 5 to 10\nPayment unverified\n$0 spent")[0]
    assert cero.gastado_usd == 0
    assert any("nunca contrató" in m for m in evaluar([cero], CRITERIO)[0].motivos)

    sin_dato = parsear("Algo\nProposals: 5 to 10\nPayment unverified")[0]
    assert sin_dato.gastado_usd is None
    assert not any("nunca contrató" in m for m in evaluar([sin_dato], CRITERIO)[0].motivos)


def test_el_historial_del_cliente_es_una_escala_y_no_un_umbral() -> None:
    """$300K y $5K son los dos "cliente con historial" y no son el mismo
    cliente. Con un solo umbral, el que gastó sesenta veces más puntúa igual."""

    def total(gastado: str) -> int:
        entrada = parsear(f"Algo\nProposals: Less than 5\nPayment verified\n{gastado} spent")
        return evaluar(entrada, CRITERIO)[0].total

    assert total("$300K+") > total("$20K+") > total("$2K+") > total("$100")


# El país del cliente es la ÚLTIMA línea de la tarjeta en la pantalla de Upwork.
_EEUU = """Senior Python Engineer for RAG pipeline
Proposals: Less than 5
Payment verified
$120K+ spent
United States"""

_INDIA = """Senior Python Engineer for RAG pipeline
Proposals: Less than 5
Payment verified
$120K+ spent
India"""


def test_el_pais_del_cliente_ordena_sin_descartar() -> None:
    """US y Canadá suman; India y España restan como un junior: con 40 la oferta
    cae debajo del mínimo aunque el stack coincida entero.

    Pero NO se descarta en el código. Ninguna señal descarta sola acá, y filtrar
    en silencio es cómo un criterio equivocado se vuelve invisible: no verías las
    ofertas que te estás perdiendo, verías menos ofertas y nada más."""
    eeuu = evaluar(parsear(_EEUU), CRITERIO)[0]
    india = evaluar(parsear(_INDIA), CRITERIO)[0]
    assert "cliente_norteamerica" in eeuu.puntaje.senales
    assert "cliente_bloqueado" in india.puntaje.senales
    assert eeuu.total > india.total
    # La de India sigue existiendo y con su señal a la vista, no desaparece.
    assert india.puntaje.senales


def test_nombrar_un_pais_en_la_descripcion_no_es_ser_de_ahi() -> None:
    """ "Some of our engineers are based in India and Spain" lo escribe una
    empresa de EE.UU. contratando afuera — es lo contrario de una señal mala.

    El patrón exige que el país sea la línea ENTERA, que es como Upwork lo pone
    al pie de la tarjeta. Sin ese ancla, esta oferta se hundiría 40 puntos por
    mencionar dónde vive su equipo."""
    texto = """Senior Python Engineer for RAG pipeline
Proposals: Less than 5
We are a US company with a distributed team; some of our engineers are based in
India and Spain, and we hire worldwide through Deel.
Payment verified
$120K+ spent
United States"""
    senales = evaluar(parsear(texto), CRITERIO)[0].puntaje.senales
    assert "cliente_norteamerica" in senales
    assert "cliente_bloqueado" not in senales


def test_canada_cuenta_igual_que_estados_unidos() -> None:
    """Son la misma lista: se pidieron los dos juntos."""
    canada = parsear(_EEUU.replace("United States", "Canada"))
    assert "cliente_norteamerica" in evaluar(canada, CRITERIO)[0].puntaje.senales
