# El perfil de GitHub, hoy

Medido el 12 de septiembre de 2026 con `gh`. Lo vuelve a medir
`perfil/actualizar.py`.

## El problema principal: lo mejor está invisible

**27 repos, 8 públicos.** El README de perfil (17,5 KB, bien trabajado) destaca
Byte como trabajo principal, pero **el repo es privado**: quien lee "Self-Hosted
AI Agent con LangGraph y MCP", se interesa y hace clic, encuentra un 404.

Lo mismo con el dashboard de trading y GO190 Store. Los únicos proyectos
enlazados y accesibles son el portfolio, un generador de QR y una app del clima
— los tres más chicos.

Esto no lo arregla ninguna automatización: es una decisión sobre qué mostrar.

## Lo que sí se puede arreglar escribiendo

- **Ningún repo tiene descripción.** Es la línea que GitHub muestra en el
  listado y en los resultados de búsqueda; vacía, cada repo se ve abandonado.
- **Sin topics.** Son las etiquetas por las que un reclutador filtra
  (`langgraph`, `rag`, `nextjs`).
- **Byte no está enlazado desde el README** aunque sea el proyecto destacado.

## El CV está desactualizado y duplicado

El PDF dice **"183 tests"**; hoy son **459**. Y hay seis copias en tres lugares,
todas de distinto tamaño:

| dónde | es | en |
|---|---|---|
| `~/Downloads/cv-elvis/` | 600.026 | 590.759 |
| portfolio `public/` | 555.998 | 547.859 |
| portfolio `public/cv/` | 556.545 | 547.859 |

Más las copias de Google Drive, que es de donde se manda desde el teléfono.
Tamaños distintos son versiones distintas: hoy no hay forma de saber cuál se
envió en la última postulación.

**Por eso el CV se escribe en `cv.md` y los PDF se generan.** Una fuente, y las
copias salen de ella en vez de editarse por separado.
