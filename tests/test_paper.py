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
        registro.cerrar(oid, precio_salida=salida, motivo="x")

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
