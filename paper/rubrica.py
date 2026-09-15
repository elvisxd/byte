"""La rúbrica del criterio, medida sobre las trazas y los registros de cada brazo.

    uv run python -m paper.rubrica local=paper/operaciones.db gemini=paper/operaciones-gemini.db …

Las cuatro preguntas de paper/CRITERIO_COMPARACION.md («Rúbrica sobre las
trazas»), contadas y no opinadas:

1. ¿Reacciona a un rechazo? Tras un `predecir` rechazado, el siguiente en la
   misma vuelta ¿cambia de nivel o de marco (reacciona) o repite (insiste)?
2. ¿Recorre los ejes o repite una plantilla? Razones distintas sobre razones
   totales (del registro), y cuántos pensamientos nombran tres o más ejes.
3. ¿Cuántas vueltas chocan con el tope de iteraciones sin registrar nada?
4. ¿Juzga una abierta en su marco? Cierres manuales con menos de UNA vela de su
   marco de vida.

⚠ NO ORDENA NI ELIGE. Enseña, por brazo y en orden alfabético, para que una
persona lea las trazas con estas cuentas delante. Las trazas están recortadas
(400 caracteres por argumento): los niveles se leen con una expresión regular
y no con JSON, a propósito.
"""

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from paper.comparar import _razones

EJES = ("range-sweep", "zone-reclaim", "cvd-divergence", "dip-trap", "anti-smc")
MINUTOS_POR_MARCO = {"15m": 15, "1h": 60, "4h": 240}
ESCRIBEN = {"abrir_operacion", "dejar_orden", "predecir", "cerrar_operacion"}

_NIVEL = re.compile(r'"nivel":\s*([\d.]+)')
_MARCO = re.compile(r'"temporalidad":\s*"(\w+)"')


def _vueltas(pasos: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    por: dict[int, list[dict[str, Any]]] = {}
    for p in pasos:
        por.setdefault(int(p.get("vuelta", 0)), []).append(p)
    return por


def _llamadas(pasos: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """(herramienta, argumentos, resultado) en orden, juntando los tres pasos."""
    salida: list[tuple[str, str, str]] = []
    nombre = args = ""
    for p in pasos:
        if p["tipo"] == "herramienta":
            nombre, args = str(p.get("nombre", "")), ""
        elif p["tipo"] == "argumentos":
            args = str(p.get("texto", ""))
        elif p["tipo"] == "resultado":
            salida.append((nombre, args, str(p.get("texto", ""))))
    return salida


def rubrica_de_trazas(archivos: list[Path]) -> dict[str, Any]:
    vueltas = tope_sin_registrar = 0
    rechazos = reacciona = insiste = 0
    pensamientos = recorren = 0
    for archivo in archivos:
        try:
            t = json.loads(archivo.read_text())
        except (OSError, ValueError):
            continue
        for _n, pasos in _vueltas(t.get("pasos", [])).items():
            vueltas += 1
            llamadas = _llamadas(pasos)
            escribio = any(h in ESCRIBEN and '"ok": true' in r for h, _a, r in llamadas)
            al_tope = any(
                p["tipo"] == "texto" and "iteraciones" in str(p.get("texto", "")).lower()
                for p in pasos
            )
            if al_tope and not escribio:
                tope_sin_registrar += 1
            predicciones = [(a, r) for h, a, r in llamadas if h == "predecir"]
            for i, (a, r) in enumerate(predicciones):
                if '"ok": false' not in r or i + 1 >= len(predicciones):
                    continue
                rechazos += 1
                sig = predicciones[i + 1][0]
                n1, n2 = _NIVEL.search(a), _NIVEL.search(sig)
                m1, m2 = _MARCO.search(a), _MARCO.search(sig)
                mismo_nivel = bool(n1 and n2 and abs(float(n1[1]) - float(n2[1])) < 1e-6)
                mismo_marco = (m1[1] if m1 else "1h") == (m2[1] if m2 else "1h")
                if mismo_nivel and mismo_marco:
                    insiste += 1
                else:
                    reacciona += 1
            for p in pasos:
                if p["tipo"] == "pensamiento":
                    pensamientos += 1
                    texto = str(p.get("texto", ""))
                    if sum(e in texto for e in EJES) >= 3:
                        recorren += 1
    return {
        "vueltas": vueltas,
        "tope_sin_registrar": tope_sin_registrar,
        "rechazos": rechazos,
        "reacciona": reacciona,
        "insiste": insiste,
        "pensamientos": pensamientos,
        "recorren_ejes": recorren,
    }


def rubrica_de_registro(ruta_db: str | Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{ruta_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        razones = _razones(con)
        manuales = fuera_de_marco = 0
        for f in con.execute(
            "SELECT abierta_en, cerrada_en, temporalidad FROM operaciones "
            "WHERE cerrada_en IS NOT NULL AND motivo_cierre = 'manual'"
        ):
            manuales += 1
            try:
                vida = (
                    datetime.fromisoformat(str(f["cerrada_en"]))
                    - datetime.fromisoformat(str(f["abierta_en"]))
                ).total_seconds() / 60
            except ValueError:
                continue
            if vida < MINUTOS_POR_MARCO.get(str(f["temporalidad"] or ""), 240):
                fuera_de_marco += 1
    finally:
        con.close()
    return {**razones, "cierres_manuales": manuales, "manuales_antes_de_una_vela": fuera_de_marco}


def _linea(etiqueta: str, valores: list[str]) -> str:
    return f"  {etiqueta:<40s}" + "".join(f"{v:>16s}" for v in valores)


def imprimir(brazos: dict[str, str], trazas: Path) -> None:
    nombres = sorted(brazos)
    t = {n: rubrica_de_trazas(sorted(trazas.glob(f"vigia-{n}*.json"))) for n in nombres}
    r = {n: rubrica_de_registro(brazos[n]) for n in nombres}
    print(_linea("", nombres))
    print(_linea("vueltas en las trazas", [str(t[n]["vueltas"]) for n in nombres]))
    print(
        _linea("3. al tope sin registrar nada", [str(t[n]["tope_sin_registrar"]) for n in nombres])
    )
    print(
        _linea(
            "1. rechazos: reacciona / insiste",
            [f"{t[n]['reacciona']} / {t[n]['insiste']}" for n in nombres],
        )
    )
    print(
        _linea(
            "2. razones distintas / total",
            [f"{r[n]['distintas']} / {r[n]['total']}" for n in nombres],
        )
    )
    print(_linea("2. la más repetida, veces", [str(r[n]["mas_repetida"]) for n in nombres]))
    print(
        _linea(
            "2. pensamientos que recorren ≥3 ejes",
            [f"{t[n]['recorren_ejes']} / {t[n]['pensamientos']}" for n in nombres],
        )
    )
    print(
        _linea(
            "4. cierres manuales antes de una vela",
            [f"{r[n]['manuales_antes_de_una_vela']} / {r[n]['cierres_manuales']}" for n in nombres],
        )
    )


def main() -> None:
    pares = sys.argv[1:] or [
        "local=paper/operaciones.db",
        "gemini=paper/operaciones-gemini.db",
        "groq=paper/operaciones-groq.db",
    ]
    brazos: dict[str, str] = {}
    for par in pares:
        nombre, _, ruta = par.partition("=")
        if not ruta or not Path(ruta).exists():
            print(f"no existe {ruta or par}")
            sys.exit(1)
        brazos[nombre] = ruta
    imprimir(brazos, Path("paper/trazas"))


if __name__ == "__main__":
    main()
