"""Qué está pidiendo el mercado, medido sobre las ofertas de hoy.

**Las palabras clave no se adivinan, se cuentan.** Preguntarle a un modelo "¿qué
términos pongo en la búsqueda?" devuelve lo que suena razonable —y lo mismo que
le devolvería a cualquier otro—. Acá se traen las ofertas reales de todas las
fuentes, se cuenta qué términos aparecen, y se separan en tres montones que
llevan a decisiones distintas:

- **Lo que piden y tu CV ya dice.** Es tu ventaja, y es lo que va en el titular
  de LinkedIn y en las dos primeras líneas de una propuesta.
- **Lo que piden y tu CV no menciona.** No es una lista de lo que te falta
  saber: es lo que no está escrito. Si lo tenés, escribilo; si no, ya sabés qué
  te van a preguntar.
- **Lo que buscás y nadie pide.** Términos de tu `[stack]` que no aparecieron ni
  una vez. Cada uno es una búsqueda que no trae nada y un peso que no mueve
  ningún puntaje.

Corre contra las mismas fuentes que el cazador, así que la foto es del mercado
al que de verdad tenés acceso, no del mercado en general.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from empleo.criterio import Criterio, patron_de, puntuar
from empleo.oferta import Oferta
from empleo.vocabulario import TERMINOS

# Cuántos términos se muestran por montón. Más que esto no se lee ni se acciona.
TOPE_POR_MONTON = 12
# Mínimo de apariciones para que un término cuente como señal y no como ruido.
# Con una sola oferta, cualquier tecnología parece una tendencia.
MINIMO_APARICIONES = 2

# Cuántas ofertas hacen falta para afirmar que algo es PESO MUERTO.
#
# Es el umbral más importante del archivo. Decir "nadie pide TypeScript" después
# de mirar cuatro ofertas no es una medición, es una casualidad con formato de
# conclusión — y es la clase de dato que haría borrar de la búsqueda algo que sí
# sirve. Una corrida real trae cientos, así que el umbral no molesta a nadie
# salvo a quien esté probando con poco, que es justamente cuando hay que callarse.
MINIMO_PARA_PESO_MUERTO = 80


@dataclass(frozen=True, slots=True)
class Radiografia:
    """La foto del mercado, ya contada."""

    analizadas: int
    encajan: int
    # (término, en cuántas ofertas que encajan) ordenado de más a menos.
    piden_y_tenes: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    piden_y_no_decis: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    peso_muerto: tuple[str, ...] = field(default_factory=tuple)

    def porcentaje(self, veces: int) -> int:
        return round(100 * veces / self.encajan) if self.encajan else 0


def analizar(ofertas: list[Oferta], criterio: Criterio, cv: str) -> Radiografia:
    """Cuenta términos sobre las ofertas que encajan con el perfil.

    Se cuenta **sólo sobre las que encajan**, no sobre todas. El mercado entero
    pide WordPress y PHP; eso es cierto y no sirve para nada. Lo que mueve una
    decisión es qué piden las ofertas a las que realmente podrías aplicar.
    """
    encajan = [o for o in ofertas if puntuar(o, criterio).total >= criterio.puntaje_minimo]
    texto_cv = cv.lower()

    demanda: Counter[str] = Counter()
    for oferta in encajan:
        buscable = oferta.buscable()
        # Por oferta y no por aparición: una descripción que repite "python"
        # ocho veces no significa que el mercado lo pida ocho veces más.
        demanda.update({t for t in TERMINOS if patron_de(t).search(buscable)})

    tenes, faltan = [], []
    for termino, veces in demanda.most_common():
        if veces < MINIMO_APARICIONES:
            continue
        destino = tenes if patron_de(termino).search(texto_cv) else faltan
        destino.append((termino, veces))

    # Peso muerto: lo que está en tu `[stack]` y no apareció en ninguna oferta,
    # ni de las que encajan ni de las demás.
    muerto: tuple[str, ...] = ()
    if len(ofertas) >= MINIMO_PARA_PESO_MUERTO:
        todo = " ".join(o.buscable() for o in ofertas)
        mios = {t for grupo in criterio.stack.values() for t in grupo}
        muerto = tuple(sorted(t for t in mios if not patron_de(t).search(todo)))

    return Radiografia(
        analizadas=len(ofertas),
        encajan=len(encajan),
        piden_y_tenes=tuple(tenes[:TOPE_POR_MONTON]),
        piden_y_no_decis=tuple(faltan[:TOPE_POR_MONTON]),
        peso_muerto=muerto,
    )


# Términos con puntuación que romperían una búsqueda booleana si van sueltos.
_NECESITA_COMILLAS = re.compile(r"[^a-z0-9]")


def _comillado(termino: str) -> str:
    return f'"{termino}"' if _NECESITA_COMILLAS.search(termino) else termino


def busquedas(radiografia: Radiografia) -> dict[str, str]:
    """Las cadenas de búsqueda, armadas con lo que se acaba de medir.

    Upwork y LinkedIn aceptan los mismos `AND`/`OR`/`NOT` en mayúsculas, pero
    **LinkedIn no acepta el comodín `*`** ni llaves ni corchetes. Acá no se usa
    comodín en ninguna de las dos, así que la misma cadena sirve en ambas.
    """
    fuertes = [t for t, _ in radiografia.piden_y_tenes[:6]]
    if not fuertes:
        return {}
    grupo = " OR ".join(_comillado(t) for t in fuertes)
    # Las exclusiones rinden más que las inclusiones: sacan el ruido que más
    # veces te haría abrir una oferta para nada.
    fuera = '"data entry" OR wordpress OR shopify OR "virtual assistant"'
    return {
        "amplia": f"({grupo}) NOT ({fuera})",
        "estrecha": f"({grupo}) AND (senior OR staff OR lead OR architect) NOT ({fuera})",
    }


def informe(radiografia: Radiografia) -> str:
    """Lo que se imprime."""
    if not radiografia.analizadas:
        return "Ninguna fuente devolvió ofertas. Probá `--probar` para ver cuál falló."
    if not radiografia.encajan:
        return (
            f"{radiografia.analizadas} ofertas y ninguna llegó al mínimo. "
            "Sin ofertas que encajen no hay nada que contar: o el criterio quedó "
            "demasiado duro, o hoy no había nada."
        )

    def bloque(titulo: str, filas: tuple[tuple[str, int], ...], nota: str) -> list[str]:
        if not filas:
            return [titulo, "  (nada)", ""]
        cuerpo = [
            f"  {termino:<24} en {veces} de {radiografia.encajan} "
            f"({radiografia.porcentaje(veces)}%)"
            for termino, veces in filas
        ]
        return [titulo, *cuerpo, f"  → {nota}", ""]

    lineas = [
        f"Radiografía — {radiografia.analizadas} ofertas, "
        f"{radiografia.encajan} encajan con tu perfil",
        "",
        *bloque(
            "LO QUE PIDEN Y TU CV YA DICE",
            radiografia.piden_y_tenes,
            "esto va en el titular de LinkedIn y en las dos primeras líneas",
        ),
        *bloque(
            "LO QUE PIDEN Y TU CV NO MENCIONA",
            radiografia.piden_y_no_decis,
            "si lo tenés, escribilo; si no, ya sabés qué te van a preguntar",
        ),
    ]
    if radiografia.analizadas < MINIMO_PARA_PESO_MUERTO:
        lineas += [
            f"(Con {radiografia.analizadas} ofertas no se puede decir qué términos "
            f"tuyos no pide nadie: harían falta {MINIMO_PARA_PESO_MUERTO}.)",
            "",
        ]
    elif radiografia.peso_muerto:
        lineas += [
            "PESO MUERTO EN TU BÚSQUEDA",
            f"  {', '.join(radiografia.peso_muerto)}",
            "  → están en [stack] del TOML y no aparecieron en ninguna oferta",
            "",
        ]

    for nombre, cadena in busquedas(radiografia).items():
        lineas += [f"BÚSQUEDA {nombre.upper()} (sirve en Upwork y en LinkedIn)", f"  {cadena}", ""]
    return "\n".join(lineas)


def main() -> None:
    import argparse
    import asyncio
    from pathlib import Path

    from empleo.cazador import recolectar
    from empleo.criterio import cargar_criterio

    raiz = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Mide qué pide el mercado al que tenés acceso y sugiere las búsquedas."
    )
    parser.add_argument("--criterio", type=Path, default=raiz / "perfil" / "busqueda.toml")
    parser.add_argument("--cv", type=Path, default=raiz / "perfil" / "cv.md")
    args = parser.parse_args()

    criterio = cargar_criterio(args.criterio)
    ofertas, conteo = asyncio.run(recolectar(criterio, "AI agent LangGraph RAG"))
    try:
        cv = args.cv.read_text(encoding="utf-8")
    except OSError:
        cv = ""
        print(f"(sin {args.cv}: no se puede separar lo que ya decís de lo que falta)\n")
    print(informe(analizar(ofertas, criterio, cv)))


if __name__ == "__main__":
    main()
