"""Cuánto tardás en postular, medido contra el disco y el buzón.

Lo que se cuida acá es que el número no mienta: un emparejado flojo que ate
cualquier acuse a cualquier oferta daría una mediana bonita y falsa, y sobre
ese número se decide si la frescura vale 25 puntos o vale cinco.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from empleo.postulaciones import Respuesta
from empleo.retraso import Aviso, emparejar, informe, leer_avisos, medir, resumen


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

    texto = informe(medir(avisos, [], puntaje_minimo=25, horas_a_tiempo=96))

    assert "sin rastro de postulación: 2" in texto
    assert "todavía a tiempo" in texto
    assert "Cohere" in texto
    assert "Vieja" not in texto


def test_sin_digests_lo_dice_en_vez_de_inventar_una_mediana(tmp_path: Path) -> None:
    """Una mediana sobre cero datos es un número que se lee igual que uno real."""
    texto = informe(medir(leer_avisos(tmp_path), [], puntaje_minimo=25, horas_a_tiempo=96))

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

    texto = informe(
        medir(avisos, [], 25, 96, sin_buzon="faltan GMAIL_USUARIO / GMAIL_APP_PASSWORD")
    )

    assert "NO SE LEYÓ EL BUZÓN" in texto
    assert "sin comprobar contra el buzón: 1" in texto
    assert "sin rastro de postulación" not in texto


def test_con_buzon_vacio_lo_dice_distinto_que_con_buzon_ilegible() -> None:
    """Un buzón que se leyó y no trajo nada no es un buzón que no se pudo leer:
    el primero es un dato, el segundo es una tuerca floja."""
    avisos = [_aviso("Cohere", 10, puntaje=52)]

    texto = informe(medir(avisos, [], 25, 96))

    assert "no trajo ninguna respuesta" in texto
    assert "sin rastro de postulación: 1" in texto


def test_el_resumen_entra_en_el_tope_del_panel() -> None:
    """El panel rechaza los textos de más de 1000 caracteres y devuelve 422 con
    el aviso entero: pasarse por uno **no manda nada**. El informe largo se
    pasa apenas hay unas pocas ofertas sin postular, así que al teléfono va el
    resumen, que son números y no la lista."""
    from empleo.aviso import MAX_CARACTERES

    avisos = [_aviso(f"Empresa{n}", 10, puntaje=52) for n in range(40)]
    respuestas = [
        _respuesta(f"jobs@empresa{n}.com", datetime.now(tz=UTC) - timedelta(hours=2))
        for n in range(20)
    ]
    medicion = medir(avisos, respuestas, 25, 96)

    assert len(informe(medicion)) > MAX_CARACTERES
    assert len(resumen(medicion)) <= MAX_CARACTERES


def test_el_resumen_dice_que_no_hubo_buzon_en_vez_de_una_mediana_vacia() -> None:
    """Es el caso que se vio de verdad: sin credenciales, el resumen no puede
    salir diciendo que tardás cero."""
    medicion = medir([_aviso("Cohere", 10, puntaje=52)], [], 25, 96, sin_buzon="faltan claves")

    texto = resumen(medicion)

    assert "sin buzón" in texto
    assert "mediana" not in texto


# --- La revisión del 19/09: ocho cosas que salieron de correr el código ---


def test_un_token_de_dos_letras_no_ata_nada() -> None:
    """EL bug grave. `daniel@aiscaling.ai` daba token "ai" —el TLD, no un
    nombre— y el emparejado era por SUBCADENA, así que "ai" estaba adentro de
    "bairesdev". El acuse de Scale Army se ataba a una oferta de BairesDev e
    inventaba un retraso de 27 horas, que entraba a la mediana como si fuera
    bueno. Con datos reales del buzón, no inventados."""
    baires = _aviso("BairesDev", 30)
    scale = _respuesta("daniel@aiscaling.ai", datetime.now(tz=UTC) - timedelta(hours=3))

    atados, _, sueltos = emparejar([baires], [scale])

    assert not atados
    assert len(sueltos) == 1


def test_el_token_sale_del_nombre_y_no_del_tld() -> None:
    """La raíz del anterior: se descartaba una lista de cinco TLDs a mano, así
    que cualquier dominio fuera de esa lista devolvía el TLD como si fuera el
    nombre de la empresa."""
    from empleo.postulaciones import Respuesta as R

    def empresa(remitente: str) -> str:
        return R("x", remitente, "s", "f", "acuse").empresa

    assert empresa("daniel@aiscaling.ai") == "aiscaling"
    assert empresa("noreply@notify.nodi.global") == "nodi"
    assert empresa("jobs@ada.cx") == "ada"
    assert empresa("x@example.co.uk") == "example"
    # Y los que ya andaban siguen andando.
    assert empresa("no-reply@cohere.com") == "cohere"
    assert empresa("no-reply-acnt@es.relay.walmart.com") == "walmart"


def test_los_nombres_de_varias_palabras_siguen_atando() -> None:
    """El arreglo no puede volverse tan estricto que deje de atar lo legítimo:
    "grafana" nombra a "Grafana Labs" y "generalmotors" a "General Motors"."""
    ahora = datetime.now(tz=UTC)
    atados, _, _ = emparejar(
        [_aviso("Grafana Labs", 30)],
        [_respuesta("no-reply@grafana.com", ahora - timedelta(hours=3))],
    )
    assert len(atados) == 1


def test_workday_pone_la_empresa_en_el_buzon_y_de_ahi_se_saca() -> None:
    """`generalmotors@myworkday.com`: el dominio es del ATS y el nombre está en
    la parte local. Tres de cada veinte acuses reales vienen así, y mirando
    sólo el dominio quedaban todos anónimos."""
    atados, por_ats, _ = emparejar(
        [_aviso("General Motors", 30)],
        [_respuesta("generalmotors@myworkday.com", datetime.now(tz=UTC) - timedelta(hours=3))],
    )

    assert len(atados) == 1
    assert not por_ats


def test_las_variantes_de_un_ats_se_reconocen_igual() -> None:
    """`workablemail`, `greenhouse-mail`, `ashbyhq`: con igualdad exacta contra
    la lista, dos de cada veinte acuses reales se contaban como empresas y
    nunca emparejaban, inflando el cubo de "el emparejado falla"."""
    ahora = datetime.now(tz=UTC) - timedelta(hours=3)
    for remitente in (
        "noreply@workablemail.com",
        "no-reply@us.greenhouse-mail.io",
        "noreply@monstergovt.com",
    ):
        _, por_ats, sueltos = emparejar([_aviso("Cohere", 30)], [_respuesta(remitente, ahora)])
        assert len(por_ats) == 1, remitente
        assert not sueltos, remitente


def test_hay_digests_pero_ninguna_pasa_el_minimo() -> None:
    """No es lo mismo que no haya digests. El informe decía "no hay con qué
    medir" cuando sí había: el mismo fallo silencioso que este comando existe
    para destapar, otra vez."""
    medicion = medir([_aviso("Acme", 5, puntaje=10)], [], puntaje_minimo=25, horas_a_tiempo=96)

    texto = informe(medicion)

    assert "No hay digests" not in texto
    assert "sobre 1 avisadas" in texto


def test_la_linea_de_a_tiempo_dice_respecto_de_que() -> None:
    """Sin el umbral, "a tiempo: 121" no significa nada."""
    texto = informe(medir([_aviso("Cohere", 5, puntaje=52)], [], 25, 168))

    assert "todavía a tiempo (7.0 d o menos)" in texto


def test_de_dos_avisos_de_la_misma_oferta_vale_el_primero(tmp_path: Path) -> None:
    """Si se pierde la memoria y una oferta se avisa dos veces, el retraso se
    mide contra el PRIMER aviso. Al revés salía más corto de lo real: el sesgo
    justo en la dirección que te hace parecer más rápido."""
    for nombre in ("2026-09-15-0907.md", "2026-09-16-0907.md"):
        (tmp_path / nombre).write_text(_UN_DIGEST, encoding="utf-8")

    avisos = leer_avisos(tmp_path)

    assert len(avisos) == 2  # dos ofertas distintas, no cuatro
    assert all(a.cuando.day == 15 for a in avisos)


def test_una_empresa_que_contiene_el_nombre_de_un_ats_no_es_un_ats() -> None:
    """Salió de la SEGUNDA revisión: el arreglo del emparejado reintrodujo el
    mismo error una línea más abajo. `_es_intermediario` buscaba la palabra
    adentro del dominio, y "Leverage Labs" —`leverage-labs.com`— se clasificaba
    como ATS porque "lever" está adentro de "leverage".

    Un falso positivo acá se traga la empresa en silencio; una variante de ATS
    que falte, en cambio, cae en "sueltos" y se ve. Por eso la comparación es
    por etiqueta completa del dominio y la lista incluye las formas pegadas.
    """
    from empleo.retraso import _es_intermediario

    assert not _es_intermediario("leverage-labs")
    assert not _es_intermediario("cohere")
    assert _es_intermediario("lever")
    assert _es_intermediario("ashbyhq")
    assert _es_intermediario("greenhouse-mail")
    assert _es_intermediario("workablemail")


def test_leverage_labs_empareja_como_la_empresa_que_es() -> None:
    """El otro lado del mismo caso, de punta a punta."""
    atados, por_ats, _ = emparejar(
        [_aviso("Leverage Labs", 30)],
        [_respuesta("jobs@leverage-labs.com", datetime.now(tz=UTC) - timedelta(hours=4))],
    )

    assert len(atados) == 1
    assert not por_ats


# Los remitentes reales del buzón del 19/09, con qué empresa los generó. Es la
# tabla que destapó los cuatro defectos del emparejado: el TLD por nombre, la
# subcadena, los ATS con variante y el guion del dominio.
_BUZON_REAL = [
    ("no-reply@grafana.com", "Grafana Labs"),
    ("shoffman@mintmcp.com", "MintMCP"),
    ("generalmotors@myworkday.com", "General Motors"),
    ("associates@amazon.com", "Amazon"),
    ("noreply@notify.nodi.global", "Nodi"),
    ("daniel@connect-clera.com", "Connect Clera"),
    ("notification@captechcareers.com", "CapTech Careers"),
    ("daniel@aiscaling.ai", "AI Scaling"),
]
_ATS_REALES = [
    "noreply@workablemail.com",
    "no-reply@us.greenhouse-mail.io",
    "noreply@applytojob.com",
    "no-reply@ashbyhq.com",
    "no-reply@ats.rippling.com",
    "noreply@monstergovt.com",
    "notifications@smartrecruiters.com",
]


@pytest.mark.parametrize(("remitente", "empresa"), _BUZON_REAL)
def test_cada_remitente_real_ata_a_su_empresa(remitente: str, empresa: str) -> None:
    """Contra el buzón de verdad y no contra remitentes imaginados, que es de
    donde salieron los cuatro defectos. Ocho de ocho, sin un solo cruce."""
    avisos = [_aviso(e, 30) for _, e in _BUZON_REAL]
    respuesta = _respuesta(remitente, datetime.now(tz=UTC) - timedelta(hours=3))

    atados, _, _ = emparejar(avisos, [respuesta])

    assert len(atados) == 1
    assert atados[0][0].empresa == empresa


@pytest.mark.parametrize("remitente", _ATS_REALES)
def test_cada_ats_real_se_cuenta_aparte(remitente: str) -> None:
    """Ninguno puede colarse como empresa ni como fallo del emparejado."""
    _, por_ats, sueltos = emparejar(
        [_aviso(e, 30) for _, e in _BUZON_REAL],
        [_respuesta(remitente, datetime.now(tz=UTC) - timedelta(hours=3))],
    )

    assert len(por_ats) == 1
    assert not sueltos


# --- Segunda revisión: lo que la primera tanda de arreglos dejó pasar ---


@pytest.mark.parametrize(("token", "empresa"), [("ada", "Ada"), ("ibm", "IBM"), ("sap", "SAP")])
def test_una_empresa_de_tres_letras_empareja_igual(token: str, empresa: str) -> None:
    """El largo mínimo existía para frenar `ai` delante de "airbnb", pero de
    paso dejaba afuera a Ada —una de las canadienses que miramos—, IBM y SAP.

    La igualdad exacta no necesita largo: sólo el emparejado POR PREFIJO, que
    es el que empareja con demasiados.
    """
    from empleo.retraso import _coincide

    assert _coincide(token, empresa)


@pytest.mark.parametrize("empresa", ["BairesDev", "Airbnb", "AI Scaling Partners"])
def test_y_el_token_corto_sigue_sin_ser_prefijo_de_nadie(empresa: str) -> None:
    """El arreglo de arriba no puede devolver el bug grave del #143."""
    from empleo.retraso import _coincide

    assert not _coincide("ai", empresa)


@pytest.mark.parametrize("buzon", ["careers", "talent", "noreply", "jobs", "hr"])
def test_un_buzon_generico_de_workday_no_nombra_a_nadie(buzon: str) -> None:
    """Workday pone la empresa en la parte local, pero no siempre:
    `careers@myworkday.com` ataba a una empresa llamada "Careers Inc". El
    nombre propio sirve, el genérico es ruido con largo suficiente."""
    avisos = [_aviso("Careers Inc", 30), _aviso("Talent Partners", 30), _aviso("HR Solutions", 30)]
    respuesta = _respuesta(f"{buzon}@myworkday.com", datetime.now(tz=UTC) - timedelta(hours=2))

    atados, por_ats, _ = emparejar(avisos, [respuesta])

    assert not atados
    assert len(por_ats) == 1


def test_el_nombre_propio_del_buzon_sigue_atando() -> None:
    """El filtro de genéricos no puede llevarse puesto el caso que lo motivó."""
    atados, _, _ = emparejar(
        [_aviso("General Motors", 30)],
        [_respuesta("generalmotors@myworkday.com", datetime.now(tz=UTC) - timedelta(hours=2))],
    )

    assert len(atados) == 1


def test_el_sin_rastro_se_cuenta_sobre_las_que_pasaban_el_corte() -> None:
    """ "De 121 avisadas, 14 sin rastro" mezcla dos universos: esas 14 salen
    sólo de las que pasaban el mínimo. El denominador tiene que ser el mismo."""
    medicion = medir(
        [_aviso("Cohere", 30, puntaje=52), _aviso("Acme", 30, puntaje=10)], [], 25, 168
    )

    texto = informe(medicion)

    assert "De 1 que pasaban el corte (sobre 2 avisadas)" in texto
