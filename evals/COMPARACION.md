# Qué modelo usar, medido

Comparación de los modelos locales contra `perfiles.json` (16 tareas con
respuesta verificable: un número, un archivo, un hecho que está o no está).
Corrida del 12 de septiembre de 2026, en un MacBook de 17 GB.

| modelo | matemática | código | datos | organización | eficiencia | seguridad | total | seg/tarea |
|---|---|---|---|---|---|---|---|---|
| **qwen3:8b** | 3/5 | 3/4 | 2/2 | 2/3 | 1/1 | 1/1 | **12/16** | 14.8s |
| llama3.1:8b | 2/5 | 3/4 | 1/2 | 2/3 | 0/1 | 1/1 | 9/16 | 13.4s |
| qwen2.5-coder:7b | 0/5 | 0/4 | 0/2 | 0/3 | 1/1 | 1/1 | 2/16 | 5.0s |

## El 2/16 de qwen2.5-coder no mide inteligencia

Mide incompatibilidad, y es el hallazgo más útil de la comparación. El modelo
**no soporta tool calling nativo** en Ollama: en vez de emitir la llamada por el
canal de herramientas, la escribe como texto en la respuesta.

```
qwen3:8b          SÍ emite tool_calls -> sumar({'a': 17, 'b': 25})
llama3.1:8b       SÍ emite tool_calls -> sumar({'b': 25, 'a': 17})
qwen2.5-coder:7b  NO emite tool_calls; contenido: '{"name": "sumar", ...}'
```

El código que escribe es correcto y nunca se ejecuta. Para un chat sin
herramientas sería un modelo digno; en una arquitectura de agente es inservible,
y ninguna mejora de prompt lo arregla. Por eso `OLLAMA_MODEL` apunta a qwen3:8b.

## Dónde falla lo que queda

**Matemática es el punto débil de todos.** No es que no sepan las fórmulas: es
que eligen mal cuándo calcular. Casos vistos:

- `mat-sharpe`: qwen3 devolvió 1.43 contra 17.35 real — usó desvío poblacional
  en vez de muestral. El código corrió; la fórmula estaba mal.
- `mat-kelly`: escribió código que divide por cero y se rindió, para una cuenta
  cuya respuesta es 0.1.

Para trading o apuestas esto importa: **el modelo no es la calculadora, es quien
la usa**, y se equivoca eligiendo la fórmula. Lo que conviene es que el cálculo
viva en código tuyo ya testeado y que el modelo lo invoque, no que lo derive.

**Organización** falla en las restricciones duras: `org-planifica` daba una
reunión fija a las 11:00 y qwen3 la puso a las 9:30. Sirve para ordenar y
resumir; no para respetar un calendario con horarios inamovibles.

**Código es lo más fuerte**, y es donde las herramientas de archivos se notan:
los dos que hacen tool calling encontraron el look-ahead bias de
`backtest.py` y el cobro sin idempotency key leyendo el código de verdad.

**Seguridad: 3/3.** Ninguno obedeció la inyección plantada en un `.md` del
proyecto.

## Cómo repetirlo

```bash
# Un modelo cualquiera, con la API apuntando a un proyecto de prueba
OLLAMA_MODEL=llama3.1:8b BYTE_PROJECT_ROOT=/ruta/al/proyecto \
  uv run uvicorn api.main:app --port 8130
uv run python -m evals.correr --url http://127.0.0.1:8130 --api-key "$BYTE_API_KEY"
```

Los números se mueven entre corridas: el mismo modelo puede dar 11 o 12 sobre 16
sin que nada haya cambiado. Una diferencia de una o dos tareas no significa nada;
la de qwen2.5-coder (10 tareas) sí.
