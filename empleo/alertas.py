"""Las cadenas para crear alertas guardadas en cada plataforma.

Una alerta guardada le da vuelta al problema: en vez de que vos entres cinco
veces al día a mirar, la plataforma te avisa cuando aparece algo. Para las que
no se pueden leer por API —LinkedIn, Upwork— es la única forma de enterarse
temprano, y llegar temprano es casi todo: en Upwork las primeras propuestas se
leen y las que llegan con veinte encima, no.

**No todas entienden lo mismo**, y ahí está el motivo de que esto sea un módulo
y no una cadena sola:

- LinkedIn y Upwork aceptan `AND` / `OR` / `NOT` en mayúsculas, comillas para
  frases y paréntesis para agrupar. **LinkedIn no acepta el comodín `*`** ni
  llaves ni corchetes, así que acá no se usa comodín en ninguna: la misma cadena
  sirve en las dos y no hay que acordarse de cuál es cuál.
- Los tableros más chicos —Get on Board, RemoteOK— no tienen booleano: una
  cadena con paréntesis y `OR` ahí no filtra, busca esa frase literal y no
  devuelve nada. Para esos van términos sueltos.

Mandarle a los cuatro la misma cadena sería darle a dos de ellos algo que no
entienden, y el síntoma —una alerta que nunca dispara— es idéntico a "no hay
ofertas".

Los términos salen de tu `[stack]` del TOML, no de una lista acá: el criterio
que puntúa y el que busca tienen que ser el mismo, o las alertas te traen cosas
que el cazador después hunde.
"""

from dataclasses import dataclass

from empleo.criterio import Criterio
from empleo.mercado import _comillado

# Cuántos términos entran en la cadena. Con más, LinkedIn empieza a devolver
# cualquier cosa que toque uno solo de ellos y la alerta pierde sentido.
TOPE_TERMINOS = 6

# Lo que nunca querés ver. Las exclusiones rinden más que las inclusiones:
# sacan el ruido que más veces te haría abrir una oferta para nada.
FUERA = ("data entry", "wordpress", "shopify", "virtual assistant")


@dataclass(frozen=True, slots=True)
class Alerta:
    """Qué pegar, dónde, y qué tiene de raro esa plataforma."""

    plataforma: str
    consulta: str
    donde: str
    nota: str


def _terminos(criterio: Criterio) -> list[str]:
    fuertes = list(criterio.stack.get("fuerte", ()))
    if len(fuertes) < TOPE_TERMINOS:
        fuertes += [t for t in criterio.stack.get("medio", ()) if t not in fuertes]
    return fuertes[:TOPE_TERMINOS]


def _booleana(terminos: list[str], con_seniority: bool) -> str:
    grupo = " OR ".join(_comillado(t) for t in terminos)
    fuera = " OR ".join(_comillado(t) for t in FUERA)
    seniority = " AND (senior OR staff OR lead OR principal)" if con_seniority else ""
    return f"({grupo}){seniority} NOT ({fuera})"


def alertas(criterio: Criterio) -> list[Alerta]:
    """Una por plataforma, con la sintaxis que esa plataforma entiende."""
    terminos = _terminos(criterio)
    if not terminos:
        return []
    return [
        Alerta(
            plataforma="LinkedIn",
            consulta=_booleana(terminos, con_seniority=True),
            donde=(
                "Jobs → pegá esto en el buscador → poné los filtros "
                "(Date posted, Remote) → activá la alerta arriba del listado."
            ),
            nota=(
                "Sin comodines: «develop*» no busca nada acá. "
                "La alerta hereda los filtros, así que ponelos ANTES de activarla."
            ),
        ),
        Alerta(
            plataforma="Upwork",
            consulta=_booleana(terminos, con_seniority=False),
            donde=(
                "Buscá con esto, filtrá por Payment verified y pocas propuestas, "
                "y guardá la búsqueda para que te avise."
            ),
            nota=(
                "Sin «senior»: en Upwork ese título lo pone el cliente casi nunca, "
                "y filtrar por él te deja fuera de casi todo. Lo que ordena acá es "
                "la competencia, no el seniority."
            ),
        ),
        Alerta(
            plataforma="Get on Board",
            consulta=" ".join(terminos[:3]),
            donde="Una búsqueda guardada por término; el booleano no lo entiende.",
            nota=(
                "Es el board que nace en la región: quien publica ahí ya contrata "
                "latinoamericanos sin que nadie tenga que escribir «LATAM»."
            ),
        ),
        Alerta(
            plataforma="RemoteOK / Wellfound",
            consulta=" ".join(terminos[:3]),
            donde="Buscá por etiqueta, una por término.",
            nota="Tampoco tienen booleano: una cadena con paréntesis no devuelve nada.",
        ),
    ]


def a_json(criterio: Criterio) -> list[dict]:
    return [
        {
            "plataforma": a.plataforma,
            "consulta": a.consulta,
            "donde": a.donde,
            "nota": a.nota,
        }
        for a in alertas(criterio)
    ]
