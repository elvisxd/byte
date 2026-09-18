"""Qué lista de modelos recibe un brazo remoto después de pasar por el catálogo.

Dos reglas, y las dos son sobre dinero o sobre muestra:

  · un id de OpenRouter sin `:free` se va aunque deje el brazo sin modelos, porque
    un brazo muerto se ve en el log y una factura no;
  · un catálogo que descarta a TODOS no deja el brazo sin lista, porque una ficha
    vieja no puede costar la muestra de un día.
"""

from datetime import UTC, datetime

import pytest

from agent.catalogo import Catalogo
from api.config import Settings
from paper.sesion import _catalogo_y_lista

GRATIS = "openrouter/qwen/qwen3.8-27b:free"
COBRADO = "openrouter/deepseek/deepseek-chat-v3-0324"
GLM = "openrouter/z-ai/glm-5.2:free"


def _ajustes(ruta="") -> Settings:
    return Settings(OPENROUTER_API_KEY="k", BYTE_CATALOGO_DB=str(ruta))


def test_sin_catalogo_la_lista_pasa_tal_cual():
    catalogo, nombres = _catalogo_y_lista(_ajustes(), [GRATIS, GLM])
    assert catalogo is None
    assert nombres == [GRATIS, GLM]


def test_el_id_sin_free_no_llega_al_brazo(tmp_path):
    """La lista entra por una variable de Railway: un sufijo perdido es una factura."""
    catalogo, nombres = _catalogo_y_lista(_ajustes(tmp_path / "c.db"), [GRATIS, COBRADO])
    assert nombres == [GRATIS]
    # Y queda anotado, para que el parte lo explique en vez de que desaparezca.
    assert catalogo.descartado(COBRADO)["motivo"] == "se_cobra"
    catalogo.cerrar()


def test_un_brazo_entero_de_pago_no_arranca(tmp_path):
    """⚠ Acá sí se prefiere el brazo muerto: pagar es peor que no medir."""
    with pytest.raises(ValueError, match=":free"):
        _catalogo_y_lista(_ajustes(tmp_path / "c.db"), [COBRADO])


def test_lo_descartado_sale_de_la_lista(tmp_path):
    ruta = tmp_path / "c.db"
    cat = Catalogo(ruta)
    cat.descartar(GLM, codigo=404, texto="No endpoints found that support tool use")
    cat.cerrar()

    catalogo, nombres = _catalogo_y_lista(_ajustes(ruta), [GRATIS, GLM])
    assert nombres == [GRATIS]
    catalogo.cerrar()


def test_si_el_catalogo_descarta_a_todos_se_prueba_la_lista_entera(tmp_path):
    """Una ficha vieja no puede dejar un brazo sin una sola vuelta en el día."""
    ruta = tmp_path / "c.db"
    cat = Catalogo(ruta)
    for modelo in (GRATIS, GLM):
        cat.descartar(modelo, codigo=404, texto="does not exist", ahora=datetime.now(UTC))
    cat.cerrar()

    catalogo, nombres = _catalogo_y_lista(_ajustes(ruta), [GRATIS, GLM])
    assert nombres == [GRATIS, GLM]
    catalogo.cerrar()


def test_un_catalogo_que_no_se_puede_abrir_no_para_el_brazo(tmp_path):
    """Es un apunte, no un requisito. Un directorio en el lugar del archivo lo simula."""
    estorbo = tmp_path / "c.db"
    estorbo.mkdir()
    catalogo, nombres = _catalogo_y_lista(_ajustes(estorbo), [GRATIS])
    assert catalogo is None
    assert nombres == [GRATIS]
