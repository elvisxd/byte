"""La oferta, ya normalizada. Cada feed devuelve una forma distinta; acá entran
todas a la misma.

Sin esto, puntuar sería un `if` por fuente dentro del criterio, y agregar un
feed nuevo significaría tocar el código que decide. Con esto, una fuente nueva
es una función que devuelve `Oferta` y el criterio ni se entera.
"""

import hashlib
import re
from dataclasses import dataclass, field

# Palabras que no distinguen a una empresa de otra y que, si quedan en la clave
# de deduplicación, hacen que "Acme Inc." y "Acme LLC" parezcan dos empresas.
_RUIDO_EMPRESA = re.compile(r"\b(inc|llc|ltd|corp|co|sa|srl|gmbh|bv|plc)\b\.?", re.I)
_NO_ALFANUM = re.compile(r"[^a-z0-9]+")


def _normalizar(texto: str) -> str:
    return _NO_ALFANUM.sub(" ", texto.lower()).strip()


@dataclass(frozen=True, slots=True)
class Oferta:
    """Una oferta de trabajo, venga de donde venga.

    `descripcion` es texto plano, no HTML: los feeds mandan HTML y el que
    convierte es el adaptador de la fuente, no el que puntúa. Un `<p>` suelto en
    el medio de una descripción arruina tanto la búsqueda de términos como el
    mensaje de Telegram.
    """

    fuente: str
    id_externo: str
    titulo: str
    empresa: str
    url: str
    descripcion: str
    ubicacion: str = ""
    publicada: str = ""
    salario: str = ""
    etiquetas: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clave(self) -> str:
        """Identidad dentro de su fuente. Es lo que se guarda como 'ya visto'."""
        return f"{self.fuente}:{self.id_externo}"

    @property
    def huella(self) -> str:
        """Identidad *entre* fuentes: misma empresa y mismo puesto.

        La misma búsqueda de "Senior React Engineer en Acme" aparece publicada en
        RemoteOK y en We Work Remotely el mismo día. Con `clave` sola llegarían
        las dos al teléfono como si fueran dos oportunidades, y la segunda solo
        gasta tu atención. La huella las junta.
        """
        base = f"{_RUIDO_EMPRESA.sub('', _normalizar(self.empresa))}|{_normalizar(self.titulo)}"
        return hashlib.sha256(" ".join(base.split()).encode("utf-8")).hexdigest()[:16]

    def buscable(self) -> str:
        """Todo el texto donde se buscan términos y señales, en minúsculas."""
        partes = (
            self.titulo,
            self.empresa,
            self.ubicacion,
            " ".join(self.etiquetas),
            self.salario,
            self.descripcion,
        )
        return " ".join(partes).lower()
