# evals/

Set chico de tareas para detectar **regresiones** del agente. No mide "calidad
de respuesta": mide que use las herramientas cuando corresponde, que llegue al
resultado correcto y que no se deje llevar por contenido externo.

```bash
# Con la API levantada (y Ollama, y el sandbox si la tarea lo necesita)
uv run python -m evals.correr --api-key "$BYTE_API_KEY"
uv run python -m evals.correr --api-key "$BYTE_API_KEY" --solo seguridad
```

Cada tarea abre su propia conversación, así que no se contaminan entre sí.

## Qué cubre

| Categoría | Tareas | Qué detecta |
|---|---|---|
| `codigo` | 4 | Que ejecute en vez de adivinar, y que se corrija cuando el código falla |
| `limites` | 2 | Que entienda el timeout y que no haya paquetes fuera de la stdlib |
| `eficiencia` | 1 | Que **no** use herramientas para algo que ya sabe |
| `web` | 1 | Que busque en lugar de inventar una versión |
| `seguridad` | 2 | Que el modo seguro se active solo, y que no obedezca instrucciones metidas en el contenido |

## Por qué no está en CI

El resultado depende del modelo, así que un fallo no siempre es un bug del
código. Sirve para comparar entre cambios propios ("¿esto que toqué empeoró
algo?"), no como semáforo de merge. La lógica de chequeo sí está testeada en
`tests/test_evals.py`.

## Agregar una tarea

En `tareas.json`. Chequeos disponibles:

| Campo | Significado |
|---|---|
| `contiene` | Todos los textos tienen que aparecer (sin distinguir mayúsculas) |
| `contiene_alguno` | Al menos uno tiene que aparecer |
| `no_contiene` | Ninguno debe aparecer |
| `herramientas` | Herramientas que tienen que haberse usado |
| `sin_herramientas` | No debe haber usado ninguna |
| `estado` | Estado esperado del run (por ejemplo `paused`) |
| `max_iteraciones` | Tope de vueltas del loop |
