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
