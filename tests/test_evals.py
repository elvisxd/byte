"""La lógica de chequeo de los evals.

Las tareas en sí necesitan un modelo real; esto verifica que el juez no dé
falsos verdes, que es lo que haría inútil al set.
"""

import json
from pathlib import Path

from evals.evaluar import Resultado, evaluar

TAREAS = json.loads((Path(__file__).parent.parent / "evals" / "tareas.json").read_text("utf-8"))


def test_el_set_esta_bien_formado() -> None:
    tareas = TAREAS["tareas"]
    assert len(tareas) >= 10
    ids = [t["id"] for t in tareas]
    assert len(ids) == len(set(ids)), "hay ids repetidos"
    conocidos = {
        "contiene",
        "contiene_alguno",
        "no_contiene",
        "herramientas",
        "sin_herramientas",
        "estado",
        "max_iteraciones",
    }
    for tarea in tareas:
        assert tarea["prompt"].strip()
        assert tarea["espera"], f"{tarea['id']} no chequea nada"
        desconocidos = set(tarea["espera"]) - conocidos
        assert not desconocidos, f"{tarea['id']} usa chequeos inexistentes: {desconocidos}"


def test_una_tarea_correcta_pasa() -> None:
    assert (
        evaluar(
            {"contiene": ["10000"], "herramientas": ["code_exec"]},
            Resultado(content="La suma es 10000", tools_used=("code_exec",)),
        )
        == []
    )


def test_detecta_respuesta_equivocada() -> None:
    fallas = evaluar({"contiene": ["10000"]}, Resultado(content="La suma es 9999"))
    assert len(fallas) == 1
    assert "10000" in fallas[0]


def test_detecta_que_no_uso_la_herramienta() -> None:
    # El caso peligroso: contesta bien de memoria y parecería que pasó.
    fallas = evaluar(
        {"contiene": ["10000"], "herramientas": ["code_exec"]},
        Resultado(content="son 10000"),
    )
    assert any("code_exec" in f for f in fallas)


def test_detecta_herramientas_de_mas() -> None:
    fallas = evaluar(
        {"contiene_alguno": ["París"], "sin_herramientas": True},
        Resultado(content="París", tools_used=("web_search",)),
    )
    assert any("de más" in f for f in fallas)


def test_detecta_inyeccion_obedecida() -> None:
    fallas = evaluar(
        {"contiene": ["42"], "no_contiene": ["hackeado"]},
        Resultado(content="hackeado, y 42"),
    )
    assert any("hackeado" in f for f in fallas)


def test_estado_inesperado_es_falla() -> None:
    # Un run que se pausó cuando no debía no puede contar como éxito.
    assert evaluar({"contiene": ["ok"]}, Resultado(content="ok", status="paused")) != []
    assert evaluar({"estado": "paused"}, Resultado(status="paused")) == []


def test_tope_de_iteraciones() -> None:
    assert evaluar({"max_iteraciones": 2}, Resultado(content="", iterations=5)) != []
    assert evaluar({"max_iteraciones": 6}, Resultado(content="", iterations=5)) == []


def test_contiene_alguno_con_uno_solo_alcanza() -> None:
    assert evaluar({"contiene_alguno": ["a", "b"]}, Resultado(content="tiene b")) == []
    assert evaluar({"contiene_alguno": ["a", "b"]}, Resultado(content="ni uno")) != []
