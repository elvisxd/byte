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

Reimprimir los PDF sigue siendo manual: el navegador es lo que da el resultado
que ya te gusta, y automatizarlo con otra herramienta cambiaría el diseño.

## Cómo se mantiene

Los números que envejecen —cantidad de tests, líneas, repos— se sacan del
código con `perfil/actualizar.py`, no se escriben a mano. El CV en PDF decía
"183 tests" cuando ya eran 459: un dato correcto el día que se escribió y falso
tres semanas después.
