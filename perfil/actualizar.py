"""Los números del CV, sacados del código en vez de escritos a mano.

El CV en PDF decía "183 tests" cuando ya eran 459: un dato correcto el día que
se escribió y falso tres semanas después. Todo lo que se cuenta —tests, líneas,
repos— envejece igual, así que se mide acá y se pega donde haga falta.

    uv run python perfil/actualizar.py          # muestra los números
    uv run python perfil/actualizar.py --json   # para pegarlos en otra cosa
"""

import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _correr(*cmd: str, cwd: Path | None = None) -> str:
    """La salida del comando, o vacío si falla. Nada de esto vale un traceback."""
    try:
        # S603: los comandos son literales de este archivo, no entrada de nadie.
        salida = subprocess.run(  # noqa: S603
            cmd, cwd=cwd or RAIZ, capture_output=True, text=True, timeout=120, check=False
        )
        return salida.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tests() -> int:
    """Cuántos tests tiene el proyecto.

    Se cuentan **recolectando**, no corriendo: `pytest --collect-only` tarda un
    segundo contra el minuto largo de la suite entera, y para un número del CV
    alcanza con saber cuántos hay.
    """
    # Se corre la suite entera, no `--collect-only`. Recolectar es un segundo
    # contra el minuto de correr, pero cuenta 497 donde pasan 459: la diferencia
    # son los que se saltean sin Postgres. Un número inflado en un CV es peor
    # que esperar un minuto, y además así se sabe que están todos en verde.
    salida = _correr(".venv/bin/python", "-m", "pytest", "-q")
    encontrado = re.search(r"(\d+) passed", salida)
    return int(encontrado.group(1)) if encontrado else 0


def lineas_de_codigo() -> dict[str, int]:
    """Líneas por lenguaje, sin contar dependencias ni lo generado."""
    extensiones = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript"}
    ignorar = {".venv", "node_modules", ".git", "__pycache__", "dist", "build", ".next"}
    total: dict[str, int] = {}
    for ruta in RAIZ.rglob("*"):
        if not ruta.is_file() or ruta.suffix not in extensiones:
            continue
        if any(parte in ignorar for parte in ruta.parts):
            continue
        try:
            total[extensiones[ruta.suffix]] = total.get(extensiones[ruta.suffix], 0) + len(
                ruta.read_text(encoding="utf-8", errors="ignore").splitlines()
            )
        except OSError:
            continue
    return total


def commits() -> int:
    return len([x for x in _correr("git", "log", "--oneline").splitlines() if x])


def herramientas_del_agente() -> list[str]:
    """Las herramientas que el agente puede usar hoy.

    Se leen del registro y no de una lista escrita a mano: agregar una y
    olvidarse de contarla es exactamente lo que hace que un CV mienta.
    """
    sys.path.insert(0, str(RAIZ))
    try:
        from api.config import Settings
        from tools.registry import build_registry

        # `Settings()` ya lee el .env del proyecto: sin eso no ve
        # BYTE_PROJECT_ROOT y las herramientas de archivos quedarían afuera.
        return sorted(t.name for t in build_registry(Settings(_env_file=RAIZ / ".env"), None).all())
    except Exception:  # noqa: BLE001 - un número del CV no vale romper nada
        return []


def repos_de_github() -> dict[str, int]:
    """Cuántos repos hay y cuántos son visibles para alguien que te busca."""
    crudo = _correr("gh", "repo", "list", "--limit", "100", "--json", "name,isPrivate")
    try:
        repos = json.loads(crudo or "[]")
    except json.JSONDecodeError:
        return {}
    return {
        "total": len(repos),
        "publicos": sum(1 for r in repos if not r.get("isPrivate")),
    }


def todo() -> dict[str, object]:
    return {
        "tests": tests(),
        "commits": commits(),
        "lineas": lineas_de_codigo(),
        "herramientas": herramientas_del_agente(),
        "github": repos_de_github(),
    }


if __name__ == "__main__":
    datos = todo()
    if "--json" in sys.argv:
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        print(f"tests:        {datos['tests']}")
        print(f"commits:      {datos['commits']}")
        for lenguaje, n in sorted(datos["lineas"].items(), key=lambda x: -x[1]):  # type: ignore[union-attr]
            print(f"  {lenguaje:12} {n:,} líneas")
        print(f"herramientas: {', '.join(datos['herramientas']) or '(no se pudieron leer)'}")
        gh = datos["github"]
        if gh:
            print(f"github:       {gh['publicos']}/{gh['total']} repos públicos")  # type: ignore[index]
