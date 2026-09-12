# perfil/

Lo que Byte sabe de vos: el CV, los proyectos y el estado del GitHub. Es la
base de la que salen las respuestas cuando le pedís adaptar el CV a una oferta,
escribir el README de un repo o revisar el perfil.

**Por qué en archivos y no en la base de datos.** El agente ya sabe leer
archivos (`read_file`, `grep`), así que esto no necesita herramientas nuevas; y
un archivo se versiona con git, se revisa en un diff y se corrige a mano cuando
el modelo se equivoca. Una fila en Postgres no tiene nada de eso.

**El CV es tuyo, no del modelo.** Byte propone y mide; lo que se manda a una
empresa lo aprobás vos. Nada acá se publica ni se envía solo.

## Archivos

| | qué es |
|---|---|
| `cv.md` | El CV en texto, la fuente de verdad. Los PDF salen de acá. |
| `proyectos.md` | Un proyecto por bloque: qué es, qué prueba, números reales. |
| `github.md` | Estado del perfil: qué repo es público, cuál tiene descripción. |

## El flujo real del CV

El CV vive en `~/Downloads/cv-elvis/`:

```
build/cv-en.html  ─┐  se abren en el navegador
build/cv-es.html  ─┘  → Imprimir → Guardar como PDF
                          ↓
            Elvis-Pino-CV-{en,es}.pdf
                          ↓
        portfolio/public/  +  Google Drive (desde donde se manda por teléfono)
```

Los HTML están escritos a mano, así que un dato que cambia hay que corregirlo en
dos archivos y reimprimir dos PDF. Por eso el CV decía "183 tests" cuando ya
eran 459, y por eso hay seis copias de distinto tamaño en tres lugares.

`perfil/sincronizar.py` cierra esa parte: compara los números del CV con los del
código y los corrige. No reescribe el CV —eso es tuyo—, solo los números que
envejecen solos, diciendo exactamente qué cambió.

```bash
uv run python perfil/sincronizar.py            # dice qué está viejo
uv run python perfil/sincronizar.py --aplicar  # lo corrige
```

Los PDF se generan con **el mismo Chrome** con el que se imprimían a mano, en
modo headless — no es otra herramienta que podría cambiar el diseño, es el mismo
motor. El generado sale de 590.760 bytes contra los 590.759 del impreso a mano:
un byte de metadata de fecha, las mismas 4 páginas y la misma foto.

Y se copian solos a `portfolio/public/`. Lo único que queda en tus manos es
revisarlos y hacer el commit del portfolio, que es una decisión de publicar.

Y a Google Drive, que es de donde salen los CV que se mandan desde el teléfono
— por eso son los que más importa que estén al día, y los que más fácil quedan
viejos. Si Drive no está montado, se saltea sin fallar.

En Drive todo vive en `Mi unidad/Currículum ` —con el espacio final, así se
llama la carpeta—: los dos CV, la carta de presentación, y una subcarpeta
`anteriores/` con las nueve versiones viejas que estaban sueltas en la raíz.

Movidas, no borradas: alguna podría estar compartida por link con alguien, y
romper ese enlace sería peor que el desorden. Los `.gdoc` tampoco se tocaron —
son punteros a documentos de Google Docs, no archivos.

## Cómo se mantiene

Los números que envejecen —cantidad de tests, líneas, repos— se sacan del
código con `perfil/actualizar.py`, no se escriben a mano. El CV en PDF decía
"183 tests" cuando ya eran 459: un dato correcto el día que se escribió y falso
tres semanas después.
