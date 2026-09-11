# web/
Frontend de Byte: FastAPI + Jinja con la identidad de `docs/prompts-canva-byte.md`.

- `templates/index.html` — las dos pantallas (chat y documentos) más el ingreso
- `static/app.js` — cliente de los eventos AG-UI por `EventSource`, historial y
  carga de documentos. Pinta con `textContent`, nunca `innerHTML`: lo que llega
  del modelo, de las herramientas y de los documentos es contenido no confiable
- `static/app.css` — la paleta e identidad Byte, con modo oscuro
- `static/byte.svg` — la mascota, en SVG para que escale y sirva de favicon

La CSP no permite JS ni CSS inline ni de terceros (`script-src 'self'`): por eso
todo va en archivos propios y no hay CDN ni fuentes externas. Manrope e Inter se
usan solo si el sistema ya las tiene.

El chat usa `EventSource` directo y no HTMX: el streaming ya tenía resuelta la
reconexión con `Last-Event-ID` y el ciclo de aprobación del modo seguro, y
reescribirlo no habría agregado nada.
