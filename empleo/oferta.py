"""La oferta, ya normalizada. Cada feed devuelve una forma distinta; acá entran
todas a la misma.

Sin esto, puntuar sería un `if` por fuente dentro del criterio, y agregar un
feed nuevo significaría tocar el código que decide. Con esto, una fuente nueva
es una función que devuelve `Oferta` y el criterio ni se entera.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

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

    def publicada_en(self) -> datetime | None:
        """La fecha de publicación, en UTC. `None` si el feed no la manda o no se entiende.

        Cada fuente la manda distinta —ISO, RFC 822 del RSS, epoch— y ninguna
        promete el formato. Se prueban los tres y se devuelve `None` antes que
        adivinar: una fecha inventada haría que una oferta de hace dos semanas
        se anuncie como recién salida, que es el error más caro de todos acá.
        """
        crudo = self.publicada.strip()
        if not crudo:
            return None

        if crudo.isdigit():  # epoch en segundos
            try:
                return datetime.fromtimestamp(int(crudo), tz=UTC)
            except (ValueError, OSError):
                return None

        try:
            fecha = datetime.fromisoformat(crudo.replace("Z", "+00:00"))
        except ValueError:
            try:
                fecha = parsedate_to_datetime(crudo)
            except (TypeError, ValueError):
                return None
        # Una fecha sin huso se toma como UTC: los feeds publican en UTC y
        # tratarla como local correría la antigüedad varias horas.
        return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)

    def antiguedad_horas(self, ahora: datetime | None = None) -> float | None:
        """Cuántas horas pasaron desde que se publicó. `None` si no se sabe."""
        publicada = self.publicada_en()
        if publicada is None:
            return None
        delta = (ahora or datetime.now(tz=UTC)) - publicada
        # Un feed con el reloj adelantado daría negativo; para lo que sigue, eso
        # es "recién salida", no un error.
        return max(0.0, delta.total_seconds() / 3600)

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
