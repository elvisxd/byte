"""En qué sesión del mercado estamos, como HECHO para el modelo y como EJE para medir.

═══ POR QUÉ EXISTE ═══

La pregunta era «¿cuántas vueltas por sesión —Asia, Londres, Nueva York—
necesita el modelo?». La respuesta del vigía es que no se cuenta por sesión
sino por evento, y que en hora de Nueva York los cierres de 4h caen justo en
las aperturas: 04:00 Londres, 08:00 Nueva York, 20:00 Asia. Lo que faltaba no
eran vueltas, era que el modelo SUPIERA en qué sesión lee —un hecho más del
mapa, sin veredicto— y que las predicciones se pudieran separar por la sesión
en que se hicieron: en dos semanas, los datos dirán si una lectura de Londres
vale lo mismo que una de Nueva York. Si la de las 04:00 resulta valiosa, se
abre la ventana para ese cierre con un número delante.

⚠ SOLO HECHOS. Acá no va «Asia es tranquila» ni «Londres marca el rango»: eso
sería el script leyendo por el modelo. Va el nombre, las horas, y cuánto falta
para el cierre de 4h.

Las sesiones se definen en UTC —el reloj del exchange y de las velas— y no en
la hora de la máquina, para que el hecho sea el mismo en la Mac y en el
Codespace.
"""

from datetime import UTC, datetime

# (nombre, desde, hasta) en minutos UTC desde medianoche. Tokio 09-17 JST;
# Londres 08-16:30 UTC; Nueva York 09:30-17 ET = 13:30-21 UTC. Entre las 21 y
# la medianoche no hay bolsa grande abierta (Sídney abre a las 22).
SESIONES: tuple[tuple[str, int, int], ...] = (
    ("Asia", 0, 8 * 60),
    ("Londres", 8 * 60, 13 * 60 + 30),
    ("Londres y Nueva York", 13 * 60 + 30, 16 * 60 + 30),
    ("Nueva York", 16 * 60 + 30, 21 * 60),
    ("entre Nueva York y Asia", 21 * 60, 24 * 60),
)


def sesion_de(momento: datetime) -> str:
    """El nombre de la sesión en la que cae `momento` (en cualquier zona; se pasa a UTC)."""
    utc = momento.astimezone(UTC)
    minuto = utc.hour * 60 + utc.minute
    for nombre, desde, hasta in SESIONES:
        if desde <= minuto < hasta:
            return nombre
    return SESIONES[-1][0]


def minutos_al_cierre_4h(momento: datetime) -> int:
    """Cuántos minutos faltan para que cierre la vela de 4h en curso (rejilla UTC)."""
    utc = momento.astimezone(UTC)
    minuto = utc.hour * 60 + utc.minute
    return 240 - (minuto % 240)


def describir(momento: datetime) -> str:
    """La línea que va al mapa: sesión, sus horas UTC, el cierre de 4h y el fin de semana."""
    utc = momento.astimezone(UTC)
    nombre = sesion_de(utc)
    desde, hasta = next((d, h) for n, d, h in SESIONES if n == nombre)
    faltan = minutos_al_cierre_4h(utc)
    cierre = f"{faltan // 60} h {faltan % 60:02d}" if faltan >= 60 else f"{faltan} min"
    horas = f"{desde // 60:02d}:{desde % 60:02d}–{hasta // 60:02d}:{hasta % 60:02d} UTC"
    linea = f"sesión: {nombre} ({horas}) · la vela de 4h cierra en {cierre}"
    if utc.weekday() >= 5:
        linea += " · fin de semana"
    return linea
