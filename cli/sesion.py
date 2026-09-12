"""La sesión del CLI: dónde vive el token y cómo se renueva.

La Fase 4 trajo usuarios, pero el CLI solo sabía de la API key — que identifica
a la instancia, no a una persona. Con esto `byte login` guarda un token y el CLI
pasa a ser vos mismo desde la terminal.

Decisiones:

- **El archivo va en `~/.config/byte`**, no en el repo: la sesión es de quien usa
  la máquina, no del proyecto, y así `byte` funciona igual desde cualquier lado.
  Se respeta `XDG_CONFIG_HOME` si está definido.
- **Permisos 0600.** Adentro hay un refresh token que vale 14 días: si otro
  usuario de la máquina puede leerlo, se lleva la sesión entera.
- **Una sesión por URL.** Apuntar a otra instancia no debería pisar el token de
  la local, que es lo que uno tiene abierto todo el día.
- **Sin login, todo sigue igual.** El CLI usa la API key como siempre. El login
  es para cuando querés que tus conversaciones sean tuyas y no de la instancia.
"""

import json
import os
from pathlib import Path
from typing import Any


def _directorio() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "byte" if base else Path.home() / ".config" / "byte"


def _archivo() -> Path:
    return _directorio() / "sesion.json"


def _todo() -> dict[str, Any]:
    """Lo guardado, o un diccionario vacío si no hay nada legible.

    Un archivo corrupto no es motivo para que el CLI no arranque: se ignora y
    quien quiera sesión vuelve a entrar.
    """
    archivo = _archivo()
    if not archivo.is_file():
        return {}
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return datos if isinstance(datos, dict) else {}


def leer(url: str) -> dict[str, Any] | None:
    """La sesión guardada para esa instancia, si la hay."""
    sesion = _todo().get(url.rstrip("/"))
    return sesion if isinstance(sesion, dict) else None


def guardar(url: str, access_token: str, refresh_token: str, email: str) -> None:
    """Guarda la sesión con permisos 0600.

    El directorio también va en 0700: si el archivo es privado pero el
    directorio no, alguien puede reemplazarlo por uno suyo.
    """
    directorio = _directorio()
    directorio.mkdir(parents=True, exist_ok=True)
    os.chmod(directorio, 0o700)  # noqa: S103 - 0700 es lo contrario de permisivo

    datos = _todo()
    datos[url.rstrip("/")] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "email": email,
    }

    archivo = _archivo()
    # Se crea con los permisos correctos desde el principio: escribir primero y
    # ajustar después deja una ventana en la que el token es legible por otros.
    descriptor = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as salida:
        json.dump(datos, salida, indent=2)


def borrar(url: str) -> bool:
    """Olvida la sesión de esa instancia. Devuelve si había alguna."""
    datos = _todo()
    if datos.pop(url.rstrip("/"), None) is None:
        return False
    archivo = _archivo()
    descriptor = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as salida:
        json.dump(datos, salida, indent=2)
    return True


def ruta_visible() -> str:
    """Dónde está el archivo, para poder decírselo a quien pregunte."""
    return str(_archivo())
