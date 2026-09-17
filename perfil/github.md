# El perfil de GitHub, hoy

Medido el 16 de septiembre de 2026 con `gh`. Lo vuelve a medir
`perfil/actualizar.py`.

## El problema principal: lo mejor está invisible

**26 repos, 8 públicos.** El README de perfil (17,5 KB, bien trabajado) destaca
Byte como trabajo principal.

**Byte ya es público** (se abrió después de esta medición), así que el 404 que
encontraba quien hacía clic en "Self-Hosted AI Agent con LangGraph y MCP" está
resuelto. Falta lo demás: el dashboard de trading y GO190 Store siguen privados,
y los únicos otros proyectos enlazados y accesibles son el portfolio, un
generador de QR y una app del clima — los tres más chicos.

Esto no lo arregla ninguna automatización: es una decisión sobre qué mostrar.

## Lo que sí se puede arreglar escribiendo

- **Ningún repo tiene descripción.** Es la línea que GitHub muestra en el
  listado y en los resultados de búsqueda; vacía, cada repo se ve abandonado.
- **Sin topics.** Son las etiquetas por las que un reclutador filtra
  (`langgraph`, `rag`, `nextjs`).
- **Byte no está enlazado desde el README** aunque sea el proyecto destacado.

## El CV: el número envejeció tres veces

El PDF llegó a decir **"183 tests"** cuando eran 459; después el `cv.md` decía
**459** mientras los HTML decían **479** y el código ya iba por encima de los
ochocientos. Tres mediciones, tres valores distintos, ninguno escrito de mala
fe: cada uno era cierto el día que se tipeó. Ese es el argumento entero de por
qué se mide y no se escribe — y por eso acá no va la cifra de hoy, que
envejecería igual. La dice `perfil/actualizar.py`.

Y había seis copias en tres lugares, todas de distinto tamaño:

| dónde | es | en |
|---|---|---|
| `~/Downloads/cv-elvis/` | 600.026 | 590.759 |
| portfolio `public/` | 555.998 | 547.859 |
| portfolio `public/cv/` | 556.545 | 547.859 |

Más las de Google Drive, que es de donde se manda desde el teléfono. Tamaños
distintos son versiones distintas: no había forma de saber cuál se envió en la
última postulación.

**Por eso el CV se escribe en `cv.md` y los PDF se generan.** Una fuente, y las
copias salen de ella en vez de editarse por separado. `perfil/sincronizar.py`
corrige los números que envejecen solos y reimprime los PDF con el mismo Chrome
con el que se imprimían a mano.
