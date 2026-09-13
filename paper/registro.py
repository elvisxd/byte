"""El registro de operaciones en papel: contexto, decisión y resultado.

Lo que un backtest no guarda es **por qué** se entró. Un backtest sabe que la
señal se disparó y qué pasó después; no sabe qué más estaba ocurriendo en el
gráfico ni qué esperaba quien entró. Eso es lo que se registra acá.

**La razón se sella antes de conocer el resultado.** No como convención sino por
construcción: al abrir se calcula un hash del contexto y la razón, y al cerrar
se verifica. Un registro cuya razón pudo escribirse sabiendo cómo salió no vale
nada, y la única forma de garantizarlo es que sea imposible falsificarlo.

**Los números los calcula este módulo, no el modelo.** Precio, R múltiplo, P&L:
todo sale de aritmética con los datos del mercado. Ya está medido que un modelo
de 8B da 1.43 donde el valor real es 17.35 —eligió desvío poblacional en vez de
muestral— y una decisión de trading sobre un número inventado no es exploración,
es ruido. El modelo elige **cuándo y por qué**; el código calcula **qué pasó**.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# El esquema vive en SQLite y no en Redis: acá lo que importa es poder consultar
# "todas las operaciones cuya razón menciona X, agrupadas por eje" sin cargar
# todo en memoria. Y un archivo se versiona y se copia; una base en la nube no.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS operaciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    eje             TEXT    NOT NULL,   -- qué hipótesis la generó
    simbolo         TEXT    NOT NULL,
    direccion       TEXT    NOT NULL,   -- long | short
    abierta_en      TEXT    NOT NULL,   -- ISO 8601 UTC
    precio_entrada  REAL    NOT NULL,
    stop_loss       REAL    NOT NULL,
    take_profit     REAL,
    contexto        TEXT    NOT NULL,   -- JSON: el estado del gráfico al entrar
    razon           TEXT    NOT NULL,   -- lo que el agente escribió AL ENTRAR
    sello           TEXT    NOT NULL,   -- hash de lo de arriba
    -- ⚠ EL STOP MOVIDO VA APARTE Y NO PISA `stop_loss`. Mover el stop a la
    -- entrada tras un parcial es gestión normal, pero el R múltiplo tiene que
    -- seguir midiéndose contra el riesgo que se asumió AL ENTRAR: recalcularlo
    -- contra un stop movido convertiría toda gestión en una mejora artificial
    -- del resultado y haría incomparables las operaciones entre sí. Además
    -- `stop_loss` está sellado, así que tocarlo marcaría la fila como
    -- adulterada — que es lo correcto para una edición, no para una decisión.
    stop_actual     REAL,               -- el stop vigente, si se movió
    nota_stop       TEXT,               -- por qué se movió
    -- Lo de abajo se escribe después, al cerrar.
    cerrada_en      TEXT,
    precio_salida   REAL,
    motivo_cierre   TEXT,               -- stop | objetivo | manual
    r_multiplo      REAL,               -- (salida-entrada)/(entrada-stop), con signo
    contexto_salida TEXT,               -- el gráfico al cerrar
    analisis        TEXT                -- qué dijo el agente DESPUÉS, aparte
);
CREATE INDEX IF NOT EXISTS idx_eje ON operaciones(eje);
CREATE INDEX IF NOT EXISTS idx_abiertas ON operaciones(cerrada_en) WHERE cerrada_en IS NULL;

-- Las salidas parciales: cada vez que se suelta un trozo de la posición.
--
-- ⚠ UNA TABLA APARTE Y NO COLUMNAS MÁS. Tomar parciales es lo normal —salir a
-- la mitad en el primer objetivo y mover el stop a la entrada— y el número de
-- salidas no se sabe de antemano. Con columnas habría que elegir un máximo
-- arbitrario; con filas, una operación puede tener las que haga falta.
--
-- ⚠ `fraccion` ES DE LA POSICIÓN ORIGINAL, no de lo que queda. Si alguien sale
-- de la mitad y después de "la mitad", lo segundo es ambiguo: ¿la mitad de
-- todo, o del resto? Referido siempre al total, la suma de las fracciones no
-- puede pasar de 1 y eso se verifica al escribir. Es la definición que hace
-- imposible el error, no la que suena más natural al hablar.
CREATE TABLE IF NOT EXISTS tramos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operacion_id    INTEGER NOT NULL REFERENCES operaciones(id),
    salida_en       TEXT    NOT NULL,   -- ISO 8601 UTC
    precio_salida   REAL    NOT NULL,
    fraccion        REAL    NOT NULL,   -- porción de la posición ORIGINAL (0..1]
    motivo          TEXT    NOT NULL,   -- parcial | stop | objetivo | manual
    r_multiplo      REAL    NOT NULL,   -- el R de ESTE tramo, sin ponderar
    contexto_salida TEXT,
    analisis        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tramos ON tramos(operacion_id);
"""


@dataclass(frozen=True, slots=True)
class Contexto:
    """El estado del gráfico en un momento. Lo que un backtest no guarda.

    Son los ejes que el repo de trading ya identificó como relevantes en
    `rangeSweepCombo.ts`: ancho del rango, día de la semana, régimen de BTC.
    Se guardan **todos siempre**, aunque el eje que generó la señal use solo
    algunos: el objetivo es poder preguntar después "¿fallaba más los miércoles?"
    sin haber decidido de antemano que el día importaba.
    """

    precio: float
    timestamp: str
    dia_semana: int  # 0=lunes
    hora_utc: int
    ancho_rango_pct: float | None = None
    velas_en_rango: int | None = None
    regimen_btc: str | None = None  # ascending | descending | neutral
    volumen_relativo: float | None = None  # contra la media de las últimas N
    extra: dict[str, Any] = field(default_factory=dict)


def _sellar(
    contexto: Contexto,
    razon: str,
    eje: str,
    *,
    simbolo: str,
    direccion: str,
    stop_loss: float,
    take_profit: float | None,
) -> str:
    """El hash que hace imposible reescribir la decisión después.

    ⚠ SELLA LOS NÚMEROS, NO SOLO LA PROSA. La primera versión hasheaba solo
    `{eje, razon, contexto}` y dejaba fuera `direccion` y `stop_loss` — que son
    justamente los dos campos de los que sale el R múltiplo. Medido: un
    `UPDATE ... SET direccion='long'` sobre un short convertía un −4R en un +4R
    y `verificar_sellos()` seguía devolviendo `[]`, o sea "historial limpio".
    Mover el stop de 99 a 99.9 llevaba un 4R a 40R con el mismo silencio.

    Era el fraude que este módulo existe para impedir, y además el más rentable:
    editar la razón cambia el relato, editar el stop cambia la cifra que decide
    si el eje sirve. Un sello que cubre la explicación pero no la decisión no
    protege nada.

    `simbolo` y `take_profit` entran por el mismo motivo: cambiar el par
    recontextualiza la operación entera, y el objetivo es parte de lo que se
    decidió al entrar.
    """
    material = json.dumps(
        {
            "eje": eje,
            "razon": razon,
            "contexto": asdict(contexto),
            "simbolo": simbolo,
            "direccion": direccion,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class Registro:
    """Las operaciones en papel, en SQLite."""

    def __init__(self, ruta: str | Path) -> None:
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.ruta)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(ESQUEMA)
        self._migrar()
        self._con.commit()

    def _migrar(self) -> None:
        """Las columnas que se añadieron después, sobre una base que ya existe.

        `CREATE TABLE IF NOT EXISTS` no altera una tabla creada antes, así que
        un registro con operaciones dentro se quedaría sin las columnas nuevas y
        fallaría al escribirlas. Sin esto, estrenar los parciales obligaría a
        borrar el historial — que es justo lo que este módulo existe para
        conservar.
        """
        columnas = {f["name"] for f in self._con.execute("PRAGMA table_info(operaciones)")}
        for nombre, tipo in (("stop_actual", "REAL"), ("nota_stop", "TEXT")):
            if nombre not in columnas:
                self._con.execute(f"ALTER TABLE operaciones ADD COLUMN {nombre} {tipo}")  # noqa: S608

    def abrir(
        self,
        *,
        eje: str,
        simbolo: str,
        direccion: str,
        contexto: Contexto,
        razon: str,
        stop_loss: float,
        take_profit: float | None = None,
    ) -> int:
        """Registra una entrada. La razón queda sellada acá y no se toca más."""
        if direccion not in ("long", "short"):
            raise ValueError(f"dirección desconocida: {direccion!r}")
        if not razon.strip():
            raise ValueError("una entrada sin razón escrita no se registra")
        # El stop del lado correcto: un long con el stop arriba del precio no es
        # un error de tipeo del modelo, es una señal de que no entendió la
        # operación, y guardarlo contamina el R múltiplo de todo el eje.
        if direccion == "long" and stop_loss >= contexto.precio:
            raise ValueError("en un long el stop va por debajo del precio de entrada")
        if direccion == "short" and stop_loss <= contexto.precio:
            raise ValueError("en un short el stop va por encima del precio de entrada")
        # ⚠ UN RIESGO MINÚSCULO NO ES UNA OPERACIÓN AJUSTADA, ES UN ERROR DE
        # TIPEO. El R múltiplo divide por la distancia al stop, así que un stop
        # pegado al precio produce cifras absurdas: medido, `stop_loss=99.99999999`
        # sobre un precio de 100 dio **50.000.031 R** en una sola operación. Eso
        # no desvía el promedio del eje, lo destruye — y como `por_eje()` es lo
        # que alimenta el criterio de aborto, ese eje queda "ganador" para
        # siempre por un decimal de más.
        #
        # El mínimo es 0.05% del precio. Por debajo de eso el ruido del mercado
        # ya toca el stop, así que no hay operación que registrar: hay un error.
        if abs(contexto.precio - stop_loss) < contexto.precio * 0.0005:
            raise ValueError(
                f"el stop está pegado al precio (riesgo {abs(contexto.precio - stop_loss)}): "
                "por debajo del 0.05% el R múltiplo se dispara y contamina el eje"
            )
        # El objetivo del lado correcto, por lo mismo que el stop: un long con el
        # objetivo por debajo de la entrada no es una operación, es un descuido.
        if take_profit is not None:
            if direccion == "long" and take_profit <= contexto.precio:
                raise ValueError("en un long el objetivo va por encima del precio de entrada")
            if direccion == "short" and take_profit >= contexto.precio:
                raise ValueError("en un short el objetivo va por debajo del precio de entrada")

        cursor = self._con.execute(
            """INSERT INTO operaciones
               (eje, simbolo, direccion, abierta_en, precio_entrada, stop_loss,
                take_profit, contexto, razon, sello)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                eje,
                simbolo,
                direccion,
                # ⚠ LA HORA DEL REGISTRO, NO LA DE LA VELA. Acá iba
                # `contexto.timestamp`, que es el cierre de la última vela: en
                # 15m puede llevar hasta un cuarto de hora de atraso, así que la
                # duración de toda operación salía sesgada. Y cuando el proceso
                # muere entre abrir y cerrar —el caso que `abiertas()` existe
                # para cubrir— no quedaba ningún sello temporal propio: no había
                # forma de saber cuánto llevaba abierta una posición al volver.
                # La hora de la vela no se pierde: está en el contexto sellado.
                datetime.now(UTC).isoformat(),
                contexto.precio,
                stop_loss,
                take_profit,
                json.dumps(asdict(contexto), ensure_ascii=False),
                razon.strip(),
                _sellar(
                    contexto,
                    razon.strip(),
                    eje,
                    simbolo=simbolo,
                    direccion=direccion,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                ),
            ),
        )
        self._con.commit()
        return int(cursor.lastrowid or 0)

    def cerrar(
        self,
        operacion_id: int,
        *,
        precio_salida: float,
        motivo: str,
        contexto_salida: Contexto | None = None,
        analisis: str = "",
    ) -> float:
        """Cierra una operación y devuelve su R múltiplo.

        El R lo calcula este método, no quien llama: es la única cifra que
        decide si un eje sirve, y dejarla en manos del modelo sería construir
        todo el registro sobre un número que puede estar mal.
        """
        # ⚠ `parcial` NO ESTÁ, y su ausencia es la decisión. El esquema no tiene
        # columna de tamaño ni filas hijas, así que un cierre parcial escribía
        # `cerrada_en` igual que uno total: la posición desaparecía de
        # `abiertas()`, el resto no se registraba nunca y el eje contabilizaba el
        # R del primer tramo como si fuera el resultado completo. Medido.
        #
        if motivo not in ("stop", "objetivo", "manual"):
            raise ValueError(
                f"motivo de cierre desconocido: {motivo!r}. Son stop, objetivo o manual "
                "(para soltar solo una parte, usá salir_parcial)."
            )
        fila = self._abierta(operacion_id)
        vendido = self._fraccion_vendida(operacion_id)
        restante = 1.0 - vendido
        r_tramo = self._r_de(fila, precio_salida)

        ahora = datetime.now(UTC).isoformat()
        contexto_json = (
            json.dumps(asdict(contexto_salida), ensure_ascii=False) if contexto_salida else None
        )
        self._con.execute(
            """INSERT INTO tramos
               (operacion_id, salida_en, precio_salida, fraccion, motivo, r_multiplo,
                contexto_salida, analisis)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                operacion_id,
                ahora,
                precio_salida,
                restante,
                motivo,
                r_tramo,
                contexto_json,
                analisis.strip(),
            ),
        )

        # ⚠ EL R DE LA OPERACIÓN ES LA SUMA PONDERADA DE SUS TRAMOS, no el del
        # último. Quien sale de la mitad a +2R y del resto a 0R hizo +1R, no 0R:
        # guardar solo la última salida borraría la ganancia ya tomada, que es
        # justamente lo que la gestión por parciales busca conseguir.
        r_total = self._r_ponderado(operacion_id)
        self._con.execute(
            """UPDATE operaciones
               SET cerrada_en=?, precio_salida=?, motivo_cierre=?, r_multiplo=?,
                   contexto_salida=?, analisis=?
               WHERE id=?""",
            (ahora, precio_salida, motivo, r_total, contexto_json, analisis.strip(), operacion_id),
        )
        self._con.commit()
        return r_total

    def salir_parcial(
        self,
        operacion_id: int,
        *,
        precio_salida: float,
        fraccion: float,
        contexto_salida: Contexto | None = None,
        analisis: str = "",
    ) -> float:
        """Suelta una parte de la posición y deja el resto abierto.

        Devuelve el R **de este tramo**, sin ponderar: es lo que hizo esta
        salida concreta. El de la operación entera se conoce al cerrarla.

        `fraccion` es de la posición ORIGINAL. Ver el comentario del esquema
        sobre por qué esa definición y no "lo que queda".
        """
        if not 0 < fraccion < 1:
            raise ValueError(
                f"la fracción de un parcial va entre 0 y 1 sin incluirlos: {fraccion}. "
                "Para soltar todo lo que queda, usá cerrar."
            )
        fila = self._abierta(operacion_id)
        vendido = self._fraccion_vendida(operacion_id)
        # Con tolerancia, porque tres tercios en coma flotante no suman 1 exacto
        # y no tiene sentido rechazar un parcial por 1e-16.
        if vendido + fraccion > 1.0 + 1e-9:
            raise ValueError(
                f"no queda tanta posición: ya se soltó {vendido:.2f} y se pide {fraccion:.2f}"
            )

        r_tramo = self._r_de(fila, precio_salida)
        self._con.execute(
            """INSERT INTO tramos
               (operacion_id, salida_en, precio_salida, fraccion, motivo, r_multiplo,
                contexto_salida, analisis)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                operacion_id,
                datetime.now(UTC).isoformat(),
                precio_salida,
                fraccion,
                "parcial",
                r_tramo,
                json.dumps(asdict(contexto_salida), ensure_ascii=False)
                if contexto_salida
                else None,
                analisis.strip(),
            ),
        )
        self._con.commit()
        return r_tramo

    def mover_stop(self, operacion_id: int, *, nuevo_stop: float, razon: str = "") -> None:
        """Mueve el stop de una operación abierta.

        ⚠ NO TOCA EL SELLO, y por eso hace falta este método en vez de un UPDATE
        a mano. El sello cubre `stop_loss` —cambiarlo por fuera marca la
        operación como adulterada, que es lo correcto—, así que mover el stop
        legítimamente exige registrarlo como lo que es: una decisión posterior,
        anotada aparte, sin tocar lo que se selló al entrar.

        ⚠ EL R MÚLTIPLO SIGUE USANDO EL STOP ORIGINAL. Es lo que hace
        comparables las operaciones entre sí: el riesgo que se asumió al entrar
        es el que se asumió, y recalcularlo contra un stop movido convertiría
        toda gestión en una mejora artificial del resultado. Por eso esto va a
        `stop_actual` y no pisa `stop_loss`.
        """
        fila = self._abierta(operacion_id)
        direccion = fila["direccion"]
        if direccion == "long" and nuevo_stop >= fila["precio_entrada"] + abs(
            fila["precio_entrada"] - fila["stop_loss"]
        ):
            raise ValueError("ese stop está por encima de lo razonable para un long")
        self._con.execute(
            "UPDATE operaciones SET stop_actual = ?, nota_stop = ? WHERE id = ?",
            (nuevo_stop, razon.strip(), operacion_id),
        )
        self._con.commit()

    def tramos(self, operacion_id: int) -> list[dict[str, Any]]:
        """Las salidas de una operación, en orden."""
        return [
            dict(f)
            for f in self._con.execute(
                "SELECT * FROM tramos WHERE operacion_id = ? ORDER BY id", (operacion_id,)
            )
        ]

    def _abierta(self, operacion_id: int) -> sqlite3.Row:
        """La fila de una operación que todavía admite salidas."""
        fila = self._con.execute(
            "SELECT * FROM operaciones WHERE id = ?", (operacion_id,)
        ).fetchone()
        if fila is None:
            raise ValueError(f"no existe la operación {operacion_id}")
        if fila["cerrada_en"]:
            raise ValueError(f"la operación {operacion_id} ya estaba cerrada")
        return fila

    def _fraccion_vendida(self, operacion_id: int) -> float:
        fila = self._con.execute(
            "SELECT COALESCE(SUM(fraccion), 0.0) AS v FROM tramos WHERE operacion_id = ?",
            (operacion_id,),
        ).fetchone()
        return float(fila["v"])

    def _r_de(self, fila: sqlite3.Row, precio_salida: float) -> float:
        """El R de una salida a ese precio, contra el riesgo ORIGINAL."""
        entrada, stop = fila["precio_entrada"], fila["stop_loss"]
        riesgo = abs(entrada - stop)
        if riesgo == 0:
            raise ValueError("el stop coincide con la entrada: el riesgo sería cero")
        ganancia = (
            precio_salida - entrada if fila["direccion"] == "long" else entrada - precio_salida
        )
        return ganancia / riesgo

    def _r_ponderado(self, operacion_id: int) -> float:
        """El R de la operación entera: cada tramo por la parte que soltó."""
        fila = self._con.execute(
            "SELECT COALESCE(SUM(r_multiplo * fraccion), 0.0) AS r FROM tramos "
            "WHERE operacion_id = ?",
            (operacion_id,),
        ).fetchone()
        return float(fila["r"])

    def abiertas(self, eje: str = "") -> list[dict[str, Any]]:
        """Las operaciones sin cerrar. Es lo que el agente lee al volver.

        Entre una sesión y la siguiente el proceso muere —en un Codespace, cada
        vez— así que esto es lo único que sabe qué quedó a medias.
        """
        sql = "SELECT * FROM operaciones WHERE cerrada_en IS NULL"
        parametros: tuple[Any, ...] = ()
        if eje:
            sql += " AND eje = ?"
            parametros = (eje,)
        return [dict(f) for f in self._con.execute(sql + " ORDER BY id", parametros)]

    def por_eje(self) -> list[dict[str, Any]]:
        """Cómo va cada eje. Sin ordenar por resultado: elegir el mejor mirando
        esta tabla es exactamente el sobreajuste que el criterio de aborto
        prohíbe, y ordenarla lo invita."""
        return [
            dict(f)
            for f in self._con.execute(
                # ⚠ `WHERE r_multiplo IS NOT NULL` ADEMÁS DE `cerrada_en`. Una
                # sola fila cerrada sin R —una migración, un cierre a mano—
                # hacía que AVG y SUM devolvieran NULL para el eje ENTERO: 50
                # operaciones buenas y una rota daban `r_total: None`. Medido.
                # Una fila que no se puede medir se excluye del recuento; no
                # borra lo que sí se midió.
                #
                # ⚠ `>= 0` EN LOS ACIERTOS, no `> 0`. Un breakeven exacto no es
                # una derrota: salir a 0R después de mover el stop es un
                # resultado neutro, y contarlo como fallo castiga justamente la
                # gestión que el experimento quiere observar.
                """SELECT eje,
                          COUNT(*) AS cerradas,
                          ROUND(AVG(r_multiplo), 3) AS r_promedio,
                          ROUND(SUM(r_multiplo), 2) AS r_total,
                          ROUND(100.0 * SUM(r_multiplo >= 0) / COUNT(*), 1) AS aciertos_pct
                   FROM operaciones
                   WHERE cerrada_en IS NOT NULL AND r_multiplo IS NOT NULL
                   GROUP BY eje
                   ORDER BY eje"""
            )
        ]

    def verificar_sellos(self) -> list[int]:
        """Los ids cuya decisión cambió después de registrarse.

        Debería devolver siempre una lista vacía. Si no lo hace, los datos no
        sirven: significa que alguien editó una razón, un stop o una dirección,
        y no hay forma de saber si fue antes o después de ver el resultado.

        ⚠ UNA FILA ILEGIBLE CUENTA COMO ROTA, no como un error del proceso. Esto
        hacía `Contexto(**json.loads(...))` a pelo, así que un solo contexto
        corrupto —o una clave que dejara de existir en el dataclass— lanzaba y
        se llevaba puesta la publicación entera y `estado_paper` con ella. El
        agente perdía el registro completo por una fila mala, que es el peor
        canje posible: lo que no se puede verificar es exactamente lo que hay
        que marcar como no confiable, no un motivo para no enseñar el resto.
        """
        rotos = []
        for f in self._con.execute("SELECT * FROM operaciones"):
            try:
                contexto = Contexto(**json.loads(f["contexto"]))
                sello = _sellar(
                    contexto,
                    f["razon"],
                    f["eje"],
                    simbolo=f["simbolo"],
                    direccion=f["direccion"],
                    stop_loss=f["stop_loss"],
                    take_profit=f["take_profit"],
                )
            except (ValueError, TypeError):
                rotos.append(int(f["id"]))
                continue
            if sello != f["sello"]:
                rotos.append(int(f["id"]))
        return rotos

    def cerrar_conexion(self) -> None:
        self._con.close()
