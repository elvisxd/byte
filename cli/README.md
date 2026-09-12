# cli/
Cliente de línea de comandos.

- `byte_cli.py` — el CLI. Habla HTTP y nada más: no importa nada de `api/` ni
  de `agent/`, así que sirve igual contra una instancia remota
- `sesion.py` — dónde vive el token de `byte login` y cómo se renueva

Es el CLI definitivo: la reescritura en Go que el plan preveía quedó fuera de
alcance (`docs/plan-asistente-ia-local.md`, Fase 6).

```bash
uv run python -m cli.byte_cli            # la bienvenida: estado y qué podés hacer
uv run python -m cli.byte_cli status    # estado de Byte y sus servicios
uv run python -m cli.byte_cli ask "cómo creo un endpoint en FastAPI"
uv run python -m cli.byte_cli ask --safe "ejecutá código que ..."  # pide confirmación
uv run python -m cli.byte_cli search "última versión de LangGraph" # solo en documentos
uv run python -m cli.byte_cli run script.py   # en el sandbox, sin pasar por el modelo
uv run python -m cli.byte_cli docs add manual.pdf
uv run python -m cli.byte_cli docs list
uv run python -m cli.byte_cli conversations
```

## Entrar como usuario

Sin esto el CLI usa la `BYTE_API_KEY`, que identifica a la **instancia**: lo que
creás es de ella y se ve con esa clave. Con sesión iniciada es tuyo y de nadie
más.

```bash
uv run python -m cli.byte_cli login vos@ejemplo.com   # la contraseña no se ve ni queda en el historial
uv run python -m cli.byte_cli whoami
uv run python -m cli.byte_cli logout
```

El token queda en `~/.config/byte/sesion.json` con permisos 0600 —adentro hay un
refresh que vale 14 días— y se renueva solo cuando el access vence a los 30
minutos, así que no hay que volver a entrar. Hay una sesión por instancia:
apuntar `BYTE_URL` a otra no pisa la local.

`logout` revoca el refresh en el servidor además de borrar el archivo: borrarlo
solo dejaría la sesión viva 14 días para quien tuviera el token.

Para escribir `byte` a secas, un alias en tu shell:

```bash
alias byte='PYTHONPATH=/ruta/a/byte uv run --project /ruta/a/byte python -m cli.byte_cli'
```

El `PYTHONPATH` hace falta: `uv run --project` cambia el entorno pero no el
directorio, así que sin él Python no encuentra el paquete `cli` cuando el
comando se llama desde otra carpeta.

No hay entry point instalado a propósito: el proyecto es `package = false`
(los módulos se importan desde la raíz del repo) y empaquetarlo solo para
ahorrar unas teclas no vale el cambio.

La API key sale de `BYTE_API_KEY`, o del `.env` del proyecto si se corre desde
el repo. `BYTE_URL` apunta a otra instancia (por defecto `localhost:8000`).

Mientras el modelo responde gira un spinner con el tiempo transcurrido; va
sobre stderr, así que no ensucia la salida. Los colores y el spinner se apagan
solos cuando no hay terminal, así que `byte ask ... > archivo` guarda texto
limpio. `byte run` propaga el código de
salida del sandbox, para encadenar en scripts.

## El de Go (Fase 6)
Sigue en pie: mismo mapeo de comandos, cliente generado desde el OpenAPI con
`oapi-codegen`, y el streaming token a token por SSE que este no usa (para un
comando de una sola vuelta, `?wait=true` alcanza). Este CLI existe para poder
usar Byte desde la terminal mientras tanto.
