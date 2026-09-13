# Byte — convenciones

- **Comentarios y mensajes de commit en español.** El código (nombres de
  funciones y variables) también, salvo lo que venga de una librería.
- **Los tests explican por qué existen**, no qué hacen: el nombre dice el
  comportamiento y el docstring, qué se rompería sin ellos.
- `uv run pytest -q` antes de cada commit. `uv run ruff check .` también.
- Nada de `--force` en git, y `main` no se reescribe.
