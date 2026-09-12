"""Las herramientas del CV: leer, agregar una certificación, regenerar (Fase 6).

Lo que más importa acá es que **las dos versiones se mantengan juntas**. El CV
existe en inglés y español, y agregar algo a una sola es exactamente cómo
aparecieron seis copias de distinto tamaño sin saber cuál se había mandado.
"""

from pathlib import Path

import pytest

from tools.cv import (
    ArchivosDelCV,
    CertificacionArgs,
    VerArgs,
    _agregar_certificacion,
    _ver,
    build_cv_tools,
)

HTML = """<html><head><style>.cert{color:red}</style></head><body>
<div class="sec-h">CERTIFICATIONS</div>
<div class="cert"><strong>Python TOTAL</strong> — Udemy, 2026</div>
<div class="cert"><strong>Claude 101</strong> — Anthropic, 2026</div>
</body></html>"""


@pytest.fixture
def cv(tmp_path: Path) -> ArchivosDelCV:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "cv-en.html").write_text(HTML, encoding="utf-8")
    (tmp_path / "build" / "cv-es.html").write_text(
        HTML.replace("CERTIFICATIONS", "CERTIFICACIONES"), encoding="utf-8"
    )
    return ArchivosDelCV(tmp_path)


def test_una_certificacion_va_a_las_dos_versiones(cv: ArchivosDelCV) -> None:
    """Agregarla a una sola las desincroniza, y la desincronización es el
    problema que estas herramientas vienen a resolver."""
    resultado = _agregar_certificacion(
        cv, CertificacionArgs(nombre="Kubernetes CKA", emisor="Linux Foundation", anio="2026")
    )
    assert resultado.ok is True
    for ruta in cv.html.values():
        assert "Kubernetes CKA" in ruta.read_text(encoding="utf-8")


def test_se_copia_el_markup_que_ya_existe(cv: ArchivosDelCV) -> None:
    """El bloque nuevo hereda las clases del último existente en vez de
    inventar HTML: así el estilo sale bien sin conocerlo, y sigue funcionando
    si el diseño cambia."""
    _agregar_certificacion(
        cv, CertificacionArgs(nombre="Nueva", emisor="Alguien", anio="2026")
    )
    html = cv.html["en"].read_text(encoding="utf-8")
    assert '<div class="cert"><strong>Nueva</strong>' in html


def test_no_se_agrega_dos_veces(cv: ArchivosDelCV) -> None:
    """Pedir lo mismo dos veces —fácil en una conversación— no puede dejar el
    certificado duplicado en el CV."""
    args = CertificacionArgs(nombre="Python TOTAL", emisor="Udemy", anio="2026")
    resultado = _agregar_certificacion(cv, args)
    assert resultado.ok is False
    assert "ya está" in resultado.content


def test_la_credencial_es_opcional(cv: ArchivosDelCV) -> None:
    _agregar_certificacion(
        cv, CertificacionArgs(nombre="Con id", emisor="X", anio="2026", credencial="abc123")
    )
    assert "abc123" in cv.html["en"].read_text(encoding="utf-8")


def test_sin_los_html_se_avisa_en_vez_de_explotar(tmp_path: Path) -> None:
    """La carpeta puede no estar montada —el CV vive en Downloads, no en el
    repo— y eso no es un error del agente."""
    resultado = _agregar_certificacion(
        ArchivosDelCV(tmp_path), CertificacionArgs(nombre="X", emisor="Y", anio="2026")
    )
    assert resultado.ok is False


def test_ver_el_cv_devuelve_texto_sin_markup(cv: ArchivosDelCV) -> None:
    """Lo que el modelo tiene que leer es el contenido; el HTML y el CSS solo
    gastan contexto."""
    contenido = _ver(cv, VerArgs(), 4000).content
    assert "Python TOTAL" in contenido
    assert "<div" not in contenido
    assert "color:red" not in contenido, "se coló el CSS"


def test_lo_que_se_lee_va_marcado_como_no_confiable(cv: ArchivosDelCV) -> None:
    """Es un archivo del disco como cualquier otro: si alguien mete
    instrucciones en el CV, entran como datos."""
    assert "NO CONFIABLE" in _ver(cv, VerArgs(), 4000).content


def test_las_tres_herramientas_se_arman(tmp_path: Path) -> None:
    nombres = {h.name for h in build_cv_tools(tmp_path, 4000)}
    assert nombres == {"ver_cv", "agregar_certificacion", "regenerar_cv"}


async def test_la_herramienta_corre_de_verdad(cv: ArchivosDelCV) -> None:
    agregar = {h.name: h for h in build_cv_tools(cv.carpeta, 4000)}["agregar_certificacion"]
    resultado = await agregar.run(
        CertificacionArgs(nombre="Desde la herramienta", emisor="X", anio="2026")
    )
    assert resultado.ok is True
    assert "Desde la herramienta" in cv.html["es"].read_text(encoding="utf-8")
