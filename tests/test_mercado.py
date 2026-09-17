"""La radiografía del mercado: qué se cuenta y, sobre todo, qué no se afirma.

Lo que se cuida acá es no convertir una casualidad en un consejo. Este módulo
existe para decidir qué palabras poner en una búsqueda y qué escribir en el CV:
un número mal medido se paga en semanas de búsqueda apuntando al lugar
equivocado.
"""

from pathlib import Path

from empleo.criterio import cargar_criterio
from empleo.mercado import MINIMO_PARA_PESO_MUERTO, analizar, busquedas, informe
from empleo.oferta import Oferta

CRITERIO = cargar_criterio(Path(__file__).resolve().parent.parent / "perfil" / "busqueda.toml")
CV = "Python, FastAPI, LangGraph, pgvector y RAG en producción."

ENCAJA = "LangGraph, RAG, pgvector, Python, FastAPI. Kubernetes. Anywhere in the world."


def _oferta(descripcion: str, titulo: str = "Senior AI Engineer") -> Oferta:
    return Oferta(
        fuente="x",
        id_externo=descripcion[:20],
        titulo=titulo,
        empresa="A",
        url="u",
        descripcion=descripcion,
    )


def test_se_cuenta_solo_sobre_las_que_encajan() -> None:
    """El mercado entero pide WordPress y PHP. Es cierto y no sirve para nada: lo
    que mueve una decisión es qué piden las ofertas a las que podrías aplicar."""
    radiografia = analizar(
        [_oferta(ENCAJA), _oferta("wordpress php mysql", "WordPress dev")], CRITERIO, CV
    )
    assert radiografia.analizadas == 2
    assert radiografia.encajan == 1
    terminos = [t for t, _ in radiografia.piden_y_tenes + radiografia.piden_y_no_decis]
    assert "wordpress" not in terminos


def test_un_termino_repetido_en_una_oferta_cuenta_una_vez() -> None:
    """Una descripción que dice "Python" ocho veces no significa que el mercado lo
    pida ocho veces más. Sin esto, el ranking lo gana quien escribe más largo."""
    radiografia = analizar([_oferta("Python " * 8 + ENCAJA)] * 2, CRITERIO, CV)
    assert dict(radiografia.piden_y_tenes)["python"] == 2


def test_lo_tuyo_y_lo_que_falta_salen_separados() -> None:
    """Son dos decisiones distintas: lo que ya decís va al titular, y lo que no
    decís es lo que hay que escribir o prepararse para que te pregunten."""
    radiografia = analizar([_oferta(ENCAJA)] * 3, CRITERIO, CV)
    tenes = [t for t, _ in radiografia.piden_y_tenes]
    faltan = [t for t, _ in radiografia.piden_y_no_decis]
    assert "langgraph" in tenes and "rag" in tenes
    assert "kubernetes" in faltan
    assert not set(tenes) & set(faltan)


def test_con_pocas_ofertas_no_se_afirma_que_algo_sea_peso_muerto() -> None:
    """Decir "nadie pide TypeScript" tras mirar cuatro ofertas no es una medición:
    es una casualidad con formato de conclusión, y haría borrar de la búsqueda
    algo que sí sirve."""
    pocas = analizar([_oferta(ENCAJA)] * 3, CRITERIO, CV)
    assert pocas.peso_muerto == ()
    assert "no se puede decir qué términos tuyos no pide nadie" in informe(pocas)

    muchas = analizar([_oferta(ENCAJA)] * MINIMO_PARA_PESO_MUERTO, CRITERIO, CV)
    assert "typescript" in muchas.peso_muerto


def test_las_busquedas_sirven_en_linkedin_tambien() -> None:
    """LinkedIn acepta los mismos AND/OR/NOT pero NO el comodín `*`, ni llaves ni
    corchetes. Una cadena con comodín rompe allá sin avisar, así que no se usa
    ninguno y la misma sirve en las dos."""
    cadenas = busquedas(analizar([_oferta(ENCAJA)] * 3, CRITERIO, CV))
    assert cadenas
    for cadena in cadenas.values():
        assert "*" not in cadena
        assert "{" not in cadena and "[" not in cadena


def test_los_terminos_de_varias_palabras_van_entre_comillas() -> None:
    """Sin comillas, `retrieval augmented` se lee como dos términos sueltos y la
    búsqueda devuelve cualquier cosa que diga "augmented"."""
    radiografia = analizar([_oferta("retrieval augmented generation " + ENCAJA)] * 3, CRITERIO, CV)
    if any(t == "retrieval augmented" for t, _ in radiografia.piden_y_tenes[:6]):
        assert '"retrieval augmented"' in busquedas(radiografia)["amplia"]


def test_sin_ofertas_que_encajen_lo_dice_en_vez_de_inventar() -> None:
    """Cero ofertas y cero coincidencias son cosas distintas, y las dos llevan a
    acciones distintas: revisar las fuentes o aflojar el criterio."""
    assert "Ninguna fuente devolvió ofertas" in informe(analizar([], CRITERIO, CV))
    solo_malas = analizar([_oferta("wordpress php", "WordPress dev")], CRITERIO, CV)
    assert "ninguna llegó al mínimo" in informe(solo_malas)
