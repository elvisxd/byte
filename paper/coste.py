"""Cuánto costaría cada brazo si se pagara, leído de sus logs.

    uv run python -m paper.coste /tmp/vigia_gemini.log /tmp/vigia_groq.log

⚠ ESTO NO DECIDE NADA, Y MENOS QUE NADA DECIDE POR PRECIO. El criterio dice que
la comparación se juzga a las 50 predicciones resueltas por brazo y por lo que
hacen —Brier, R, si sus razones discriminan—, no por lo que cuestan. El coste
es la respuesta a «si este brazo resulta ser el bueno, ¿cuánto vale tenerlo?»,
y esa pregunta solo tiene sentido DESPUÉS.

Las tarifas son de la capa de pago, consultadas el 2026-09-17 y anotadas con su
fecha porque caducan: la de Gemini 3.8 Flash es promocional y DOBLA el
2027-01-01. Un número viejo aquí sería peor que ninguno, así que quien lea esto
dentro de unos meses tiene que comprobarlas.

El log da los tokens de cada llamada —los escribe `agent/relevo.py`—, así que
lo que sale de aquí es consumo medido, no estimado. Lo único que se extrapola
es el día completo, y se dice cuándo se hace.
"""

import re
import sys
from pathlib import Path

# Dólares por millón de tokens, capa de pago. Consultadas el 2026-09-17.
# ⚠ La de Gemini es promocional hasta el 2026-12-31; en enero dobla.
TARIFAS: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-3-flash-preview": (0.30, 2.50),
    "groq/openai/gpt-oss-120b": (0.15, 0.60),
    "groq/openai/gpt-oss-20b": (0.10, 0.50),
}
# Para un modelo que no esté en la tabla: no se inventa un precio, se avisa.
SIN_TARIFA = "sin tarifa conocida"

_LINEA = re.compile(
    r"\[relevo\].*?(?:contesta )?(?P<modelo>[\w.\-/]+) · (?P<entrada>\d+) tokens de entrada"
    r"(?: \((?P<cache>\d+) de caché\))?(?: · (?P<salida>\d+) de salida)?"
)


def leer(ruta: Path) -> dict[str, dict[str, int]]:
    """Por modelo: llamadas, tokens de entrada, de caché y de salida."""
    por_modelo: dict[str, dict[str, int]] = {}
    for linea in ruta.read_text(errors="ignore").splitlines():
        m = _LINEA.search(linea)
        if not m:
            continue
        d = por_modelo.setdefault(
            m["modelo"], {"llamadas": 0, "entrada": 0, "cache": 0, "salida": 0}
        )
        d["llamadas"] += 1
        d["entrada"] += int(m["entrada"])
        d["cache"] += int(m["cache"] or 0)
        d["salida"] += int(m["salida"] or 0)
    return por_modelo


def coste(modelo: str, entrada: int, salida: int) -> float | None:
    """Dólares de ese consumo, o None si el modelo no tiene tarifa anotada."""
    tarifa = TARIFAS.get(modelo)
    if tarifa is None:
        return None
    por_entrada, por_salida = tarifa
    return entrada / 1e6 * por_entrada + salida / 1e6 * por_salida


def informe(rutas: list[Path]) -> None:
    for ruta in rutas:
        if not ruta.exists():
            print(f"no existe {ruta}")
            continue
        por_modelo = leer(ruta)
        if not por_modelo:
            print(f"\n{ruta.name}: sin llamadas registradas")
            continue
        print(f"\n{ruta.name}")
        total = 0.0
        sin_tarifa = False
        llamadas = salida_total = 0
        for modelo in sorted(por_modelo):
            d = por_modelo[modelo]
            c = coste(modelo, d["entrada"], d["salida"])
            llamadas += d["llamadas"]
            salida_total += d["salida"]
            if c is None:
                sin_tarifa = True
                precio = SIN_TARIFA
            else:
                total += c
                precio = f"${c:.4f}"
            cache = f" ({d['cache']:,} de caché)" if d["cache"] else ""
            print(
                f"  {modelo:26s} {d['llamadas']:3d} llamadas · "
                f"{d['entrada']:>8,} entrada{cache} · {d['salida']:>7,} salida · {precio}"
            )
        print(f"  {'':26s} {'':3s}            total del periodo: ${total:.4f}")
        if salida_total == 0 and llamadas:
            print(
                "  ⚠ Sin tokens de SALIDA en el log: son anteriores al registro "
                "de salida. El total de arriba solo cuenta la entrada y se queda corto."
            )
        if sin_tarifa:
            print(f"  ⚠ Algún modelo va {SIN_TARIFA}: su consumo no entra en el total.")


def main() -> None:
    # Donde `scripts/vigia.sh` escribe los logs de los brazos remotos. Se LEEN,
    # no se crean: no es un temporal inseguro, es el archivo que el lanzador ya
    # decidió (por eso el noqa).
    rutas = [Path(a) for a in sys.argv[1:]] or [
        Path("/tmp/vigia_gemini.log"),  # noqa: S108
        Path("/tmp/vigia_groq.log"),  # noqa: S108
    ]
    informe(rutas)
    print(
        "\nTarifas de la capa de pago consultadas el 2026-09-17; la de Gemini 3.8 Flash\n"
        "es promocional y dobla el 2027-01-01. Comprobarlas antes de decidir nada."
    )


if __name__ == "__main__":
    main()
