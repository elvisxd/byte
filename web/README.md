# web/
Frontend: HTML mínimo con Jinja en el MVP → FastAPI + Jinja + HTMX (o React) con la identidad Byte en Fase 5.

- `templates/index.html` — página del chat (sin diseño todavía)
- `static/app.js` — cliente de los eventos AG-UI por `EventSource`. Pinta con
  `textContent`, nunca `innerHTML`
- `static/app.css` — estilos mínimos

La CSP no permite JS ni CSS inline: por eso van en archivos aparte.
