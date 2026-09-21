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

**Los PRs propios se mergean en verde, sin preguntar.** Lo decidió Elvis el
2026-09-18 («Merges tu siempre») y lo repitió el 21: con los 6 checks en verde y
sin hilos abiertos, se mergea. Rojo se arregla o se explica en el PR; nunca se
mergea en rojo, y nunca se aprueba ni mergea un PR que no sea propio.

Y como el link es lo que hace falta, se pasa aunque el mensaje sea corto, aunque
el cambio sea chico, y aunque en esa misma vuelta se esté contando otra cosa.

**Railway se puede tocar, con dos límites que siguen siendo de Elvis.** Las
variables de `vigia-service` (`BYTE_REF`, `MODELOS_*`, `LEER`, `RUBRICA`,
`CATALOGO`, `CONTAR`, `PERFIL`) se ponen y se despliega sin preguntar, pero:
un reinicio de los brazos va FUERA de la ventana 08:00–20:30 EDT —dentro le
cuesta una vuelta a cada brazo—, y una variable que cambie la conducta de un
brazo DE LA COMPARACIÓN (gemini, groq: su lista de modelos, su tope de salida,
el prompt) se propone con cifras y no se toca: altera qué mide el experimento.
`BYTE_REF` va siempre a un sha, nunca a `main` (Docker cachea el clone).

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

### Dónde va el experimento — leer antes de tocar nada (2026-09-21)

- **Gemini cruzó las 50 y NO discrimina.** 77 resueltas: Brier 0.2578 contra
  0.2405 de decir siempre la tasa base. Diga 29 %, 47 % o 64 %, el nivel se toca
  ~40 % de las veces. El segundo corte (sin 15m, régimen) lo confirma. Ese 40 %
  no es su acierto: es la tasa con la que los niveles que elige se tocan.
- **Groq va 30/50 (~7 al día). La comparación v4 se lee cuando cruce**, y hasta
  entonces el prompt (`VERSION_PROMPT = 4`) no se toca. Las seis hipótesis de
  mejora están FIJADAS en `paper/HIPOTESIS_PROMPT_V5.md` antes de esa lectura:
  H1 (se ancla en la tasa base) y H5 (revisa ≥3 ejes solo en 8 de 53
  pensamientos) convergen en un solo cambio —las listas de comprobación en prosa
  se saltan; van como campos de `predecir`/`abrir_operacion`—. H3: el régimen
  no aporta (coincidir = peor) y a 31/77 les falta. H6 es de cadencia.
- **Nada se aplica a gemini solo.** Si el prompt cambia, cambia para todos los
  brazos en el mismo commit con `VERSION_PROMPT` +1, y se separan las muestras.
- **Los brazos corren en Railway**, no en la Mac (`railway-vigia-service` del
  dashboard). Su log imprime en cada arranque: `CONTAR` (recuento), `LEER`
  (la lectura de un brazo ≥50, con la puerta de las 50 EN EL CÓDIGO), `RUBRICA`
  (trazas), `PERFIL` (emisión), `CATALOGO` (modelos por brazo). Es el tablero
  cuando el panel no se alcanza.
- **El 413 de Groq es `pedido = entrada + salida anterior de ESE modelo`**, no
  un tope de tamaño ni el reloj — medido tres veces con aritmética exacta
  (2026-09-20/21). Se reintenta el mismo modelo recortando el historial
  (`agent/relevo.py`, `recortar_entrada`, byte#150). Un 413, 429 o 503 NUNCA
  entra en `agent/catalogo.py`: solo el 404 y el 402 son veredictos del modelo.
- **`z-ai/glm-5.2:free` no tiene herramientas** (404 «no endpoints found that
  support tool use»): no vuelve. Cualquier id de OpenRouter sin `:free` se
  cobra y el brazo se niega a llamarlo (`paper/sesion.py`).
- Lo que el log de Railway enseña por predicción resuelta lleva el Brier
  (`paper/sesion.py:393`); no se suma a mano antes de las 50 de cada brazo.
