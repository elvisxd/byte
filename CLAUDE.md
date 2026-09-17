# Byte — convenciones

- **Comentarios y mensajes de commit en español.** El código (nombres de
  funciones y variables) también, salvo lo que venga de una librería.
- **Los tests explican por qué existen**, no qué hacen: el nombre dice el
  comportamiento y el docstring, qué se rompería sin ellos.
- `uv run pytest -q` antes de cada commit. `uv run ruff check .` también.
- Nada de `--force` en git, y `main` no se reescribe.

## Cómo se entrega un cambio

**El link del PR va primero, siempre.** Arriba de todo en el mensaje, antes de
cualquier explicación. No al final, no "lo subí a la rama X": el link, que es lo
único con lo que se puede hacer algo.

**Todo va en draft y lo aprueba y mergea Elvis.** Nunca se mergea ni se aprueba
por él, ni siquiera con el CI en verde y sin comentarios. Verde quiere decir
"listo para que lo mires", no "listo para entrar".

Y como el link es lo que hace falta, se pasa aunque el mensaje sea corto, aunque
el cambio sea chico, y aunque en esa misma vuelta se esté contando otra cosa.

**Su infraestructura tampoco se toca sin permiso.** El proyecto tiene servicios
vivos en Railway —`cazador-service`, `vigia-service`— y ahí un redeploy, una
variable o un volumen cambian algo que está corriendo de verdad. Se revisa, se
informa qué se encontró, y se pregunta antes de tocar.

## Este repo es público

Lo que se commitea acá lo lee cualquiera, incluido el reclutador al que se le
manda el link del agente. **La información personal del usuario va oculta**, y
eso no es caso por caso: es cómo se maneja siempre.

- Lo que puede leer un tercero va versionado: el stack, los proyectos, que la
  autorización para trabajar está **vigente**.
- Lo que no, va a `perfil/privado.md`, que está en `.gitignore` y tiene
  plantilla en `privado.ejemplo.md`: fechas de visado, países y fechas de
  mudanzas, documentos, lo que se esté negociando. Byte lo usa para razonar,
  **no para decir**.
- Ante la duda, va a `privado.md`. Sacarlo después no lo borra del historial de
  git ni de lo que alguien ya clonó.

La regla operativa —qué se dice afuera y qué no— está en `perfil/quien-soy.md`,
que entra en el prompt de cada conversación. Antes de agregar un dato personal a
cualquier archivo versionado, mirar ahí. Y si algo tiene que salir en un texto
que va a otra persona, sale como lo diga ese archivo, nunca con el dato crudo.

Aparte de esto, `api/redaccion.py` limpia claves, JWT, correos y teléfonos de lo
que sale hacia Langfuse: son dos capas distintas, una para lo que se commitea y
otra para lo que se manda a un tercero en runtime.

## El agente que opera en papel

`paper/` y `tools/paper.py` son el motor de un experimento de trading **en
papel, sin dinero**: Byte entra y sale sobre cinco hipótesis a la vez, escribe
por qué entra ANTES de saber cómo sale, y publica el historial en un panel web.
El objetivo no es ganar, es saber cuál hipótesis resiste.

**Los datos viven en otro repo**: `elvisxd/mi-dashboard-trading` (privado), en
`scripts/paper/` — velas e indicadores en Node. `paper/mercado.py` es el puente,
y necesita `BYTE_PAPER_SCRIPTS` apuntando ahí o las herramientas no se registran.
El panel que enseña el historial también está en ese repo
(`railway-chart-service`, página `/papel`).

Tres cosas que no son de estilo y tienen pruebas que fallan si se rompen:

- **Los números los calcula el código, nunca el modelo.** El R múltiplo sale de
  `registro.cerrar()`. Ya está medido que un 8B da 1.43 donde el valor real es
  17.35: el modelo elige *cuándo y por qué*, el código calcula *qué pasó*.
- **La razón se sella al entrar** con un hash del contexto. Sin eso, todo el
  ejercicio es un backtest con prosa encima.
- **Los ejes nunca se ordenan por resultado** —ni en `por_eje()`, ni en la foto
  que viaja al panel, ni en la página—. Con pocas operaciones el mejor por azar
  parece bueno. Está argumentado en `paper/CRITERIO_ABORTO.md`, que se
  commiteó **solo y antes que cualquier código** a propósito.

Lo demás está en `paper/README.md` y `paper/EJES.md`.
