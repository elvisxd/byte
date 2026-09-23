"""Los dos brazos de la comparación, lado a lado. Ver paper/CRITERIO_COMPARACION.md.

    uv run python -m paper.comparar paper/operaciones.db paper/operaciones-gemini.db

⚠ NO ORDENA POR RESULTADO, NI ELIGE. Enseña, brazo por brazo, lo que el criterio
dice que se mide: cuántas predicciones resolvió cada uno y con qué Brier por
marco, R por motivo de cierre, y si las razones se repiten. La decisión es a
las 50 predicciones resueltas por brazo, y la toma una persona con esto y las
trazas delante — no este script.
"""

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from paper.prompt import VERSION_PROMPT
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
            # Con qué prompt escribe HOY este brazo, y la muestra de cada
            # prompt por separado: ver `_por_version`.
            "prompt_actual": VERSION_PROMPT,
            "por_version": _por_version(con),
        }
    finally:
        con.close()


# ═══ LA MUESTRA DE CADA PROMPT, POR SEPARADO ═══
#
# Un prompt es otra muestra (paper/prompt.py): lo que un brazo hizo con v4 no
# dice qué hace con v5, y mezclarlos en un Brier deja la lectura sin sentido.
# Cada versión lleva su propia puerta de 50 —igual que el brazo entero— y por
# debajo solo la cuenta. El panel las pone en columnas aparte.
#
# `analista` (v5): la comparación que el sello permite hacer SIN haberla
# mezclado —el Brier del número que firmó el trader contra el de la media de
# las lecturas del analista, en las predicciones donde el trader eligió el
# mismo nivel que el analista—. Es lo que mide si «muestrear y promediar»
# aporta sobre el número del trader.


def _por_version(con: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    filas = list(
        con.execute(
            """SELECT COALESCE(json_extract(contexto, '$.extra.prompt'), '?') v,
                      probabilidad, ocurrio, brier, temporalidad, hacia, nivel, contexto,
                      resuelta_en
               FROM predicciones"""
        )
    )
    por_v: dict[str, list[Any]] = {}
    for f in filas:
        por_v.setdefault(str(f["v"]), []).append(f)
    ops = {
        str(f["v"]): {
            "cerradas": int(f["cerradas"] or 0),
            "abiertas": int(f["abiertas"] or 0),
            "r_total": f["r_total"],
        }
        for f in con.execute(
            """SELECT COALESCE(json_extract(contexto, '$.extra.prompt'), '?') v,
                      SUM(cerrada_en IS NOT NULL) cerradas, SUM(cerrada_en IS NULL) abiertas,
                      ROUND(SUM(CASE WHEN cerrada_en IS NOT NULL THEN r_multiplo END), 2) r_total
               FROM operaciones GROUP BY v"""
        )
    }
    for v in sorted(set(por_v) | set(ops)):
        todas = por_v.get(v, [])
        resueltas = [f for f in todas if f["resuelta_en"] is not None and f["brier"] is not None]
        bloque: dict[str, Any] = {
            "escritas": len(todas),
            "resueltas": len(resueltas),
            "vivas": len(todas) - len(resueltas),
            "faltan": max(0, PREDICCIONES_PARA_DECIDIR - len(resueltas)),
            "operaciones": {
                "cerradas": ops.get(v, {}).get("cerradas", 0),
                "abiertas": ops.get(v, {}).get("abiertas", 0),
                # El R espera a la puerta, como todo lo demás.
                "r_total": None,
            },
            "brier": None,
            "ingenuo": None,
            "por_marco": {},
            "tramos": [],
            "analista": None,
        }
        if len(resueltas) >= PREDICCIONES_PARA_DECIDIR:
            n = len(resueltas)
            ocurrio = sum(int(f["ocurrio"]) for f in resueltas) / n
            bloque["brier"] = round(sum(float(f["brier"]) for f in resueltas) / n, 4)
            bloque["ingenuo"] = round(ocurrio * (1 - ocurrio), 4)
            bloque["operaciones"]["r_total"] = ops.get(v, {}).get("r_total")
            marcos: dict[str, list[Any]] = {}
            tramos: dict[str, list[Any]] = {}
            for f in resueltas:
                marcos.setdefault(str(f["temporalidad"] or "?"), []).append(f)
                base = int(float(f["probabilidad"]) * 100 // 20) * 20
                tramos.setdefault(f"{base}-{base + 20}%", []).append(f)
            bloque["por_marco"] = {m: _celda(fs) for m, fs in sorted(marcos.items())}
            bloque["tramos"] = [{"tramo": t, **_celda(fs)} for t, fs in sorted(tramos.items())]
            bloque["analista"] = _contra_el_analista(resueltas)
        out[v] = bloque
    return out


def _celda(fs: list[Any]) -> dict[str, Any]:
    n = len(fs)
    return {
        "n": n,
        "brier": round(sum(float(f["brier"]) for f in fs) / n, 4),
        "dijo": round(sum(float(f["probabilidad"]) for f in fs) / n, 3),
        "ocurrio": round(sum(int(f["ocurrio"]) for f in fs) / n, 3),
    }


def _contra_el_analista(resueltas: list[Any]) -> dict[str, Any] | None:
    """Trader contra analista, solo donde apostaron al MISMO nivel (v5)."""
    pares: list[tuple[float, float, int]] = []
    for f in resueltas:
        try:
            analista = json.loads(f["contexto"]).get("extra", {}).get("analista")
        except (TypeError, ValueError):
            continue
        if not isinstance(analista, dict):
            continue
        lado = analista.get(str(f["hacia"]))
        if not isinstance(lado, dict) or lado.get("media") is None or lado.get("nivel") is None:
            continue
        if abs(float(lado["nivel"]) - float(f["nivel"])) > 1e-6:
            continue
        pares.append((float(f["probabilidad"]), float(lado["media"]), int(f["ocurrio"])))
    if not pares:
        return None
    n = len(pares)
    return {
        "comparables": n,
        "brier_trader": round(sum((p - o) ** 2 for p, _, o in pares) / n, 4),
        "brier_analista": round(sum((m - o) ** 2 for _, m, o in pares) / n, 4),
    }


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
