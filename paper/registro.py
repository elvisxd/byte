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
    -- Lo de abajo se escribe después, al cerrar.
    cerrada_en      TEXT,
    precio_salida   REAL,
    motivo_cierre   TEXT,               -- stop | objetivo | manual | parcial
    r_multiplo      REAL,               -- (salida-entrada)/(entrada-stop), con signo
    contexto_salida TEXT,               -- el gráfico al cerrar
    analisis        TEXT                -- qué dijo el agente DESPUÉS, aparte
);
CREATE INDEX IF NOT EXISTS idx_eje ON operaciones(eje);
CREATE INDEX IF NOT EXISTS idx_abiertas ON operaciones(cerrada_en) WHERE cerrada_en IS NULL;
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


def _sellar(contexto: Contexto, razon: str, eje: str) -> str:
    """El hash que hace imposible reescribir la razón después.

    Incluye el precio y el timestamp, así que un contexto retocado da otro hash.
    No protege de alguien que borre la fila entera —nada lo hace— pero sí de la
    tentación real: ajustar la razón cuando ya se sabe cómo salió.
    """
    material = json.dumps(
        {"eje": eje, "razon": razon, "contexto": asdict(contexto)},
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
        self._con.commit()

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

        cursor = self._con.execute(
            """INSERT INTO operaciones
               (eje, simbolo, direccion, abierta_en, precio_entrada, stop_loss,
                take_profit, contexto, razon, sello)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                eje,
                simbolo,
                direccion,
                contexto.timestamp,
                contexto.precio,
                stop_loss,
                take_profit,
                json.dumps(asdict(contexto), ensure_ascii=False),
                razon.strip(),
                _sellar(contexto, razon.strip(), eje),
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
        fila = self._con.execute(
            "SELECT * FROM operaciones WHERE id = ?", (operacion_id,)
        ).fetchone()
        if fila is None:
            raise ValueError(f"no existe la operación {operacion_id}")
        if fila["cerrada_en"]:
            raise ValueError(f"la operación {operacion_id} ya estaba cerrada")

        entrada, stop = fila["precio_entrada"], fila["stop_loss"]
        riesgo = abs(entrada - stop)
        if riesgo == 0:
            raise ValueError("el stop coincide con la entrada: el riesgo sería cero")
        ganancia = (
            precio_salida - entrada if fila["direccion"] == "long" else entrada - precio_salida
        )
        r = ganancia / riesgo

        self._con.execute(
            """UPDATE operaciones
               SET cerrada_en=?, precio_salida=?, motivo_cierre=?, r_multiplo=?,
                   contexto_salida=?, analisis=?
               WHERE id=?""",
            (
                datetime.now(UTC).isoformat(),
                precio_salida,
                motivo,
                r,
                json.dumps(asdict(contexto_salida), ensure_ascii=False)
                if contexto_salida
                else None,
                analisis.strip(),
                operacion_id,
            ),
        )
        self._con.commit()
        return r

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
                """SELECT eje,
                          COUNT(*) AS cerradas,
                          ROUND(AVG(r_multiplo), 3) AS r_promedio,
                          ROUND(SUM(r_multiplo), 2) AS r_total,
                          ROUND(100.0 * SUM(r_multiplo > 0) / COUNT(*), 1) AS aciertos_pct
                   FROM operaciones
                   WHERE cerrada_en IS NOT NULL
                   GROUP BY eje
                   ORDER BY eje"""
            )
        ]

    def verificar_sellos(self) -> list[int]:
        """Los ids cuya razón o contexto cambiaron después de registrarse.

        Debería devolver siempre una lista vacía. Si no lo hace, los datos no
        sirven: significa que alguien editó una razón, y no hay forma de saber
        si fue antes o después de ver el resultado.
        """
        rotos = []
        for f in self._con.execute("SELECT * FROM operaciones"):
            contexto = Contexto(**json.loads(f["contexto"]))
            if _sellar(contexto, f["razon"], f["eje"]) != f["sello"]:
                rotos.append(int(f["id"]))
        return rotos

    def cerrar_conexion(self) -> None:
        self._con.close()
