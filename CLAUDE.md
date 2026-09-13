# Byte — convenciones

- **Comentarios y mensajes de commit en español.** El código (nombres de
  funciones y variables) también, salvo lo que venga de una librería.
- **Los tests explican por qué existen**, no qué hacen: el nombre dice el
  comportamiento y el docstring, qué se rompería sin ellos.
- `uv run pytest -q` antes de cada commit. `uv run ruff check .` también.
- Nada de `--force` en git, y `main` no se reescribe.

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
