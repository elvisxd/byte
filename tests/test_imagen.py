"""Lo que la imagen de la API tiene que llevar adentro.

El CI construye la imagen y hace `import api.main` adentro, que es la prueba de
verdad. Pero eso tarda minutos y necesita un daemon de Docker; esto corre en
milisegundos y falla con el nombre del paquete que falta. Existe porque ya pasó:
`api/routes/ofertas.py` importa `empleo` al cargar el módulo, el Dockerfile no
lo copiaba, y la imagen se construyó perfecta para reventar al arrancar.
"""

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOCKERFILE = (RAIZ / "docker" / "Dockerfile.api").read_text("utf-8")


def _paquetes_locales() -> set[str]:
    return {d.name for d in RAIZ.iterdir() if d.is_dir() and (d / "__init__.py").exists()}


def _importados_por(paquete: str, locales: set[str]) -> set[str]:
    """Los paquetes locales que `paquete` necesita, siguiendo la cadena entera."""
    usados: set[str] = set()
    vistos: set[str] = set()
    pendientes = [paquete]
    while pendientes:
        actual = pendientes.pop()
        if actual in vistos:
            continue
        vistos.add(actual)
        for archivo in (RAIZ / actual).rglob("*.py"):
            for nodo in ast.walk(ast.parse(archivo.read_text("utf-8"))):
                if isinstance(nodo, ast.Import):
                    nombres = [alias.name.split(".")[0] for alias in nodo.names]
                elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
                    nombres = [(nodo.module or "").split(".")[0]]
                else:
                    continue
                for nombre in nombres:
                    if nombre in locales and nombre != actual:
                        usados.add(nombre)
                        pendientes.append(nombre)
    return usados


def test_la_imagen_copia_todo_lo_que_la_api_importa() -> None:
    locales = _paquetes_locales()
    copiados = set(re.findall(r"^COPY (\w+)/ \1/$", DOCKERFILE, re.M))
    faltan = _importados_por("api", locales) - copiados
    assert not faltan, f"el Dockerfile no copia: {sorted(faltan)}"


def test_la_imagen_no_copia_el_perfil_entero() -> None:
    """En `perfil/` viven `privado.md` y `privado.toml` —dónde va a estar
    viviendo, cuándo vence el permiso— y una imagen se publica. El
    `.dockerignore` también los excluye; esto es el segundo cerrojo, porque
    depender de que nadie se olvide de mantener esa lista no es un cerrojo."""
    assert not re.search(r"^COPY perfil/ ", DOCKERFILE, re.M)
    assert re.search(r"^COPY perfil/busqueda\.toml ", DOCKERFILE, re.M)
