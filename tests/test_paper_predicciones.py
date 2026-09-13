"""Las predicciones probabilísticas: qué se rompería sin estas pruebas.

No miden si el modelo adivina el precio —eso ya está respondido y la respuesta
es que no—. Miden si sabe cuándo no sabe. Ver `paper/CRITERIO_PREDICCIONES.md`,
que se escribió antes que el código y en su propio commit.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from paper.registro import Contexto, Registro


@pytest.fixture
def registro(tmp_path: Path) -> Registro:
    return Registro(tmp_path / "p.db")


@pytest.fixture
def contexto() -> Contexto:
    return Contexto(
        precio=100.0,
        timestamp="2026-09-13T19:00:00Z",
        dia_semana=6,
        hora_utc=19,
        extra={"indicadores": {"regime": {"regimen": "RANGE"}}},
    )


def _vela(cuando: datetime, *, high: float, low: float, close: float = 0.0) -> dict:
    return {"time": int(cuando.timestamp()), "high": high, "low": low, "close": close or high}


def test_la_probabilidad_entra_en_el_sello(registro: Registro, contexto: Contexto) -> None:
    """Bajar un 80% a un 55% tras fallar convierte un Brier de 0.64 en 0.30.

    Es el mismo fraude que `_sellar` impide con el stop, aplicado a la única
    cifra que aquí decide si el modelo sabe algo. Sin la probabilidad dentro del
    hash, editarla no dejaría rastro.
    """
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=contexto,
        nivel=105.0,
        hacia="arriba",
        probabilidad=0.8,
        razonamiento="pool de liquidez en 105",
    )
    sello_antes = registro.predicciones_vivas()[0]["sello"]

    registro._con.execute("UPDATE predicciones SET probabilidad=0.55 WHERE id=1")
    registro._con.commit()

    from paper.registro import _sellar_prediccion

    fila = registro.predicciones_vivas()[0]
    assert fila["sello"] == sello_antes  # el sello guardado no cambió
    recalculado = _sellar_prediccion(
        contexto,
        fila["razonamiento"],
        simbolo=fila["simbolo"],
        nivel=fila["nivel"],
        hacia=fila["hacia"],
        probabilidad=fila["probabilidad"],
    )
    assert recalculado != sello_antes, "editar la probabilidad tiene que romper el sello"


def test_un_nivel_del_lado_equivocado_no_se_registra(
    registro: Registro, contexto: Contexto
) -> None:
    """Un «arriba» por debajo del precio ya ocurrió al registrarse.

    Sería un acierto garantizado que infla la muestra sin decir nada del modelo:
    diez predicciones así darían un Brier excelente y cero información.
    """
    with pytest.raises(ValueError, match="ya ocurrió"):
        registro.predecir(
            simbolo="BTCUSDT",
            contexto=contexto,
            nivel=95.0,
            hacia="arriba",
            probabilidad=0.9,
            razonamiento="x",
        )
    with pytest.raises(ValueError, match="ya ocurrió"):
        registro.predecir(
            simbolo="BTCUSDT",
            contexto=contexto,
            nivel=105.0,
            hacia="abajo",
            probabilidad=0.9,
            razonamiento="x",
        )


def test_las_velas_anteriores_a_la_prediccion_no_la_resuelven(
    registro: Registro, contexto: Contexto
) -> None:
    """`velas()` trae las últimas 200 —unas 50 horas— y casi todas son pasado.

    Sin el filtro, cualquier predicción se resolvería contra precios que
    ocurrieron ANTES de existir: acertar sabiendo el resultado. Es el mismo bug
    que ya cazó el test del vencimiento en `evaluar_ordenes`.
    """
    ahora = datetime.now(UTC)
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=contexto,
        nivel=105.0,
        hacia="arriba",
        probabilidad=0.7,
        razonamiento="x",
        horas_vigencia=24,
    )
    # Una vela ANTERIOR que barre 105 de sobra.
    pasado = [_vela(ahora - timedelta(hours=3), high=999, low=1)]
    assert registro.resolver_predicciones(pasado) == []
    assert len(registro.predicciones_vivas()) == 1


def test_se_mira_el_maximo_no_el_cierre(registro: Registro, contexto: Contexto) -> None:
    """El precio toca un nivel dentro de la vela aunque cierre lejos.

    Mirar solo el cierre perdería las mechas, que en 15 minutos son la mayoría
    de los toques. Es la diferencia entre resolver la apuesta que se hizo y otra
    parecida.
    """
    ahora = datetime.now(UTC)
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=contexto,
        nivel=105.0,
        hacia="arriba",
        probabilidad=0.6,
        razonamiento="x",
    )
    # Máximo 106 pero cierre 99: tocó.
    resueltas = registro.resolver_predicciones(
        [_vela(ahora + timedelta(minutes=5), high=106, low=98, close=99)]
    )
    assert resueltas[0]["ocurrio"] is True
    assert resueltas[0]["brier"] == pytest.approx((0.6 - 1) ** 2)


def test_una_prediccion_viva_que_no_vencio_sigue_abierta(
    registro: Registro, contexto: Contexto
) -> None:
    """Todavía puede ocurrir: cerrarla como fallo antes de tiempo la falsea."""
    ahora = datetime.now(UTC)
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=contexto,
        nivel=105.0,
        hacia="arriba",
        probabilidad=0.5,
        razonamiento="x",
        horas_vigencia=48,
    )
    assert registro.resolver_predicciones([_vela(ahora, high=101, low=99)]) == []
    assert len(registro.predicciones_vivas()) == 1


def test_el_regimen_lo_pone_el_codigo_no_el_modelo(
    registro: Registro, contexto: Contexto
) -> None:
    """Se guardan los dos por separado porque la pregunta es si COINCIDEN.

    Si el modelo pudiera escribir `regimen_medido`, la comparación mediría su
    memoria y no su lectura del gráfico.
    """
    registro.predecir(
        simbolo="BTCUSDT",
        contexto=contexto,
        nivel=105.0,
        hacia="arriba",
        probabilidad=0.6,
        razonamiento="x",
        regimen_dicho="TREND",
    )
    fila = registro.predicciones_vivas()[0]
    assert fila["regimen_medido"] == "RANGE"  # del contexto, calculado
    assert fila["regimen_dicho"] == "TREND"  # lo que dijo el modelo


def test_los_tramos_callan_por_debajo_del_minimo(
    registro: Registro, contexto: Contexto
) -> None:
    """Enseñarle su tasa con 20 predicciones es invitarlo a ajustar contra ruido.

    La fiabilidad del Brier es inestable con muestra pequeña y todos los modelos
    medidos muestran sobreconfianza sistemática. Es el mismo principio que «no
    elijas el mejor eje mirando esta tabla».
    """
    ahora = datetime.now(UTC)
    for i in range(3):
        registro.predecir(
            simbolo="BTCUSDT",
            contexto=contexto,
            nivel=105.0 + i,
            hacia="arriba",
            probabilidad=0.7,
            razonamiento=f"x{i}",
        )
    registro.resolver_predicciones([_vela(ahora + timedelta(minutes=5), high=200, low=99)])

    resumen = registro.brier_por_tramo()
    assert resumen["resueltas"] == 3
    assert resumen["faltan"] == 47
    assert resumen["tramos"] == [], "sin muestra no se enseña ningún tramo"


def test_con_muestra_los_tramos_salen_ordenados_por_probabilidad(
    registro: Registro, contexto: Contexto
) -> None:
    """Nunca por resultado: ordenar por acierto invita a quedarse con el mejor.

    Es exactamente el sobreajuste que `por_eje()` evita con el mismo cuidado.
    """
    ahora = datetime.now(UTC)
    for i in range(50):
        registro.predecir(
            simbolo="BTCUSDT",
            contexto=contexto,
            nivel=105.0 + i,
            hacia="arriba",
            probabilidad=0.9 if i % 2 else 0.3,
            razonamiento=f"x{i}",
        )
    registro.resolver_predicciones([_vela(ahora + timedelta(minutes=5), high=999, low=99)])

    resumen = registro.brier_por_tramo()
    assert resumen["resueltas"] == 50
    nombres = [t["tramo"] for t in resumen["tramos"]]
    assert nombres == sorted(nombres)
    assert resumen["tasa_base"] == 1.0  # todas tocaron: la vela barre todo


def test_dos_niveles_a_menos_de_un_atro_y_medio_son_la_misma_apuesta(
    registro: Registro,
) -> None:
    """Pasó de verdad: el modelo predijo 76.500 y 76.400 con un ATR de ~87.

    Si el precio baja a barrer ese pool toca los dos, así que sus resultados
    están correlacionados. Para la calibración eso es veneno: dos aciertos que
    en realidad son uno inflan la muestra sin aportar información, y con 50
    predicciones así la muestra efectiva sería una fracción.

    ⚠ EL MÍNIMO ES 1.5 ATR Y NO 1. Con 1 ATR estricto este caso NO se bloqueaba
    —100 > 87— así que el arreglo no arreglaba nada. Un ATR es lo que recorre
    una vela; hacen falta más de una para que los dos niveles sean separables.
    """
    ctx = Contexto(
        precio=77300.0,
        timestamp="2026-09-13T20:24:00Z",
        dia_semana=6,
        hora_utc=20,
        extra={"indicadores": {"atr": 87.0}},
    )
    registro.predecir(
        simbolo="BTCUSDT", contexto=ctx, nivel=76500.0, hacia="abajo",
        probabilidad=0.6, razonamiento="pool EQL", temporalidad="1h",
    )
    with pytest.raises(ValueError, match="misma"):
        registro.predecir(
            simbolo="BTCUSDT", contexto=ctx, nivel=76400.0, hacia="abajo",
            probabilidad=0.4, razonamiento="y", temporalidad="15m",
        )
    # Lejos sí entra, y hacia el otro lado también: no es un veto general.
    registro.predecir(
        simbolo="BTCUSDT", contexto=ctx, nivel=76300.0, hacia="abajo",
        probabilidad=0.3, razonamiento="z", temporalidad="4h",
    )
    registro.predecir(
        simbolo="BTCUSDT", contexto=ctx, nivel=77500.0, hacia="arriba",
        probabilidad=0.5, razonamiento="techo", temporalidad="15m",
    )
    assert len(registro.predicciones_vivas()) == 3


def test_un_nivel_pegado_al_precio_lo_toca_el_ruido(registro: Registro) -> None:
    """A menos de 1.5 ATR, el precio lo toca por una vela normal.

    Sería un acierto que no dice nada del modelo: mide la volatilidad, no su
    lectura del gráfico.
    """
    ctx = Contexto(
        precio=77300.0, timestamp="x", dia_semana=6, hora_utc=20,
        extra={"indicadores": {"atr": 100.0}},
    )
    with pytest.raises(ValueError, match="ruido"):
        registro.predecir(
            simbolo="BTCUSDT", contexto=ctx, nivel=77250.0, hacia="abajo",
            probabilidad=0.6, razonamiento="x", temporalidad="15m",
        )


def test_la_temporalidad_se_guarda_y_entra_en_el_sello(registro: Registro) -> None:
    """Un 60% en 15m y un 60% en 4h no son la misma afirmación.

    El primero es scalping y el segundo una tesis de medio día. Sin la
    temporalidad, agrupar las predicciones mediría el promedio de dos cosas
    distintas; y sin ella en el sello, cambiarla después reescribiría qué se
    dijo sin dejar rastro.
    """
    from paper.registro import _sellar_prediccion

    ctx = Contexto(
        precio=77300.0, timestamp="x", dia_semana=6, hora_utc=20,
        extra={"indicadores": {"atr": 87.0}},
    )
    registro.predecir(
        simbolo="BTCUSDT", contexto=ctx, nivel=76500.0, hacia="abajo",
        probabilidad=0.6, razonamiento="pool EQL", temporalidad="4h",
    )
    fila = registro.predicciones_vivas()[0]
    assert fila["temporalidad"] == "4h"

    # El mismo sello con otra temporalidad tiene que dar distinto.
    con_otra = _sellar_prediccion(
        ctx, fila["razonamiento"], simbolo=fila["simbolo"], nivel=fila["nivel"],
        hacia=fila["hacia"], probabilidad=fila["probabilidad"], temporalidad="15m",
    )
    assert con_otra != fila["sello"], "cambiar la temporalidad tiene que romper el sello"


def test_el_plazo_sale_de_la_temporalidad(registro: Registro, contexto: Contexto) -> None:
    """24h fijas trataban igual a un scalp de 15m y a una tesis de 4h.

    El primero se quedaba esperando 23 horas después de que su premisa hubiera
    caducado —el gráfico de 15m de hace un día es otro gráfico— y el segundo se
    cortaba antes de que su movimiento tuviera tiempo de ocurrir. Los dos casos
    ensucian el Brier con ruido que no tiene que ver con la lectura del modelo.
    """
    ctx = Contexto(
        precio=77300.0, timestamp="x", dia_semana=6, hora_utc=20,
        extra={"indicadores": {"atr": 87.0}},
    )
    esperado = {"15m": 6.0, "1h": 24.0, "4h": 96.0, "": 24.0}
    niveles = {"15m": 76000.0, "1h": 75000.0, "4h": 74000.0, "": 73000.0}
    for marco, horas in esperado.items():
        pid = registro.predecir(
            simbolo="BTCUSDT", contexto=ctx, nivel=niveles[marco], hacia="abajo",
            probabilidad=0.5, razonamiento=f"m{marco}", temporalidad=marco,
        )
        fila = next(p for p in registro.predicciones_vivas() if p["id"] == pid)
        vivo = datetime.fromisoformat(fila["vence_en"]) - datetime.fromisoformat(
            fila["hecha_en"]
        )
        assert vivo.total_seconds() / 3600 == pytest.approx(horas, abs=0.01), marco


def test_un_plazo_explicito_pisa_al_derivado(registro: Registro) -> None:
    """Una tesis puede pedir otro plazo; lo que no puede es no tener ninguno."""
    ctx = Contexto(
        precio=77300.0, timestamp="x", dia_semana=6, hora_utc=20,
        extra={"indicadores": {"atr": 87.0}},
    )
    pid = registro.predecir(
        simbolo="BTCUSDT", contexto=ctx, nivel=76000.0, hacia="abajo",
        probabilidad=0.4, razonamiento="tesis larga", temporalidad="15m",
        horas_vigencia=48.0,
    )
    fila = next(p for p in registro.predicciones_vivas() if p["id"] == pid)
    vivo = datetime.fromisoformat(fila["vence_en"]) - datetime.fromisoformat(fila["hecha_en"])
    assert vivo.total_seconds() / 3600 == pytest.approx(48.0, abs=0.01)


def test_el_desglose_por_marco_no_sustituye_al_global(registro: Registro) -> None:
    """La resolución se mide sobre TODAS las predicciones, no marco por marco.

    Repartir 50 en tres marcos deja ~17 por celda, y con esa muestra el mejor
    por azar parece bueno. El desglose sirve para ver si un marco arrastra a los
    otros, no para elegir en cuál predecir — eso sería el sobreajuste que
    `por_eje` evita con el mismo cuidado.
    """
    ahora = datetime.now(UTC)
    ctx = Contexto(
        precio=77300.0, timestamp="x", dia_semana=6, hora_utc=20,
        extra={"indicadores": {"atr": 10.0}},
    )
    for i in range(50):
        registro.predecir(
            simbolo="BTCUSDT", contexto=ctx, nivel=77300.0 + 100 * (i + 1), hacia="arriba",
            probabilidad=0.5, razonamiento=f"x{i}", temporalidad=["15m", "1h", "4h"][i % 3],
        )
    registro.resolver_predicciones([_vela(ahora + timedelta(minutes=5), high=999999, low=1)])

    resumen = registro.brier_por_tramo()
    assert resumen["resueltas"] == 50
    marcos = [m["temporalidad"] for m in resumen["porMarco"]]
    assert marcos == sorted(marcos), "alfabético, nunca por resultado"
    assert set(marcos) == {"15m", "1h", "4h"}
    # El global sigue calculándose sobre las 50, no sobre el mejor marco.
    assert sum(m["n"] for m in resumen["porMarco"]) == resumen["resueltas"]
