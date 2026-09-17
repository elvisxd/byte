"""Qué ofertas ya se avisaron. Un JSON, no una tabla.

El cazador corre cada varias horas y los feeds devuelven las mismas ofertas
hasta que caducan: sin esto, el primer aviso del día sería el mismo que el de
ayer y el de anteayer, y a la tercera vez dejarías de abrirlo.

En archivo y no en Postgres porque el cazador tiene que poder correr con Byte
apagado —es un cron, no un endpoint—, y porque borrar el archivo es todo el
"volver a empezar" que esto necesita.
"""

import fcntl
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Cuánto se recuerda una oferta. Más que esto y el archivo crece para siempre
# guardando links de puestos que ya se cubrieron; menos, y una oferta que sigue
# abierta vuelve a sonar como si fuera nueva.
VIGENCIA_DIAS = 45


class Memoria:
    """Las claves y huellas ya avisadas, con cuándo se avisaron."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        self._vistas: dict[str, float] = {}
        if ruta.is_file():
            try:
                crudo = json.loads(ruta.read_text(encoding="utf-8"))
                self._vistas = {str(k): float(v) for k, v in dict(crudo).items()}
            except (OSError, ValueError, TypeError):
                # Un archivo corrupto no puede frenar la búsqueda de trabajo: se
                # arranca de cero y a lo sumo se repite un aviso.
                self._vistas = {}

    def conoce(self, *identidades: str) -> bool:
        return any(i in self._vistas for i in identidades)

    def anotar(self, *identidades: str) -> None:
        ahora = time.time()
        for i in identidades:
            self._vistas[i] = ahora

    def guardar(self) -> None:
        """Reescribe el archivo entero con lo que esta instancia tiene en memoria.

        Es read-modify-write, así que **dos vueltas simultáneas se pisan**: la
        última en guardar borra lo que anotó la primera. Quien llame a esto
        tiene que estar dentro de un `turno()`. Hoy lo garantiza `main()` del
        cazador; `tools/empleo.py` construye una `Memoria` sin turno y por eso
        solo lee.
        """
        corte = time.time() - VIGENCIA_DIAS * 86400
        vivas = {k: v for k, v in self._vistas.items() if v >= corte}
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        # Se escribe al lado y se renombra: un corte de luz a mitad de escritura
        # dejaría el JSON partido, y el próximo arranque perdería todo el
        # historial de avisos.
        temporal = self.ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(vivas, ensure_ascii=False), encoding="utf-8")
        temporal.replace(self.ruta)
        self._vistas = vivas

    def __len__(self) -> int:
        return len(self._vistas)


class YaCorriendo(Exception):
    """Otra vuelta del cazador tiene la carpeta tomada."""


@contextmanager
def turno(carpeta: Path) -> Iterator[None]:
    """Deja correr una sola vuelta a la vez sobre la misma carpeta.

    `Memoria` lee el JSON entero, lo modifica y lo reescribe. Dos vueltas
    solapadas —el cron dispara mientras la anterior sigue esperando a un feed
    lento— leen el mismo archivo y la última en guardar borra lo que anotó la
    primera: esas ofertas vuelven a avisarse mañana como si fueran nuevas.

    Es `flock`, no un archivo de PID: si el proceso muere de cualquier forma el
    sistema suelta el cerrojo solo. Un PID escrito a mano queda para siempre
    cuando lo matan con -9, y el cazador no vuelve a correr hasta que alguien
    borre el archivo a mano.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    cerrojo = carpeta / ".turno"
    # `a` y no `w`: `w` trunca el archivo antes de pedir el cerrojo, así que el
    # que NO consigue el turno igual borraría lo que haya escrito adentro el que
    # sí lo tiene. Hoy el archivo está siempre vacío y da igual; el día que
    # alguien anote un PID o una marca de tiempo para diagnosticar, no.
    with cerrojo.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise YaCorriendo(str(cerrojo)) from exc
        # No hace falta soltarlo a mano: cerrar el descriptor lo suelta, y el
        # `with` cierra por todos los caminos. Un `LOCK_UN` explícito en un
        # `finally` solo agrega una forma de tapar la excepción de la vuelta con
        # un OSError propio.
        yield
