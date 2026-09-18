"""Qué hacer en cada sitio, que no es lo mismo en los dos.

Upwork y LinkedIn se parecen en la pantalla y no se parecen en nada más. En
Upwork postular **cuesta**: cada propuesta se paga en Connects y el stock es
finito, así que el problema es dónde gastarlos. En LinkedIn postular es gratis:
lo finito ahí es tu tiempo, así que el problema es no gastarlo en las que ya
están decididas. Un mismo consejo para los dos sería malo para los dos.

Acá hay dos cosas distintas y conviene no mezclarlas:

- **Los filtros y los pasos** son fijos: dónde está la perilla en cada sitio.
  No dependen de lo que pegaste, así que están escritos.
- **Las medidas** se calculan sobre lo que acabás de pegar. Son las que
  contestan "¿y hoy?", y cambian de una pegada a la siguiente.

Lo escrito no afirma números de planes ni precios: eso Upwork lo cambia cuando
quiere y una lista desactualizada es peor que no tenerla. Los Connects que se
reparten son los que vos declarás tener.
"""

from dataclasses import dataclass

from empleo.upwork import Veredicto

# Los dos umbrales que deciden si una oferta todavía está abierta de verdad.
# No son del criterio —eso puntúa el encaje— sino de la carrera: una oferta con
# muchos competidores y muchas horas encima ya tiene finalista.
FRESCA_HORAS = 24.0
POCA_COMPETENCIA = 5


@dataclass(frozen=True, slots=True)
class Guia:
    """El manual de un sitio: qué te limita, qué filtrar y qué hacer."""

    nombre: str
    limite: str
    filtros: tuple[str, ...]
    pasos: tuple[str, ...]


GUIAS: dict[str, Guia] = {
    "upwork": Guia(
        nombre="Upwork",
        limite=(
            "Postular cuesta Connects y se acaban. Acertar no es aplicar a muchas: "
            "es no gastar en las que ya están decididas."
        ),
        filtros=(
            "Sort by → Newest. Por relevancia te muestra ofertas de hace días, "
            "ya con veinte propuestas adentro.",
            "Payment verified. Sin eso el cliente no puso tarjeta todavía.",
            f"Proposals → Less than {POCA_COMPETENCIA}. Es el único filtro que "
            "ataca la competencia de frente.",
            "Un presupuesto mínimo, por hora o fijo, para que no entren los de "
            "treinta dólares por un sistema entero.",
        ),
        pasos=(
            "Pegá la pantalla de resultados acá y mirá el reparto: las marcadas "
            "«aplicar» son las que valen el gasto, en ese orden.",
            f"Una oferta de más de {FRESCA_HORAS:.0f} h con veinte propuestas ya "
            "tiene finalista. Saltala aunque encaje perfecto.",
            "Las dos primeras líneas de la propuesta son lo único que se ve sin "
            "abrirla. Que hablen del problema del cliente, no de tu carrera.",
            "Si quedan pocos Connects, guardalos. Las ofertas nuevas vuelven "
            "mañana; los Connects no.",
        ),
    ),
    "linkedin": Guia(
        nombre="LinkedIn",
        limite=(
            "Postular es gratis, así que el costo no te frena: te frena el tiempo. "
            "El descarte hay que hacerlo antes de abrir, no después."
        ),
        filtros=(
            "Date posted → Past 24 hours. Es el que más mueve la aguja: después "
            "del primer día ya hay pila de postulantes.",
            "Workplace type → Remote. Híbrido no: una oficina en otro país no la "
            "podés pisar, por muy bien que encaje el puesto.",
            "Sort by → Most recent, no Most relevant.",
            "Experience level y Job type según lo que busques, y dejá afuera Internship.",
        ),
        pasos=(
            "Las tarjetas de la lista no traen la descripción del puesto. Por "
            "eso acá el orden sirve y el veredicto no: el puntaje saldría de un "
            "título de seis palabras.",
            "Para el veredicto completo, abrí una y pegá esa sola: ahí sí hay "
            "descripción y el puntaje de stack vale.",
            "Booleano: AND / OR / NOT en mayúsculas, comillas para frases, "
            "paréntesis para agrupar. LinkedIn no acepta el comodín *, así que "
            "«develop*» no busca nada.",
            "Easy Apply te ahorra tiempo y te pone en la misma fila que todos los "
            "que aplican en masa; cuando la oferta manda a la web de la empresa "
            "esa fila es más corta. Es un criterio, no un número medido.",
        ),
    ),
    "generico": Guia(
        nombre="sitio no reconocido",
        limite=(
            "No reconocí el sitio, así que no sé qué te cuesta postular ahí ni qué "
            "filtros tenés a mano."
        ),
        filtros=(
            "Buscá el filtro de fecha y ponelo en el rango más corto que ofrezca.",
            "Buscá el de modalidad y sacá lo presencial y lo híbrido.",
        ),
        pasos=(
            "El orden de abajo vale igual: sale del mismo criterio de "
            "perfil/busqueda.toml que usa el cazador.",
            "Lo que no vale es la cuenta de competencia, porque no sé dónde la muestra este sitio.",
        ),
    ),
}


def _cuenta(veredictos: list[Veredicto], condicion) -> int:
    return sum(1 for v in veredictos if condicion(v.entrada))


def medidas(sitio: str, veredictos: list[Veredicto]) -> tuple[str, ...]:
    """Lo que se puede afirmar de lo pegado, contando y no opinando.

    Todo lo de acá sale de contar las ofertas que acaban de entrar. Cuando el
    dato no está —hay sitios que no muestran la competencia— se dice que no
    está, en vez de contar cero y que parezca una buena noticia.
    """
    total = len(veredictos)
    if not total:
        return ()

    lineas: list[str] = []
    con_horas = _cuenta(veredictos, lambda e: e.horas is not None)
    frescas = _cuenta(veredictos, lambda e: e.horas is not None and e.horas <= FRESCA_HORAS)
    if con_horas:
        lineas.append(f"Publicadas hace menos de {FRESCA_HORAS:.0f} h: {frescas} de {total}.")
    else:
        lineas.append(f"Ninguna de las {total} trae cuándo se publicó.")

    con_competencia = _cuenta(veredictos, lambda e: e.propuestas is not None)
    if con_competencia:
        tranquilas = _cuenta(
            veredictos,
            lambda e: (
                e.propuestas is not None
                # `<=` y no `<`: "Less than 5" se lee pesimistamente como 5 —ver
                # `_propuestas`—, que es exactamente el cubo que produce el filtro
                # recomendado acá arriba. Con `<` la medida contestaba "ninguna"
                # sobre una pantalla filtrada por ese mismo criterio.
                and e.propuestas <= POCA_COMPETENCIA
                and e.horas is not None
                and e.horas <= FRESCA_HORAS
            ),
        )
        etiqueta = "propuestas" if sitio == "upwork" else "postulantes"
        lineas.append(
            f"Lista corta —{POCA_COMPETENCIA} {etiqueta} o menos Y menos de "
            f"{FRESCA_HORAS:.0f} h—: {tranquilas} de {total}."
        )
    else:
        lineas.append(f"Ninguna de las {total} trae cuánta gente va compitiendo.")

    if sitio == "upwork":
        sin_verificar = _cuenta(veredictos, lambda e: e.verificado is False)
        if sin_verificar:
            sujeto = "son de clientes" if sin_verificar > 1 else "es de un cliente"
            estado = "están penalizadas" if sin_verificar > 1 else "está penalizada"
            lineas.append(
                f"{sin_verificar} {sujeto} sin el pago verificado. "
                f"Ya {estado} en el puntaje; no hace falta filtrar dos veces."
            )
    return tuple(lineas)


def guia_de(sitio: str, veredictos: list[Veredicto]) -> dict:
    """Lo que consume la página: el manual del sitio más las cuentas de hoy."""
    guia = GUIAS.get(sitio, GUIAS["generico"])
    return {
        "nombre": guia.nombre,
        "limite": guia.limite,
        "filtros": list(guia.filtros),
        "pasos": list(guia.pasos),
        "medidas": list(medidas(sitio, veredictos)),
    }
