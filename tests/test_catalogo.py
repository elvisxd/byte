"""El catálogo de modelos: qué hay, qué sirve y qué quedó descartado.

Lo que protege es la asimetría del error. Un descarte de más borra un modelo que
funcionaba y nadie se entera; un descarte de menos cuesta una llamada fallida que
sale en el log. Así que casi todos los tests de acá comprueban que algo NO se
descarta: que un 429 no entra, que un 413 no entra, que un 404 desconocido se
revisa al mes en vez de irse para siempre, y que un modelo que vuelve a salir en
`/v1/models` NO recupera el permiso por eso.
"""

from datetime import UTC, datetime, timedelta

import pytest

from agent.catalogo import REVISION_SIN_ACCESO, Catalogo, motivo_permanente, proveedor_de

# La frase exacta que devolvió OpenRouter el 2026-09-18 a las 12:00 EDT con
# `z-ai/glm-5.2:free`, que es el caso que hizo falta este módulo.
GLM_SIN_HERRAMIENTAS = (
    "404 - {'error': {'message': 'No endpoints found that support tool use. "
    "To learn more about provider routing, visit: …'}}"
)
GLM = "openrouter/z-ai/glm-5.2:free"


@pytest.fixture
def catalogo(tmp_path):
    cat = Catalogo(tmp_path / "catalogo-modelos.db")
    yield cat
    cat.cerrar()


def test_proveedor_sale_del_prefijo():
    assert proveedor_de(GLM) == "openrouter"
    assert proveedor_de("gemini-3.8-flash") == "gemini"
    assert proveedor_de("groq/openai/gpt-oss-120b") == "groq"
    # El id de NIM lleva barra dentro y el prefijo es lo único que se mira.
    assert proveedor_de("nvidia/deepseek-ai/deepseek-v4-flash-0731") == "nvidia"
    assert proveedor_de("qwen3:14b") == "local"


def test_el_glm_queda_descartado_para_siempre(catalogo):
    """El caso que motivó el módulo: sin herramientas no es «está ocupado»."""
    assert catalogo.descartar(GLM, codigo=404, texto=GLM_SIN_HERRAMIENTAS) == "sin_herramientas"
    ficha = catalogo.descartado(GLM)
    assert ficha is not None
    assert ficha["motivo"] == "sin_herramientas"
    # ⚠ Lo que de verdad importa: no se revisa nunca. Un modelo sin llamada a
    # herramientas no le sirve a este agente ni hoy ni en un año.
    assert ficha["revisable_desde"] is None
    # Y sigue descartado dentro de diez años.
    assert catalogo.descartado(GLM, ahora=datetime.now(UTC) + timedelta(days=3650)) is not None


def test_la_negacion_va_al_principio_de_la_frase():
    """`"not support" in texto` no habría atrapado la frase de OpenRouter."""
    assert "not support" not in GLM_SIN_HERRAMIENTAS.lower()
    assert motivo_permanente(404, GLM_SIN_HERRAMIENTAS) == "sin_herramientas"


def test_un_404_desconocido_se_revisa_al_mes(catalogo):
    """Fallar hacia «revisable» cuesta una petición; hacia «nunca», un modelo."""
    ahora = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    motivo = catalogo.descartar(
        "cerebras/llama-3.3-70b",
        codigo=404,
        texto="Model does not exist or you do not have access to it",
        ahora=ahora,
    )
    assert motivo == "sin_acceso"
    assert catalogo.descartado("cerebras/llama-3.3-70b", ahora=ahora) is not None
    # Pasado el mes vuelve a estar disponible y se prueba de nuevo.
    despues = ahora + REVISION_SIN_ACCESO + timedelta(days=1)
    assert catalogo.descartado("cerebras/llama-3.3-70b", ahora=despues) is None


def test_el_402_no_se_revisa_solo(catalogo):
    """Lo que tiene que cambiar es la cuenta, no el modelo: esperar no lo arregla."""
    motivo = catalogo.descartar(
        "cerebras/qwen-3.8-27b",
        codigo=402,
        texto="Payment required to access this resource",
        ahora=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert motivo == "hay_que_pagar"
    ficha = catalogo.descartado("cerebras/qwen-3.8-27b")
    assert ficha is not None and ficha["revisable_desde"] is None


def test_estar_en_el_catalogo_no_levanta_un_descarte(catalogo):
    """⚠ El GLM SIGUE saliendo en /v1/models de OpenRouter y sigue sin herramientas.

    Y los 82 modelos que lista NVIDIA son el catálogo público, no el de la cuenta.
    Que un id exista dice qué hay, no qué se puede usar.
    """
    catalogo.descartar(GLM, codigo=404, texto=GLM_SIN_HERRAMIENTAS)
    catalogo.visto_en_catalogo([GLM])
    assert catalogo.descartado(GLM) is not None
    ficha = next(f for f in catalogo.fichas("openrouter") if f["modelo"] == GLM)
    # Se anota que existe —es información útil— sin tocar el veredicto.
    assert ficha["en_catalogo"] == 1
    assert ficha["veredicto"] == "descartado"


def test_haber_contestado_una_vuelta_si_lo_levanta(catalogo):
    """Es la única prueba directa, y por eso es lo único que rehabilita."""
    catalogo.descartar(GLM, codigo=404, texto="does not exist")
    catalogo.funciono(GLM)
    assert catalogo.descartado(GLM) is None
    ficha = next(f for f in catalogo.fichas() if f["modelo"] == GLM)
    assert ficha["veredicto"] == "sirve"
    assert ficha["veces_funciono"] == 1
    assert ficha["funciono_en"]


def test_lo_que_desaparece_del_catalogo_se_marca(catalogo):
    """El aviso que necesita OpenRouter, cuyo catálogo gratis cambia sin avisar."""
    catalogo.visto_en_catalogo([GLM, "openrouter/qwen/qwen3.8-27b:free"])
    catalogo.visto_en_catalogo(["openrouter/qwen/qwen3.8-27b:free"])
    fichas = {f["modelo"]: f for f in catalogo.fichas("openrouter")}
    assert fichas[GLM]["en_catalogo"] == 0
    assert fichas["openrouter/qwen/qwen3.8-27b:free"]["en_catalogo"] == 1
    # La primera vez que se vio no se pierde al desaparecer.
    assert fichas[GLM]["visto_primero"]


def test_un_proveedor_no_borra_el_catalogo_de_otro(catalogo):
    """`visto_en_catalogo` solo puede apagar filas del proveedor que sondeó."""
    catalogo.visto_en_catalogo(["groq/openai/gpt-oss-120b"])
    catalogo.visto_en_catalogo([GLM])
    fichas = {f["modelo"]: f for f in catalogo.fichas()}
    assert fichas["groq/openai/gpt-oss-120b"]["en_catalogo"] == 1


def test_filtrar_conserva_el_orden(catalogo):
    """En un relevo el orden NO es decorativo: el primero se reserva para los cierres."""
    lista = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]
    catalogo.descartar("gemini-3.7-flash", codigo=404, texto="not found")
    usables, fuera = catalogo.filtrar(lista)
    assert usables == ["gemini-3.8-flash", "gemini-3.5-flash"]
    assert [f["modelo"] for f in fuera] == ["gemini-3.7-flash"]


def test_el_id_sin_free_es_otra_ficha(catalogo):
    """Mismo modelo, dos facturas: no pueden compartir veredicto."""
    catalogo.marcar_se_cobra("openrouter/deepseek/deepseek-chat-v3-0324")
    assert catalogo.descartado("openrouter/deepseek/deepseek-chat-v3-0324") is not None
    assert catalogo.descartado("openrouter/deepseek/deepseek-chat-v3-0324:free") is None
    ficha = catalogo.descartado("openrouter/deepseek/deepseek-chat-v3-0324")
    assert ficha["motivo"] == "se_cobra" and ficha["revisable_desde"] is None


def test_se_reabre_sobre_el_mismo_archivo(tmp_path):
    """El apunte tiene que sobrevivir al reinicio: es toda la razón del módulo."""
    ruta = tmp_path / "catalogo-modelos.db"
    primero = Catalogo(ruta)
    primero.descartar(GLM, codigo=404, texto=GLM_SIN_HERRAMIENTAS)
    primero.cerrar()

    segundo = Catalogo(ruta)
    assert segundo.descartado(GLM) is not None
    segundo.cerrar()


def test_el_metadato_descarta_pero_se_revisa(catalogo):
    """⚠ Un campo del catálogo no es lo mismo que un 404 del servidor.

    El 404 lo dijo el servidor al intentarlo y no se revisa nunca. Un
    `supported_parameters` ausente en un catálogo de cientos de modelos puede
    estar mal, y creerle «para siempre» tiraría un modelo bueno en silencio.
    """
    catalogo.declarar_sin_herramientas(GLM)
    ficha = catalogo.descartado(GLM)
    assert ficha["motivo"] == "sin_herramientas"
    assert ficha["revisable_desde"] is not None
    assert "supported_parameters" in ficha["evidencia"]

    # Y un 404 de verdad, después, lo endurece a «nunca».
    catalogo.descartar(GLM, codigo=404, texto=GLM_SIN_HERRAMIENTAS)
    assert catalogo.descartado(GLM)["revisable_desde"] is None


def test_nunca_sondeado_no_es_lo_mismo_que_desaparecido(catalogo):
    """⚠ Se vio corriendo el parte: avisaba «ya no sale en /v1/models» de todo.

    `en_catalogo = 0` con `visto_ultimo` puesto es un aviso de verdad —lo listaba
    y desapareció—. Con `visto_ultimo` en NULL es solo que nadie sondeó ese
    proveedor, y gritarlo convierte el parte en ruido.
    """
    catalogo.funciono("gemini-3.8-flash")
    ficha = next(f for f in catalogo.fichas() if f["modelo"] == "gemini-3.8-flash")
    assert ficha["en_catalogo"] == 0
    assert ficha["visto_ultimo"] is None  # nunca se sondeó: no es una desaparición

    catalogo.visto_en_catalogo(["gemini-3.8-flash"])
    catalogo.visto_en_catalogo(["gemini-3.7-flash"])
    ficha = next(f for f in catalogo.fichas() if f["modelo"] == "gemini-3.8-flash")
    assert ficha["en_catalogo"] == 0 and ficha["visto_ultimo"] is not None  # ahora sí


def test_un_modelo_retirado_no_se_revisa_nunca(catalogo):
    """Un 410 es el proveedor diciendo que ya no existe: probarlo al mes es gastar una
    vuelta en un cadáver. Distinto del 404 «sin acceso», que puede ser un typo."""
    motivo = catalogo.descartar(
        "nvidia/deepseek-ai/deepseek-v4-flash-0731",
        codigo=410,
        texto="Error code: 410 - {'title': 'Gone', 'status': 410}",
    )
    assert motivo == "retirado"
    ficha = catalogo.descartado("nvidia/deepseek-ai/deepseek-v4-flash-0731")
    assert ficha is not None and ficha["revisable_desde"] is None
