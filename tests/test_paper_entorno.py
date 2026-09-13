"""Las variables del .env que `paper/` lee del entorno, no de `Settings`.

`paper/mercado.py`, `paper/sesion.py`, `paper/trace.py` y `paper/publicar.py`
leen `os.environ` directo: no reciben un `Settings`. Si el `.env` solo alimenta
a `Settings` —que es lo que hace `env_file` de pydantic-settings— esas rutas
quedan vacías y la sesión en papel aborta con «falta BYTE_PAPER_SCRIPTS», o
peor, escribe el SQLite en una ruta relativa que no es la del repo de trading.

En el Codespace no se veía porque `devcontainer.json` pone esas variables en el
entorno por `remoteEnv`. En una máquina local no hay remoteEnv: sin el
`load_dotenv()` de `api/config.py`, estos tests fallan.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _en_proceso_limpio(codigo: str, cwd: Path, entorno: dict[str, str]) -> str:
    """Corre `codigo` en un intérprete aparte.

    Hace falta un proceso nuevo porque `load_dotenv()` se ejecuta al importar
    `api.config`, una sola vez por intérprete: dentro de la suite ya ocurrió, y
    con el `.env` del desarrollador, que no es el que se quiere probar.
    """
    base = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(RAIZ)}
    proceso = subprocess.run(  # noqa: S603 - código y rutas propias del test
        [sys.executable, "-c", textwrap.dedent(codigo)],
        cwd=cwd,
        env={**base, **entorno},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proceso.returncode == 0, proceso.stderr
    return proceso.stdout.strip()


def test_el_env_llega_a_os_environ_y_no_solo_a_settings(tmp_path: Path) -> None:
    """Sin esto, `paper/mercado.py` no encuentra los scripts del repo de trading.

    Es el fallo que dejó una sesión entera sin poder mirar el mercado: la
    variable estaba escrita en el `.env` y `os.environ.get` devolvía "".
    """
    (tmp_path / ".env").write_text(
        "BYTE_PAPER_SCRIPTS=/ruta/al/repo/scripts/paper\n"
        "BYTE_PAPER_DB=/ruta/al/repo/paper/operaciones.db\n"
        "PANEL_URL=https://panel.example\n",
        encoding="utf-8",
    )

    salida = _en_proceso_limpio(
        """
        import os
        import api.config  # noqa: F401 - el import es lo que carga el .env
        print(os.environ.get("BYTE_PAPER_SCRIPTS", ""))
        print(os.environ.get("BYTE_PAPER_DB", ""))
        print(os.environ.get("PANEL_URL", ""))
        """,
        cwd=tmp_path,
        entorno={},
    )

    assert salida.splitlines() == [
        "/ruta/al/repo/scripts/paper",
        "/ruta/al/repo/paper/operaciones.db",
        "https://panel.example",
    ]


def test_el_entorno_le_gana_al_archivo(tmp_path: Path) -> None:
    """El Codespace pone las variables por `remoteEnv` y tienen que mandar ellas.

    Si el `.env` pisara el entorno, un `.env` viejo dentro de la máquina
    reintroduciría el `PANEL_TOKEN` que ya se corrigió en los secretos, y
    `verificarPanel.mjs` volvería a decir que no coincide sin motivo visible.
    """
    (tmp_path / ".env").write_text("PANEL_URL=https://el-del-archivo\n", encoding="utf-8")

    salida = _en_proceso_limpio(
        """
        import os
        import api.config  # noqa: F401 - el import es lo que carga el .env
        print(os.environ.get("PANEL_URL", ""))
        """,
        cwd=tmp_path,
        entorno={"PANEL_URL": "https://el-del-entorno"},
    )

    assert salida == "https://el-del-entorno"
