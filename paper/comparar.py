"""Los dos brazos de la comparación, lado a lado. Ver paper/CRITERIO_COMPARACION.md.

    uv run python -m paper.comparar paper/operaciones.db paper/operaciones-gemini.db

⚠ NO ORDENA POR RESULTADO, NI ELIGE. Enseña, brazo por brazo, lo que el criterio
dice que se mide: cuántas predicciones resolvió cada uno y con qué Brier por
marco, R por motivo de cierre, y si las razones se repiten. La decisión es a
las 50 predicciones resueltas por brazo, y la toma una persona con esto y las
trazas delante — no este script.
"""

import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from paper.sesiones import SESIONES, sesion_de

# Umbral del criterio: antes de esto, lo que se ve es ruido.
PREDICCIONES_PARA_DECIDIR = 50


def resumen(ruta: str | Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return {
            "modelos": _modelos(con),
            "predicciones": _predicciones(con),
            "cierres": _cierres(con),
            "razones": _razones(con),
            "ultimas": _ultimas(con),
        }
    finally:
        con.close()


def _modelos(con: sqlite3.Connection) -> dict[str, int]:
    """Qué modelo escribió cuántas cosas. En el brazo remoto son varios: el relevo."""
    cuenta: Counter[str] = Counter()
    for tabla in ("operaciones", "ordenes", "predicciones"):
        for fila in con.execute(f"SELECT modelo, COUNT(*) n FROM {tabla} GROUP BY modelo"):  # noqa: S608 — nombre fijo de arriba
            cuenta[str(fila["modelo"] or "?")] += int(fila["n"])
    return dict(sorted(cuenta.items()))


def _predicciones(con: sqlite3.Connection) -> dict[str, Any]:
    vivas = con.execute("SELECT COUNT(*) FROM predicciones WHERE resuelta_en IS NULL").fetchone()[0]
    por_marco: dict[str, dict[str, Any]] = {}
    for fila in con.execute(
        """SELECT COALESCE(temporalidad, '?') marco, COUNT(*) n,
                  ROUND(AVG(brier), 3) brier, SUM(ocurrio) ocurrieron
           FROM predicciones WHERE resuelta_en IS NOT NULL
           GROUP BY marco ORDER BY marco"""
    ):
        por_marco[fila["marco"]] = {
            "n": fila["n"],
            "brier": fila["brier"],
            "ocurrieron": fila["ocurrieron"],
        }
    total = con.execute(
        "SELECT COUNT(*) n, ROUND(AVG(brier), 3) brier FROM predicciones "
        "WHERE resuelta_en IS NOT NULL"
    ).fetchone()
    # Por la sesión en que se HIZO, derivada de `hecha_en`: es lo que dice si
    # una lectura de Londres vale lo mismo que una de Nueva York.
    acumulado: dict[str, list[float]] = {}
    for fila in con.execute(
        "SELECT hecha_en, brier FROM predicciones "
        "WHERE resuelta_en IS NOT NULL AND brier IS NOT NULL"
    ):
        try:
            sesion = sesion_de(datetime.fromisoformat(str(fila["hecha_en"])))
        except ValueError:
            sesion = "?"
        acumulado.setdefault(sesion, []).append(float(fila["brier"]))
    por_sesion = {
        nombre: {"n": len(v), "brier": round(sum(v) / len(v), 3)}
        for nombre, _, _ in SESIONES
        if (v := acumulado.get(nombre))
    }
    return {
        "vivas": vivas,
        "resueltas": total["n"],
        "brier": total["brier"],
        "por_marco": por_marco,
        "por_sesion": por_sesion,
    }


# Cuántas predicciones viajan una a una. Es para leer CUÁNDO acierta y cuándo
# no un brazo —con su probabilidad delante—, no para contar: contar es lo de
# arriba. Treinta cubren una semana larga a este ritmo y pesan unos 5 KB.
ULTIMAS = 30


def _ultimas(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Las últimas predicciones, de la más nueva a la más vieja, resueltas o vivas."""
    filas = []
    for f in con.execute(
        "SELECT id, hecha_en, temporalidad, nivel, hacia, probabilidad, ocurrio, brier, modelo "
        "FROM predicciones ORDER BY id DESC LIMIT ?",
        (ULTIMAS,),
    ):
        try:
            sesion = sesion_de(datetime.fromisoformat(str(f["hecha_en"])))
        except ValueError:
            sesion = "?"
        filas.append(
            {
                "id": f["id"],
                "hecha_en": f["hecha_en"],
                "temporalidad": f["temporalidad"],
                "sesion": sesion,
                "nivel": f["nivel"],
                "hacia": f["hacia"],
                "probabilidad": f["probabilidad"],
                # None = viva; el panel distingue «esperando» de «no ocurrió».
                "ocurrio": None if f["ocurrio"] is None else bool(f["ocurrio"]),
                "brier": f["brier"],
                "modelo": f["modelo"],
            }
        )
    return filas


def _cierres(con: sqlite3.Connection) -> dict[str, Any]:
    abiertas = con.execute("SELECT COUNT(*) FROM operaciones WHERE cerrada_en IS NULL").fetchone()[
        0
    ]
    por_motivo: dict[str, dict[str, Any]] = {}
    # Orden ALFABÉTICO por motivo, nunca por R.
    for fila in con.execute(
        """SELECT COALESCE(motivo_cierre, '?') motivo, COUNT(*) n, ROUND(SUM(r_multiplo), 2) r
           FROM operaciones WHERE cerrada_en IS NOT NULL GROUP BY motivo ORDER BY motivo"""
    ):
        por_motivo[fila["motivo"]] = {"n": fila["n"], "r": fila["r"]}
    ordenes = con.execute(
        "SELECT COALESCE(resultado, 'viva') r, COUNT(*) n FROM ordenes GROUP BY r ORDER BY r"
    ).fetchall()
    return {
        "abiertas": abiertas,
        "por_motivo": por_motivo,
        "ordenes": {str(f["r"]): f["n"] for f in ordenes},
    }


def _razones(con: sqlite3.Connection) -> dict[str, Any]:
    """Razones distintas sobre razones totales: 1.0 es que ninguna se repite."""
    textos: list[str] = []
    for tabla, columna in (
        ("operaciones", "razon"),
        ("ordenes", "razon"),
        ("predicciones", "razonamiento"),
    ):
        textos += [
            str(f[0]).strip()
            for f in con.execute(f"SELECT {columna} FROM {tabla}")  # noqa: S608 — nombres fijos de arriba
        ]
    return {
        "total": len(textos),
        "distintas": len(set(textos)),
        "mas_repetida": Counter(textos).most_common(1)[0][1] if textos else 0,
    }


def _linea(etiqueta: str, valores: list[str]) -> str:
    return f"  {etiqueta:<34s}" + "".join(f"{v:>26s}" for v in valores)


def imprimir(rutas: list[str]) -> None:
    resumenes = [resumen(r) for r in rutas]
    print(_linea("", [Path(r).stem for r in rutas]))
    print(
        _linea(
            "modelos que escribieron",
            [", ".join(f"{m}×{n}" for m, n in r["modelos"].items()) or "—" for r in resumenes],
        )
    )
    print()
    print("  predicciones")
    print(
        _linea(
            "  resueltas / vivas",
            [f"{r['predicciones']['resueltas']} / {r['predicciones']['vivas']}" for r in resumenes],
        )
    )
    print(_linea("  Brier (todas)", [str(r["predicciones"]["brier"] or "—") for r in resumenes]))
    marcos = sorted({m for r in resumenes for m in r["predicciones"]["por_marco"]})
    for marco in marcos:
        print(
            _linea(
                f"  {marco}: n · Brier · ocurrieron",
                [
                    (
                        f"{d['n']} · {d['brier']} · {d['ocurrieron']}"
                        if (d := r["predicciones"]["por_marco"].get(marco))
                        else "—"
                    )
                    for r in resumenes
                ],
            )
        )
    # En el orden del día UTC, nunca por Brier.
    for nombre, _, _ in SESIONES:
        if any(nombre in r["predicciones"]["por_sesion"] for r in resumenes):
            print(
                _linea(
                    f"  {nombre}: n · Brier",
                    [
                        f"{d['n']} · {d['brier']}"
                        if (d := r["predicciones"]["por_sesion"].get(nombre))
                        else "—"
                        for r in resumenes
                    ],
                )
            )
    print()
    print("  operaciones (R por motivo, en orden alfabético)")
    print(_linea("  abiertas ahora", [str(r["cierres"]["abiertas"]) for r in resumenes]))
    motivos = sorted({m for r in resumenes for m in r["cierres"]["por_motivo"]})
    for motivo in motivos:
        print(
            _linea(
                f"  {motivo}: n · R",
                [
                    f"{d['n']} · {d['r']:+.2f}"
                    if (d := r["cierres"]["por_motivo"].get(motivo))
                    else "—"
                    for r in resumenes
                ],
            )
        )
    print(
        _linea(
            "  órdenes por resultado",
            [
                ", ".join(f"{k}×{v}" for k, v in r["cierres"]["ordenes"].items()) or "—"
                for r in resumenes
            ],
        )
    )
    print()
    print("  razones")
    print(
        _linea(
            "  distintas / total",
            [f"{r['razones']['distintas']} / {r['razones']['total']}" for r in resumenes],
        )
    )
    print(
        _linea("  la más repetida, veces", [str(r["razones"]["mas_repetida"]) for r in resumenes])
    )
    print()
    faltan = [max(0, PREDICCIONES_PARA_DECIDIR - r["predicciones"]["resueltas"]) for r in resumenes]
    if any(faltan):
        print(
            f"  ⚠ Todavía no se decide: faltan {' y '.join(str(f) for f in faltan)} "
            f"predicciones resueltas para llegar a {PREDICCIONES_PARA_DECIDIR} por brazo "
            "(CRITERIO_COMPARACION.md)."
        )


def main() -> None:
    rutas = sys.argv[1:] or ["paper/operaciones.db", "paper/operaciones-gemini.db"]
    for r in rutas:
        if not Path(r).exists():
            print(f"no existe {r}")
            sys.exit(1)
    imprimir(rutas)


if __name__ == "__main__":
    main()
