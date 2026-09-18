"""Cada plataforma entiende una sintaxis distinta, y ahí está todo el punto.

Si las cuatro alertas terminaran diciendo lo mismo, el módulo sobraría — y peor:
una cadena booleana pegada en un board que no entiende booleano no filtra, busca
esa frase literal y no devuelve nada. El síntoma es una alerta que nunca dispara,
que se ve igual que "no hay ofertas".
"""

from pathlib import Path

from empleo.alertas import FUERA, TOPE_TERMINOS, a_json, alertas
from empleo.criterio import Criterio, cargar_criterio
from tests.conftest import AUTH

CRITERIO = cargar_criterio(Path(__file__).resolve().parent.parent / "perfil" / "busqueda.toml")


def _de(plataforma: str):
    return next(a for a in alertas(CRITERIO) if a.plataforma == plataforma)


def test_linkedin_y_upwork_reciben_booleano() -> None:
    for plataforma in ("LinkedIn", "Upwork"):
        assert " OR " in _de(plataforma).consulta
        assert "NOT (" in _de(plataforma).consulta


def test_los_boards_chicos_no_reciben_booleano() -> None:
    """Get on Board y RemoteOK no lo entienden: una cadena con paréntesis ahí se
    busca literal y no devuelve nada."""
    for plataforma in ("Get on Board", "RemoteOK / Wellfound"):
        consulta = _de(plataforma).consulta
        assert "OR" not in consulta
        assert "(" not in consulta


def test_ninguna_consulta_lleva_comodin() -> None:
    """LinkedIn no acepta `*`, y una cadena distinta por plataforma sería una que
    alguien pega en la equivocada."""
    for alerta in alertas(CRITERIO):
        assert "*" not in alerta.consulta
        assert "{" not in alerta.consulta and "[" not in alerta.consulta


def test_upwork_no_filtra_por_seniority() -> None:
    """En Upwork el título lo pone el cliente y casi nunca dice "senior": filtrar
    por eso deja fuera casi todo. Lo que ordena ahí es la competencia."""
    assert "senior" not in _de("Upwork").consulta.lower()
    assert "senior" in _de("LinkedIn").consulta.lower()


def test_las_exclusiones_estan_en_las_booleanas() -> None:
    for termino in FUERA:
        assert termino in _de("LinkedIn").consulta


def test_los_terminos_salen_del_stack_y_no_de_una_lista_aparte() -> None:
    """El criterio que puntúa y el que busca tienen que ser el mismo, o la alerta
    te trae lo que el cazador después hunde."""
    fuerte = CRITERIO.stack["fuerte"][0]
    assert fuerte in _de("LinkedIn").consulta


def test_un_criterio_sin_stack_no_inventa_alertas() -> None:
    assert alertas(Criterio()) == []
    assert a_json(Criterio()) == []


def test_no_entran_mas_terminos_de_los_que_se_leen() -> None:
    consulta = _de("LinkedIn").consulta
    grupo = consulta.split(")")[0]
    assert grupo.count(" OR ") < TOPE_TERMINOS


def test_la_pagina_pide_las_alertas_al_abrirse(cliente) -> None:
    respuesta = cliente.get("/api/v1/ofertas/alertas", headers=AUTH)
    assert respuesta.status_code == 200, respuesta.text
    plataformas = [a["plataforma"] for a in respuesta.json()["alertas"]]
    assert "LinkedIn" in plataformas and "Upwork" in plataformas


def test_las_alertas_piden_credencial(cliente) -> None:
    assert cliente.get("/api/v1/ofertas/alertas").status_code == 401
