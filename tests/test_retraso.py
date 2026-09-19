"""Cuánto tardás en postular, medido contra el disco y el buzón.

Lo que se cuida acá es que el número no mienta: un emparejado flojo que ate
cualquier acuse a cualquier oferta daría una mediana bonita y falsa, y sobre
ese número se decide si la frescura vale 25 puntos o vale cinco.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from empleo.postulaciones import Respuesta
from empleo.retraso import Aviso, emparejar, informe, leer_avisos


def _digest(carpeta: Path, nombre: str, cuerpo: str) -> None:
    (carpeta / nombre).write_text(cuerpo, encoding="utf-8")


_UN_DIGEST = """# Ofertas — 2026-09-15T09:07

fuentes → linkedin: 4 · remoteok: 12

## 52 · Senior AI Engineer — Cohere
- https://www.linkedin.com/jobs/view/111
- fuente: linkedin · Toronto, ON
- por qué: +24 stack: llm, rag; +25 hasta_24h (6 h)
- alerta: ai engineer canada in Canada

## 31 · Staff Software Engineer - Platform — Jobber
- https://jobs.ashbyhq.com/jobber/222
- fuente: empresas · Edmonton, AB
- por qué: +6 stack: typescript; +25 hasta_24h (3 h)
"""


def test_el_titulo_con_guiones_no_se_lleva_puesta_la_empresa(tmp_path: Path) -> None:
    """La empresa va después del último " — " y los títulos traen guiones
    propios ("Staff Software Engineer - Platform"). Cortando por el primero, la
    empresa de media lista quedaría siendo un pedazo del puesto, y el
    emparejado por empresa —que es todo lo que hay— no ataría nada."""
    _digest(tmp_path, "2026-09-15-0907.md", _UN_DIGEST)

    avisos = leer_avisos(tmp_path)

    por_empresa = {a.empresa: a for a in avisos}
    assert set(por_empresa) == {"Cohere", "Jobber"}
    assert por_empresa["Jobber"].titulo == "Staff Software Engineer - Platform"
    assert por_empresa["Cohere"].puntaje == 52


def test_la_fecha_sale_del_nombre_del_archivo(tmp_path: Path) -> None:
    """Es lo único que dice cuándo te avisaron. Sin eso no hay resta."""
    _digest(tmp_path, "2026-09-15-0907.md", _UN_DIGEST)

    aviso = leer_avisos(tmp_path)[0]

    assert (aviso.cuando.year, aviso.cuando.month, aviso.cuando.day) == (2026, 9, 15)
    assert (aviso.cuando.hour, aviso.cuando.minute) == (9, 7)


def test_un_archivo_que_no_es_un_digest_no_entra(tmp_path: Path) -> None:
    """En la carpeta de trabajo viven también `ultimo_aviso.txt` y notas
    sueltas. Un `.md` con otro nombre no tiene fecha de vuelta y no se puede
    fechar: se ignora en vez de inventarle una."""
    _digest(tmp_path, "LEEME.md", "# apuntes\n\n## 99 · Lo que sea — Acme\n- https://x/1\n")
    _digest(tmp_path, "2026-09-15-0907.md", _UN_DIGEST)

    assert [a.empresa for a in leer_avisos(tmp_path)] == ["Cohere", "Jobber"]


def _respuesta(remitente: str, cuando: datetime, estado: str = "acuse") -> Respuesta:
    return Respuesta(
        id_mensaje=f"<{remitente}-{cuando.isoformat()}>",
        remitente=remitente,
        asunto="Thanks for applying",
        fecha=cuando.strftime("%a, %d %b %Y %H:%M:%S %z"),
        estado=estado,
    )


def _aviso(empresa: str, horas_atras: float, puntaje: int = 40) -> Aviso:
    return Aviso(
        cuando=datetime.now(tz=UTC) - timedelta(hours=horas_atras),
        puntaje=puntaje,
        titulo="Senior Engineer",
        empresa=empresa,
        url=f"https://ejemplo/{empresa}",
    )


def test_el_acuse_se_ata_a_la_oferta_de_esa_empresa() -> None:
    """El correo de respuesta nunca trae el link de la oferta, así que el
    emparejado es por empresa: el dominio del remitente contra el nombre del
    digest."""
    avisos = [_aviso("Cohere", 30), _aviso("Wealthsimple", 20)]
    respuesta = _respuesta("no-reply@cohere.com", datetime.now(tz=UTC) - timedelta(hours=8))

    atados, por_ats, sueltos = emparejar(avisos, [respuesta])

    assert len(atados) == 1
    aviso, _, horas = atados[0]
    assert aviso.empresa == "Cohere"
    assert 21 < horas < 23
    assert not por_ats and not sueltos


def test_un_acuse_anterior_al_aviso_no_es_de_ese_aviso() -> None:
    """Postulaste a lo que te avisaron, no a lo que te avisarían después. Sin
    el corte, un acuse de la semana pasada se ataría al aviso de hoy y daría
    un retraso NEGATIVO que la mediana taparía."""
    avisos = [_aviso("Cohere", 2)]
    vieja = _respuesta("jobs@cohere.com", datetime.now(tz=UTC) - timedelta(days=5))

    atados, _, sueltos = emparejar(avisos, [vieja])

    assert not atados
    assert len(sueltos) == 1


def test_lo_que_llega_de_un_ats_se_cuenta_aparte() -> None:
    """`no-reply@ashbyhq.com` no nombra a la empresa en el remitente. Contarlo
    como fallo del emparejado haría parecer que esto acierta mucho menos de lo
    que acierta, y la conclusión se sacaría de un número inflado."""
    avisos = [_aviso("Cohere", 30)]
    ats = _respuesta("no-reply@ashbyhq.com", datetime.now(tz=UTC) - timedelta(hours=8))

    atados, por_ats, sueltos = emparejar(avisos, [ats])

    assert not atados and not sueltos
    assert len(por_ats) == 1


def test_un_rechazo_no_prueba_cuando_postulaste() -> None:
    """También prueba que postulaste, pero llega días o semanas después: medir
    con eso no daría tu retraso, daría el de la empresa en contestar."""
    avisos = [_aviso("Cohere", 30)]
    rechazo = _respuesta("jobs@cohere.com", datetime.now(tz=UTC), estado="rechazo")

    atados, por_ats, sueltos = emparejar(avisos, [rechazo])

    assert not atados and not por_ats and not sueltos


def test_el_informe_lista_las_que_siguen_a_tiempo() -> None:
    """El hallazgo que esto persigue no es la mediana sino la lista: lo que te
    avisamos, pasa el corte, y nadie postuló todavía."""
    avisos = [_aviso("Cohere", 10, puntaje=52), _aviso("Vieja", 500, puntaje=48)]

    texto = informe(avisos, [], puntaje_minimo=25, horas_a_tiempo=96)

    assert "sin rastro de postulación: 2" in texto
    assert "todavía a tiempo" in texto
    assert "Cohere" in texto
    assert "Vieja" not in texto


def test_sin_digests_lo_dice_en_vez_de_inventar_una_mediana(tmp_path: Path) -> None:
    """Una mediana sobre cero datos es un número que se lee igual que uno real."""
    texto = informe(leer_avisos(tmp_path), [], puntaje_minimo=25, horas_a_tiempo=96)

    assert "no hay con qué medir" in texto


_SIN_EMPRESA = """# Ofertas — 2026-09-15T09:07

## 84 · Bilingual (Spanish/English) Full-Stack Developer
- https://www.getonbrd.com/jobs/bilingual-full-stack
- fuente: getonbrd · Remote
- por qué: +30 latam; +25 hasta_24h (6 h)
"""


def test_una_oferta_sin_empresa_no_se_queda_con_el_titulo_de_empresa(tmp_path: Path) -> None:
    """`escribir_digest` omite el " — " cuando el feed no manda la empresa, y
    Get on Board no la manda nunca. Cortando con `rpartition`, el título
    terminaba TAMBIÉN en la empresa y el informe mostraba "X — X".

    No es sólo feo: el emparejado busca el dominio del remitente adentro del
    nombre de la empresa, y con el título entero ahí adentro ataría acuses a
    ofertas que no son. Salió corriéndolo de verdad, no razonándolo.
    """
    (tmp_path / "2026-09-15-0907.md").write_text(_SIN_EMPRESA, encoding="utf-8")

    aviso = leer_avisos(tmp_path)[0]

    assert aviso.titulo == "Bilingual (Spanish/English) Full-Stack Developer"
    assert aviso.empresa == ""


def test_una_empresa_vacia_no_ata_ningun_acuse() -> None:
    """El otro lado del mismo caso: sin empresa no hay con qué emparejar, y
    atar por vacío ataría cualquier cosa con cualquier cosa."""
    avisos = [
        Aviso(datetime.now(tz=UTC) - timedelta(hours=30), 84, "Full-Stack", "", "https://x/1")
    ]
    respuesta = _respuesta("no-reply@tabiya.com", datetime.now(tz=UTC) - timedelta(hours=8))

    atados, _, sueltos = emparejar(avisos, [respuesta])

    assert not atados
    assert len(sueltos) == 1


def test_sin_credenciales_el_informe_no_dice_que_no_postulaste() -> None:
    """Sin buzón, TODAS las ofertas figuran sin postular. Ese número se lee
    como "ignoraste 121 ofertas" cuando en realidad es "no miramos", y un
    informe que no distingue las dos cosas es el mismo fallo silencioso que
    este comando existe para destapar."""
    avisos = [_aviso("Cohere", 10, puntaje=52)]

    texto = informe(avisos, [], 25, 96, sin_buzon="faltan GMAIL_USUARIO / GMAIL_APP_PASSWORD")

    assert "NO SE LEYÓ EL BUZÓN" in texto
    assert "sin comprobar contra el buzón: 1" in texto
    assert "sin rastro de postulación" not in texto


def test_con_buzon_vacio_lo_dice_distinto_que_con_buzon_ilegible() -> None:
    """Un buzón que se leyó y no trajo nada no es un buzón que no se pudo leer:
    el primero es un dato, el segundo es una tuerca floja."""
    avisos = [_aviso("Cohere", 10, puntaje=52)]

    texto = informe(avisos, [], 25, 96)

    assert "no trajo ninguna respuesta" in texto
    assert "sin rastro de postulación: 1" in texto
