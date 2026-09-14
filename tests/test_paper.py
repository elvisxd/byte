"""El registro de operaciones en papel.

Lo que más se prueba es el **sello**: que una razón reescrita después de conocer
el resultado sea detectable. Sin eso, todo el ejercicio es un backtest con prosa
encima — que es justamente lo que el criterio de aborto viene a evitar.
"""

import sqlite3
from pathlib import Path

import pytest

from paper.registro import Contexto, Registro


@pytest.fixture
def registro(tmp_path: Path) -> Registro:
    return Registro(tmp_path / "ops.db")


@pytest.fixture
def contexto() -> Contexto:
    return Contexto(
        precio=100.0,
        timestamp="2026-09-13T10:00:00Z",
        dia_semana=0,
        hora_utc=10,
        ancho_rango_pct=0.8,
        velas_en_rango=14,
        regimen_btc="ascending",
    )


def test_el_r_multiplo_lo_calcula_el_codigo(registro: Registro, contexto: Contexto) -> None:
    """Es la única cifra que decide si un eje sirve. Ya está medido que un
    modelo de 8B da 1.43 donde el valor real es 17.35 —eligió desvío poblacional
    en vez de muestral—, así que dejarla en sus manos sería construir todo el
    registro sobre un número que puede estar mal."""
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="AAVEUSDT",
        direccion="long",
        contexto=contexto,
        razon="Barrido del piso con cierre de vuelta adentro.",
        stop_loss=99.0,
    )
    # entrada 100, stop 99 → riesgo 1. Salida 104 → 4R.
    assert registro.cerrar(oid, precio_salida=104.0, motivo="objetivo") == pytest.approx(4.0)


def test_el_r_de_un_short_va_al_reves(registro: Registro, contexto: Contexto) -> None:
    """Un short gana cuando el precio baja: confundir el signo invertiría el
    resultado de la mitad de las operaciones sin que nada lo delate."""
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="AAVEUSDT",
        direccion="short",
        contexto=contexto,
        razon="Barrido del techo.",
        stop_loss=101.0,
    )
    assert registro.cerrar(oid, precio_salida=96.0, motivo="objetivo") == pytest.approx(4.0)


def test_una_razon_reescrita_despues_se_detecta(registro: Registro, contexto: Contexto) -> None:
    """La tentación real no es inventar una operación: es ajustar la razón
    cuando ya se sabe cómo salió. El sello lo hace imposible de disimular."""
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="AAVEUSDT",
        direccion="long",
        contexto=contexto,
        razon="Barrido del piso.",
        stop_loss=99.0,
    )
    assert registro.verificar_sellos() == []

    ruta = registro.ruta
    registro.cerrar_conexion()
    con = sqlite3.connect(ruta)
    con.execute("UPDATE operaciones SET razon='Sabía que iba a subir' WHERE id=?", (oid,))
    con.commit()
    con.close()

    assert Registro(ruta).verificar_sellos() == [oid], "una razón reescrita pasó desapercibida"


def test_un_contexto_retocado_tambien(registro: Registro, contexto: Contexto) -> None:
    """Cambiar el precio de entrada cambiaría el R de toda la operación."""
    oid = registro.abrir(
        eje="x", simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
    )
    ruta = registro.ruta
    registro.cerrar_conexion()
    con = sqlite3.connect(ruta)
    con.execute(
        'UPDATE operaciones SET contexto=REPLACE(contexto, "100.0", "95.0") WHERE id=?', (oid,)
    )
    con.commit()
    con.close()
    assert Registro(ruta).verificar_sellos() == [oid]


def test_una_entrada_sin_razon_no_se_registra(registro: Registro, contexto: Contexto) -> None:
    """Sin razón escrita la operación no aporta nada que un backtest no tenga."""
    with pytest.raises(ValueError, match="razón"):
        registro.abrir(
            eje="x", simbolo="X", direccion="long", contexto=contexto, razon="   ", stop_loss=99.0
        )


def test_un_stop_del_lado_equivocado_se_rechaza(registro: Registro, contexto: Contexto) -> None:
    """Un long con el stop arriba del precio no es un tipeo: es señal de que el
    modelo no entendió la operación, y su R contaminaría todo el eje."""
    with pytest.raises(ValueError, match="stop"):
        registro.abrir(
            eje="x", simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=101.0
        )
    with pytest.raises(ValueError, match="stop"):
        registro.abrir(
            eje="x", simbolo="X", direccion="short", contexto=contexto, razon="y", stop_loss=99.0
        )


def test_las_abiertas_sobreviven_a_la_sesion(tmp_path: Path, contexto: Contexto) -> None:
    """Entre una sesión y la siguiente el proceso muere —en un Codespace, cada
    vez— así que esto es lo único que sabe qué quedó a medias."""
    ruta = tmp_path / "ops.db"
    primera = Registro(ruta)
    primera.abrir(
        eje="x", simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
    )
    primera.cerrar_conexion()

    segunda = Registro(ruta)
    abiertas = segunda.abiertas()
    assert len(abiertas) == 1
    assert abiertas[0]["razon"] == "y"


def test_una_operacion_no_se_cierra_dos_veces(registro: Registro, contexto: Contexto) -> None:
    """Cerrarla de nuevo escribiría otro R sobre el mismo riesgo y contaría la
    operación dos veces en el resumen del eje."""
    oid = registro.abrir(
        eje="x", simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
    )
    registro.cerrar(oid, precio_salida=101.0, motivo="objetivo")
    with pytest.raises(ValueError, match="cerrada"):
        registro.cerrar(oid, precio_salida=105.0, motivo="objetivo")


def test_el_resumen_separa_los_ejes(registro: Registro, contexto: Contexto) -> None:
    """Los ejes corren en paralelo y se comparan al final: mezclarlos sería
    perder lo único que el experimento mide."""
    for eje, salida in (("a", 104.0), ("a", 98.0), ("b", 102.0)):
        oid = registro.abrir(
            eje=eje, simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
        )
        registro.cerrar(oid, precio_salida=salida, motivo="manual")

    resumen = {f["eje"]: f for f in registro.por_eje()}
    assert resumen["a"]["cerradas"] == 2
    assert resumen["b"]["cerradas"] == 1
    assert resumen["a"]["r_total"] == pytest.approx(2.0)  # +4 y -2


def test_el_sello_no_puede_estar_vacio(registro: Registro, contexto: Contexto) -> None:
    """Un sello vacío coincide consigo mismo, así que `verificar_sellos` no
    detectaría nada y los otros dos tests pasarían igual. Lo descubrí
    desactivando el sello a propósito: seguían en verde."""
    registro.abrir(
        eje="x", simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
    )
    fila = registro._con.execute("SELECT sello FROM operaciones").fetchone()
    assert len(fila["sello"]) == 32, "el sello tiene que ser un hash, no una cadena vacía"


# --- Las herramientas del agente ---


def test_las_herramientas_se_arman(tmp_path: Path) -> None:
    from tools.paper import build_paper_tools

    nombres = {h.name for h in build_paper_tools(str(tmp_path / "ops.db"), 4000)}
    assert nombres == {
        "mirar_mercado",
        "abrir_operacion",
        "cerrar_operacion",
        "estado_paper",
        "salir_parcial",
        "mover_stop",
        "dejar_orden",
        "cancelar_orden",
        "predecir",
        "publicar_historial",
    }


def test_abrir_exige_la_razon_en_el_esquema(tmp_path: Path) -> None:
    """Que la razón sea un argumento obligatorio es lo que hace imposible
    registrar una entrada sin justificarla. Si fuera opcional, el modelo la
    omitiría en cuanto tuviera prisa."""
    from tools.paper import build_paper_tools

    abrir = {h.name: h for h in build_paper_tools(str(tmp_path / "ops.db"), 4000)}[
        "abrir_operacion"
    ]
    esquema = abrir.schema()["parameters"]
    assert "razon" in esquema["required"], "la razón tiene que ser obligatoria"


def test_el_estado_advierte_de_los_sellos_rotos(tmp_path: Path, contexto: Contexto) -> None:
    """Si una razón se editó, las operaciones de esa tanda no sirven y hay que
    decirlo donde el agente lo va a leer, no en un log que nadie mira."""
    import sqlite3

    from tools.paper import _estado

    ruta = tmp_path / "ops.db"
    registro = Registro(ruta)
    oid = registro.abrir(
        eje="x", simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
    )
    registro.cerrar_conexion()
    con = sqlite3.connect(ruta)
    con.execute("UPDATE operaciones SET razon='otra cosa' WHERE id=?", (oid,))
    con.commit()
    con.close()

    assert "SELLOS ROTOS" in _estado(Registro(ruta)).content


def test_el_estado_no_ordena_los_ejes_por_resultado(tmp_path: Path, contexto: Contexto) -> None:
    """Ordenarlos invita a elegir el mejor mirando la tabla, que es el
    sobreajuste que el criterio de aborto prohíbe."""
    from tools.paper import _estado

    registro = Registro(tmp_path / "ops.db")
    for eje, salida in (("zeta", 104.0), ("alfa", 98.0)):
        oid = registro.abrir(
            eje=eje, simbolo="X", direccion="long", contexto=contexto, razon="y", stop_loss=99.0
        )
        registro.cerrar(oid, precio_salida=salida, motivo="manual")

    contenido = _estado(registro).content
    # Alfabético: alfa (perdedor) antes que zeta (ganador).
    assert contenido.index("alfa") < contenido.index("zeta")
    assert "No elijas el mejor eje mirando esta tabla" in contenido


def test_el_sello_cubre_la_direccion_y_el_stop_no_solo_la_razon(
    registro: Registro, contexto: Contexto
) -> None:
    """Es el fraude MÁS rentable, y el sello no lo veía.

    Editar la razón cambia el relato; editar la dirección o el stop cambia el R
    múltiplo, que es la cifra que decide si un eje sirve. Medido cuando el sello
    hasheaba solo {eje, razon, contexto}: voltear `direccion` sobre un short
    convertía un −4R en un +4R y `verificar_sellos()` seguía diciendo que el
    historial estaba limpio.
    """
    # Las sentencias van literales y no interpoladas: un `f"... SET {campo}"`
    # es seguro acá —los nombres son constantes de este test— pero obliga a
    # silenciar el aviso de inyección, y un `noqa` en un test sobre integridad
    # de datos es la clase de ruido que después nadie relee.
    ediciones = (
        ("UPDATE operaciones SET direccion = ? WHERE id = ?", "long"),
        ("UPDATE operaciones SET stop_loss = ? WHERE id = ?", 99.9),
        ("UPDATE operaciones SET simbolo = ? WHERE id = ?", "OTRO"),
    )
    for sentencia, valor in ediciones:
        oid = registro.abrir(
            eje="anti-smc",
            simbolo="BTCUSDT",
            direccion="short",
            contexto=contexto,
            razon="La señal de libro decía long; opero en contra.",
            stop_loss=101.0,
            take_profit=96.0,
        )
        assert oid not in registro.verificar_sellos()

        registro._con.execute(sentencia, (valor, oid))  # noqa: SLF001
        registro._con.commit()  # noqa: SLF001

        assert oid in registro.verificar_sellos(), f"pasó desapercibido: {sentencia}"


def test_el_sello_cubre_el_take_profit(registro: Registro, contexto: Contexto) -> None:
    """El objetivo es parte de lo que se decidió al entrar: moverlo después
    reescribe la operación aunque no toque el R de un cierre por stop."""
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="AAVEUSDT",
        direccion="long",
        contexto=contexto,
        razon="Barrido del piso.",
        stop_loss=99.0,
        take_profit=104.0,
    )
    registro._con.execute(  # noqa: SLF001
        "UPDATE operaciones SET take_profit = 120.0 WHERE id = ?", (oid,)
    )
    registro._con.commit()  # noqa: SLF001

    assert oid in registro.verificar_sellos()


def test_una_fila_ilegible_se_marca_rota_y_no_tumba_el_registro(
    registro: Registro, contexto: Contexto
) -> None:
    """Perder el historial entero por una fila mala es el peor canje posible.

    `verificar_sellos` hacía `Contexto(**json.loads(...))` a pelo, así que un
    contexto corrupto lanzaba y se llevaba la publicación y `estado_paper` con
    ella. Lo que no se puede verificar hay que marcarlo como no confiable, que
    es distinto de no poder enseñar nada.
    """
    rota = _operacion_simple(registro, contexto)
    sana = _operacion_simple(registro, contexto)
    registro._con.execute(  # noqa: SLF001
        "UPDATE operaciones SET contexto = ? WHERE id = ?", ("{no es json", rota)
    )
    registro._con.commit()  # noqa: SLF001

    rotos = registro.verificar_sellos()

    assert rota in rotos
    assert sana not in rotos


def _operacion_simple(registro: Registro, contexto: Contexto) -> int:
    return registro.abrir(
        eje="range-sweep",
        simbolo="AAVEUSDT",
        direccion="long",
        contexto=contexto,
        razon="Barrido del piso con cierre de vuelta adentro.",
        stop_loss=99.0,
    )


def test_un_stop_pegado_al_precio_no_se_registra(registro: Registro, contexto: Contexto) -> None:
    """No es una operación ajustada, es un error de tipeo.

    El R divide por la distancia al stop: medido, `stop_loss=99.99999999` sobre
    un precio de 100 daba **50.000.031 R** en una sola operación. Eso no desvía
    el promedio del eje, lo destruye, y `por_eje()` alimenta el criterio de
    aborto — así que el eje quedaría 'ganador' para siempre por un decimal.
    """
    with pytest.raises(ValueError, match="pegado al precio"):
        registro.abrir(
            eje="range-sweep",
            simbolo="AAVEUSDT",
            direccion="long",
            contexto=contexto,
            razon="Stop imposible.",
            stop_loss=99.99999999,
        )


def test_el_objetivo_va_del_lado_que_corresponde(registro: Registro, contexto: Contexto) -> None:
    """Un long con el objetivo por debajo de la entrada no es una operación con
    una tesis rara: es un descuido, y se registra como si fuera intencional."""
    with pytest.raises(ValueError, match="objetivo"):
        registro.abrir(
            eje="range-sweep",
            simbolo="AAVEUSDT",
            direccion="long",
            contexto=contexto,
            razon="Objetivo al revés.",
            stop_loss=99.0,
            take_profit=95.0,
        )


def test_cerrar_no_acepta_parcial_como_motivo(registro: Registro, contexto: Contexto) -> None:
    """`cerrar` cierra TODO lo que queda; los parciales tienen su propio método.

    Antes `motivo="parcial"` estaba en el esquema y escribía `cerrada_en` igual
    que un cierre total: la posición desaparecía de `abiertas()`, el resto no se
    registraba nunca y el eje contabilizaba el R del primer tramo como resultado
    completo. Ahora el motivo se rechaza y la vía correcta es `salir_parcial`.
    """
    oid = _operacion_simple(registro, contexto)

    with pytest.raises(ValueError, match="salir_parcial"):
        registro.cerrar(oid, precio_salida=102.0, motivo="parcial")

    assert [o["id"] for o in registro.abiertas()] == [oid]


def test_una_fila_sin_r_no_anula_las_metricas_del_eje(
    registro: Registro, contexto: Contexto
) -> None:
    """AVG y SUM devuelven NULL si un solo valor lo es, así que 50 operaciones
    medidas y una rota daban `r_total: None` para el eje entero. Lo que no se
    pudo medir se excluye; no borra lo que sí."""
    oid = _operacion_simple(registro, contexto)
    registro.cerrar(oid, precio_salida=104.0, motivo="objetivo")
    roto = _operacion_simple(registro, contexto)
    registro._con.execute(  # noqa: SLF001
        "UPDATE operaciones SET cerrada_en = 'x', r_multiplo = NULL WHERE id = ?", (roto,)
    )
    registro._con.commit()  # noqa: SLF001

    resumen = registro.por_eje()[0]

    assert resumen["r_total"] == 4.0
    assert resumen["cerradas"] == 1


def test_un_breakeven_no_cuenta_como_derrota(registro: Registro, contexto: Contexto) -> None:
    """Salir a 0R después de mover el stop es un resultado neutro. Contarlo como
    fallo castiga justamente la gestión que el experimento quiere observar."""
    oid = _operacion_simple(registro, contexto)
    registro.cerrar(oid, precio_salida=contexto.precio, motivo="manual")

    assert registro.por_eje()[0]["aciertos_pct"] == 100.0


def test_el_r_de_una_operacion_con_parciales_pondera_cada_tramo(
    registro: Registro, contexto: Contexto
) -> None:
    """Salir de la mitad a +2R y del resto a 0R es +1R, no 0R.

    Guardar solo la última salida borraría la ganancia ya tomada, que es
    exactamente lo que la gestión por parciales busca conseguir. El sistema
    viejo hacía eso: `parcial` escribía `cerrada_en` y el resto de la posición
    no se registraba nunca.
    """
    oid = _operacion_simple(registro, contexto)  # entrada 100, stop 99 → 1R = 1

    assert registro.salir_parcial(oid, precio_salida=102.0, fraccion=0.5) == pytest.approx(2.0)
    assert [o["id"] for o in registro.abiertas()] == [oid], "el resto sigue abierto"

    r = registro.cerrar(oid, precio_salida=100.0, motivo="stop")

    assert r == pytest.approx(1.0)


def test_no_se_puede_soltar_mas_posicion_de_la_que_queda(
    registro: Registro, contexto: Contexto
) -> None:
    """Las fracciones son de la posición ORIGINAL, así que dos parciales del 60%
    son imposibles. Sin el guard, el R ponderado sumaría 1.2 veces la posición."""
    oid = _operacion_simple(registro, contexto)
    registro.salir_parcial(oid, precio_salida=102.0, fraccion=0.6)

    with pytest.raises(ValueError, match="no queda tanta"):
        registro.salir_parcial(oid, precio_salida=103.0, fraccion=0.6)


def test_una_fraccion_de_cero_o_uno_no_es_un_parcial(
    registro: Registro, contexto: Contexto
) -> None:
    """Soltar el 100% es cerrar, y hacerlo por esta vía dejaría la operación
    abierta con la posición ya vendida — un estado que no existe."""
    oid = _operacion_simple(registro, contexto)

    for fraccion in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="fracción"):
            registro.salir_parcial(oid, precio_salida=102.0, fraccion=fraccion)


def test_mover_el_stop_no_rompe_el_sello(registro: Registro, contexto: Contexto) -> None:
    """Mover el stop a la entrada tras un parcial es gestión normal; editar el
    stop sellado es adulterar el registro. La diferencia tiene que sobrevivir:
    `mover_stop` escribe en `stop_actual` y no toca lo que se selló."""
    oid = _operacion_simple(registro, contexto)

    registro.mover_stop(oid, nuevo_stop=100.0, razon="A la entrada tras el parcial.")

    assert registro.verificar_sellos() == []
    assert registro.abiertas()[0]["stop_actual"] == 100.0


def test_el_r_se_mide_contra_el_stop_original_aunque_se_mueva(
    registro: Registro, contexto: Contexto
) -> None:
    """Si el R se midiera contra el stop movido, toda gestión se vería como una
    mejora del resultado y las operaciones dejarían de ser comparables entre
    sí: el riesgo que se asumió al entrar es el que se asumió."""
    oid = _operacion_simple(registro, contexto)  # stop 99 → riesgo 1
    registro.mover_stop(oid, nuevo_stop=99.5, razon="Reduzco riesgo.")

    r = registro.cerrar(oid, precio_salida=102.0, motivo="objetivo")

    assert r == pytest.approx(2.0), "con el stop movido daría 5.0"


def test_una_base_vieja_se_migra_sin_perder_el_historial(tmp_path: Path) -> None:
    """`CREATE TABLE IF NOT EXISTS` no altera una tabla que ya existe, así que
    sin migración estrenar los parciales obligaría a borrar el registro — lo que
    este módulo existe para conservar."""
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.executescript(
        """CREATE TABLE operaciones (
             id INTEGER PRIMARY KEY AUTOINCREMENT, eje TEXT NOT NULL, simbolo TEXT NOT NULL,
             direccion TEXT NOT NULL, abierta_en TEXT NOT NULL, precio_entrada REAL NOT NULL,
             stop_loss REAL NOT NULL, take_profit REAL, contexto TEXT NOT NULL,
             razon TEXT NOT NULL, sello TEXT NOT NULL, cerrada_en TEXT, precio_salida REAL,
             motivo_cierre TEXT, r_multiplo REAL, contexto_salida TEXT, analisis TEXT);"""
    )
    con.execute(
        "INSERT INTO operaciones (eje,simbolo,direccion,abierta_en,precio_entrada,"
        "stop_loss,contexto,razon,sello) VALUES ('range-sweep','X','long','2026-01-01',"
        "100,99,'{}','la de antes','sello')"
    )
    con.commit()
    con.close()

    registro = Registro(ruta)

    assert registro.abiertas()[0]["razon"] == "la de antes"
    assert "stop_actual" in {
        f["name"]
        for f in registro._con.execute(  # noqa: SLF001
            "PRAGMA table_info(operaciones)"
        )
    }


def test_el_motivo_de_cierre_se_corrige_contra_los_precios(
    registro: Registro, contexto: Contexto
) -> None:
    """Medido en la primera sesión real: el modelo cerró con motivo "stop"
    diciendo «el precio alcanzó el stop de 76.800» cuando la salida fue
    76.954,57 — 154 puntos POR ENCIMA del stop.

    El R salió bien porque lo calcula el código, pero el motivo entraba tal como
    lo escribía el modelo, y es el campo que después segmenta el análisis: "¿los
    stops saltan antes de tiempo?" se responde con esto, y con motivos
    inventados no se responde nada.
    """
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=contexto,  # precio 100
        razon="Barrido del piso.",
        stop_loss=99.0,
        take_profit=104.0,
    )

    registro.cerrar(oid, precio_salida=99.9, motivo="stop")  # no llegó al stop

    fila = dict(
        registro._con.execute(  # noqa: SLF001
            "SELECT motivo_cierre FROM operaciones WHERE id = ?", (oid,)
        ).fetchone()
    )
    assert fila["motivo_cierre"] == "manual"


def test_un_stop_de_verdad_se_registra_como_stop(registro: Registro, contexto: Contexto) -> None:
    """La corrección no puede convertir todo en manual: un cierre que sí tocó el
    nivel tiene que conservar su nombre, o la segmentación se vacía igual."""
    oid = registro.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=contexto,
        razon="Barrido del piso.",
        stop_loss=99.0,
        take_profit=104.0,
    )

    registro.cerrar(oid, precio_salida=98.9, motivo="stop")

    fila = dict(
        registro._con.execute(  # noqa: SLF001
            "SELECT motivo_cierre FROM operaciones WHERE id = ?", (oid,)
        ).fetchone()
    )
    assert fila["motivo_cierre"] == "stop"


def test_el_freno_por_drawdown_aparece_en_el_estado(tmp_path: Path) -> None:
    """Un umbral que nadie mira no frena nada, y `estado_paper` es donde se mira.

    No vigila que el P&L sea negativo —eso NO aborta nada, lo dice el criterio—
    sino que una racha destructiva consuma las 100 operaciones sin producir
    variedad de razones: cien entradas idénticas perdiendo no son cien datos.

    ⚠ EL AVISO INFORMA SIN INSTRUIR. Decirle al modelo «operá menos» o «cambiá
    de eje» le cambiaría el comportamiento a mitad del experimento, que es
    exactamente lo que el criterio prohíbe. Si este test empieza a fallar porque
    alguien añadió un consejo al texto, el fallo es el consejo.
    """
    from paper.registro import Registro
    from tools.paper import _estado

    def registro_con(r_total: float) -> Registro:
        reg = Registro(tmp_path / f"d{r_total}.db")
        for _ in range(3):
            reg._con.execute(  # noqa: SLF001 - se fabrica el estado, no se opera
                "INSERT INTO operaciones (eje, simbolo, direccion, abierta_en,"
                " precio_entrada, stop_loss, contexto, razon, sello, cerrada_en,"
                " r_multiplo) VALUES ('range-sweep','BTCUSDT','long','2026-09-13T10:00:00Z',"
                "100,99,'{}','x','s','2026-09-13T11:00:00Z', ?)",
                (r_total / 3,),
            )
        reg._con.commit()  # noqa: SLF001
        return reg

    assert "FRENO" not in _estado(registro_con(-12.0)).content, "−12R no cruza el umbral"

    aviso = _estado(registro_con(-33.0)).content
    assert "FRENO" in aviso, "−33R tiene que avisar"
    # Informa sin instruir: ni «operá menos», ni «cambiá de eje».
    for consejo in ("operá menos", "cambiá de eje", "tené cuidado", "dejá de"):
        assert consejo not in aviso.lower()


def test_el_modelo_queda_grabado_en_lo_que_se_registra(tmp_path: Path) -> None:
    """Desde que hay dos máquinas, sin esto la muestra es inservible.

    El Codespace corre qwen3.6:27b y la Mac un 14B. Una racha mala no se podría
    atribuir al mercado o al modelo más chico — y ya está medido que el 8B abre
    en todas las vueltas con razones clonadas donde el 27B se abstiene cinco
    veces seguidas.

    ⚠ NO ENTRA EN EL SELLO, y es deliberado: el sello impide reescribir una
    DECISIÓN del agente, y el modelo es un hecho del entorno que fija el código.
    Meterlo ahí marcaría como adulterada toda fila anterior al cambio, que es
    ruido y no fraude.
    """
    from paper.registro import Contexto, Registro

    ruta = tmp_path / "modelo.db"
    ctx = Contexto(
        precio=100.0, timestamp="2026-09-14T00:00:00Z", dia_semana=0, hora_utc=0,
        extra={"indicadores": {"atr": 2.0}},
    )

    # Sin modelo —como las filas de antes del cambio— queda NULL. Rellenarlas
    # con el de ahora sería inventar quién las hizo.
    sin = Registro(ruta)
    sin.abrir(
        eje="range-sweep", simbolo="BTCUSDT", direccion="long", contexto=ctx,
        razon="de antes", stop_loss=99.0,
    )
    assert sin._con.execute("SELECT modelo FROM operaciones").fetchone()[0] is None  # noqa: SLF001

    # Otra máquina, misma base: es el caso que motiva la columna.
    con = Registro(ruta, modelo="qwen3.6:27b")
    con.abrir(
        eje="dip-trap", simbolo="BTCUSDT", direccion="long", contexto=ctx,
        razon="del codespace", stop_loss=99.0,
    )
    con.dejar_orden(
        eje="range-sweep", simbolo="BTCUSDT", direccion="long", contexto=ctx,
        razon="orden", precio_limite=95.0, stop_loss=90.0,
    )
    con.predecir(
        simbolo="BTCUSDT", contexto=ctx, nivel=110.0, hacia="arriba",
        probabilidad=0.6, razonamiento="pred", temporalidad="1h",
    )

    # Las tres tablas, no solo las operaciones: una orden y una predicción
    # también son trabajo de un modelo concreto.
    for tabla in ("operaciones", "ordenes", "predicciones"):
        ultimos = con._con.execute(  # noqa: SLF001
            f"SELECT modelo FROM {tabla} ORDER BY id DESC LIMIT 1"  # noqa: S608
        ).fetchone()[0]
        assert ultimos == "qwen3.6:27b", tabla

    # Y lo que de verdad importa: la mezcla se puede separar después.
    por_modelo = dict(
        con._con.execute(  # noqa: SLF001
            "SELECT COALESCE(modelo,'(sin modelo)'), COUNT(*) FROM operaciones GROUP BY modelo"
        ).fetchall()
    )
    assert por_modelo == {"(sin modelo)": 1, "qwen3.6:27b": 1}
