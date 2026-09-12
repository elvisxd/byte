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
    """Dónde vive la sesión.

    `XDG_CONFIG_HOME` solo se respeta si es una ruta absoluta, como pide la
    especificación: con un valor relativo el token terminaba en el directorio
    desde el que se corrió `byte` —posiblemente dentro de un repo— en vez de en
    el home.
    """
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if base and Path(base).is_absolute():
        return Path(base) / "byte"
    return Path.home() / ".config" / "byte"


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


def _escribir(datos: dict[str, Any]) -> None:
    """Guarda el archivo garantizando que quede privado.

    Tres cosas, y cada una cierra un agujero que el modo de `os.open` solo no
    tapa:

    - **`O_NOFOLLOW`**: el modo no se aplica si el archivo ya existe, y un
      symlink plantado en `sesion.json` se seguiría — el token acabaría en el
      buzón de quien lo plantó, mientras `os.stat` le muestra a la víctima un
      tranquilizador 0600 (el del destino, no el del enlace).
    - **`fchmod` sobre el descriptor**: un archivo preexistente en 0666 se
      quedaba en 0666, con el refresh adentro. Va sobre el descriptor ya
      abierto y no sobre la ruta, para que nadie lo cambie en el medio.
    - **El modo en `os.open`**: para que nazca privado y no haya una ventana
      entre crearlo y ajustarlo.
    """
    archivo = _archivo()
    try:
        descriptor = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        # ELOOP es el symlink; el resto, permisos o un directorio en el medio.
        raise RuntimeError(
            f"no se pudo escribir {archivo} de forma segura: {exc.strerror}"
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as salida:
        os.fchmod(salida.fileno(), 0o600)
        json.dump(datos, salida, indent=2)


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

    _escribir(datos)


def borrar(url: str) -> bool:
    """Olvida la sesión de esa instancia. Devuelve si había alguna."""
    datos = _todo()
    if datos.pop(url.rstrip("/"), None) is None:
        return False
    _escribir(datos)
    return True


def ruta_visible() -> str:
    """Dónde está el archivo, para poder decírselo a quien pregunte."""
    return str(_archivo())
