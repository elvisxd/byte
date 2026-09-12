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
    ExperienciaArgs,
    ProyectoArgs,
    ReemplazarArgs,
    VerArgs,
    _agregar_certificacion,
    _agregar_experiencia,
    _agregar_proyecto,
    _escapar,
    _reemplazar,
    _ver,
    build_cv_tools,
)

HTML = """<html><head><style>.cert{color:red}</style></head><body>
<div class="sec-h">Experience</div>
<div class="item"><div class="j-title">Founder</div><div class="j-co">Nesty C.A.</div></div>
<div class="sec-h">Selected Projects</div>
<div class="proj"><div class="p-name">Byte</div><div class="p-body">Un agente.</div></div>
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
    _agregar_certificacion(cv, CertificacionArgs(nombre="Nueva", emisor="Alguien", anio="2026"))
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


async def test_la_herramienta_corre_de_verdad(cv: ArchivosDelCV) -> None:
    agregar = {h.name: h for h in build_cv_tools(cv.carpeta, 4000)}["agregar_certificacion"]
    resultado = await agregar.run(
        CertificacionArgs(nombre="Desde la herramienta", emisor="X", anio="2026")
    )
    assert resultado.ok is True
    assert "Desde la herramienta" in cv.html["es"].read_text(encoding="utf-8")


# --- Experiencia ---


def test_una_experiencia_va_arriba_de_todo(cv: ArchivosDelCV) -> None:
    """Los CV se leen de arriba hacia abajo y el puesto actual es lo que
    importa: al final quedaría escondido detrás de trabajos de hace años."""
    _agregar_experiencia(
        cv,
        ExperienciaArgs(
            puesto_en="Staff Engineer",
            puesto_es="Ingeniero Staff",
            empresa="Acme",
            periodo="2026 — Present",
            logros_en=["Led the platform"],
            logros_es=["Lideré la plataforma"],
        ),
    )
    html = cv.html["en"].read_text(encoding="utf-8")
    assert html.index("Staff Engineer") < html.index("Founder"), "quedó debajo del puesto viejo"


def test_cada_idioma_recibe_su_texto(cv: ArchivosDelCV) -> None:
    """Copiar el inglés al español dejaría medio CV en el idioma equivocado."""
    _agregar_experiencia(
        cv,
        ExperienciaArgs(
            puesto_en="Staff Engineer",
            puesto_es="Ingeniero Staff",
            empresa="Acme",
            periodo="2026 — Present",
            logros_en=["Led the platform"],
            logros_es=["Lideré la plataforma"],
        ),
    )
    assert "Led the platform" in cv.html["en"].read_text(encoding="utf-8")
    assert "Lideré la plataforma" in cv.html["es"].read_text(encoding="utf-8")
    assert "Lideré" not in cv.html["en"].read_text(encoding="utf-8")


def test_logros_desbalanceados_se_rechazan(cv: ArchivosDelCV) -> None:
    """Tres bullets en inglés y dos en español dejan las versiones diciendo
    cosas distintas, que es el problema que todo esto viene a evitar."""
    resultado = _agregar_experiencia(
        cv,
        ExperienciaArgs(
            puesto_en="X",
            puesto_es="X",
            empresa="Acme",
            periodo="2026",
            logros_en=["uno", "dos"],
            logros_es=["uno"],
        ),
    )
    assert resultado.ok is False
    assert "mismos" in resultado.content


# --- Proyecto ---


def test_un_proyecto_va_arriba_y_en_los_dos_idiomas(cv: ArchivosDelCV) -> None:
    _agregar_proyecto(
        cv,
        ProyectoArgs(
            nombre="Nuevo",
            descripcion_en="What it proves.",
            descripcion_es="Qué prueba.",
            stack="Python",
            etiqueta="IN PROGRESS",
        ),
    )
    html = cv.html["en"].read_text(encoding="utf-8")
    assert html.index("Nuevo") < html.index("Byte")
    assert "What it proves." in html
    assert "Qué prueba." in cv.html["es"].read_text(encoding="utf-8")


# --- Lo que el modelo escribe no puede romper el HTML ---


def test_lo_que_escribe_el_modelo_se_escapa() -> None:
    """Un `&` o un `<` en un texto —"R&D", "C# < Java"— rompe el documento o
    abre una etiqueta que se come el resto del CV."""
    assert _escapar("R&D en C# < Java") == "R&amp;D en C# &lt; Java"


def test_las_entidades_del_cv_se_preservan() -> None:
    """`&mdash;` y `&middot;` son parte del estilo del CV: escaparlas las
    mostraría literales como texto."""
    assert _escapar("2026 &mdash; Present") == "2026 &mdash; Present"


def test_un_logro_con_html_no_rompe_el_cv(cv: ArchivosDelCV) -> None:
    _agregar_experiencia(
        cv,
        ExperienciaArgs(
            puesto_en="X",
            puesto_es="X",
            empresa="Acme",
            periodo="2026",
            logros_en=["<script>alert(1)</script> y R&D"],
            logros_es=["<script>alert(1)</script> y R&D"],
        ),
    )
    html = cv.html["en"].read_text(encoding="utf-8")
    assert "<script>" not in html, "se inyectó una etiqueta en el CV"
    assert "&lt;script&gt;" in html


# --- Reemplazar ---


def test_reemplazar_deja_respaldo(cv: ArchivosDelCV) -> None:
    """Es la herramienta más peligrosa: el modelo elige qué cambiar."""
    _reemplazar(
        cv, ReemplazarArgs(viejo="Un agente.", nuevo_en="An agent.", nuevo_es="Un agente nuevo.")
    )
    assert (cv.carpeta / "build" / "cv-en.html.bak").is_file()


def test_no_se_reemplaza_markup(cv: ArchivosDelCV) -> None:
    """Reemplazar etiquetas es cómo se rompe el diseño; acá se cambia texto."""
    resultado = _reemplazar(cv, ReemplazarArgs(viejo='<div class="proj">', nuevo_en="x"))
    assert resultado.ok is False
    assert "markup" in resultado.content


def test_un_texto_ambiguo_no_se_reemplaza(cv: ArchivosDelCV) -> None:
    """Si aparece dos veces no se sabe cuál se quiso cambiar."""
    resultado = _reemplazar(cv, ReemplazarArgs(viejo="2026", nuevo_en="2027"))
    assert resultado.ok is False
    assert "veces" in resultado.content


def test_si_falta_en_un_idioma_no_se_toca_ninguno(cv: ArchivosDelCV) -> None:
    """Escribir en una y fallar en la otra deja el CV a medio cambiar, que es
    peor que no haber empezado."""
    cv.html["es"].write_text("<html>otra cosa</html>", encoding="utf-8")
    antes = cv.html["en"].read_text(encoding="utf-8")
    resultado = _reemplazar(cv, ReemplazarArgs(viejo="Un agente.", nuevo_en="An agent."))
    assert resultado.ok is False
    assert cv.html["en"].read_text(encoding="utf-8") == antes, "tocó el inglés igual"


def test_las_seis_herramientas_se_arman(tmp_path) -> None:
    nombres = {h.name for h in build_cv_tools(tmp_path, 4000)}
    assert nombres == {
        "ver_cv",
        "agregar_certificacion",
        "agregar_experiencia",
        "agregar_proyecto",
        "reemplazar_en_cv",
        "regenerar_cv",
    }
