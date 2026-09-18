"""Servir el HTML de la página con los estáticos versionados.

Vive en `empleo/` y no en `web/` por una razón de despliegue: lo importan los dos
servidores —la API grande y el servicio suelto— y las tres imágenes ya copian
`empleo/`. Convertir `web/` en paquete de Python para esto obligaba a meter el
CSS y el JS en la imagen del cron, que no sirve ninguna página.

Tampoco puede vivir en `api/`: `empleo/servidor.py` lo necesita, y la imagen de
`ofertas-service` instala tres paquetes y no trae `api/`. Importar de ahí la
rompería al arrancar.
"""

from pathlib import Path


def pagina_con_version(archivo: Path) -> str:
    """El HTML con `?v=` en sus estáticos, sacado de cuándo se modificaron.

    Sin esto, el navegador se queda con el `ofertas.js` que bajó ayer y un
    despliegue nuevo no se ve — pasó de verdad: la sección de alertas estaba
    desplegada y en pantalla no aparecía. El síntoma es el peor posible, porque
    es idéntico a "el código está mal": mirás el servidor, que está bien.

    La versión sale del mtime del propio archivo estático: cambia sólo cuando el
    archivo cambia, así que no rompe el caché en cada despliegue sin motivo.
    """
    html = archivo.read_text(encoding="utf-8")
    estaticos = archivo.parent.parent / "static"
    for recurso in sorted(estaticos.glob("*")) if estaticos.is_dir() else []:
        if not recurso.is_file():
            continue
        referencia = f"/static/{recurso.name}"
        # `"` al final: sólo el atributo entero, para no tocar un nombre que
        # aparezca dentro de otro más largo.
        html = html.replace(f'{referencia}"', f'{referencia}?v={int(recurso.stat().st_mtime)}"')
    return html
