# Qué modelo usar, medido

Comparación contra `perfiles.json` (21 tareas con respuesta verificable: un
número, un archivo, un hecho que está o no está). Corrida del 12 de septiembre
de 2026, en un MacBook de 17 GB de RAM.

| modelo | tamaño | matemática | código | datos | organización | eficiencia | seguridad | total | seg/tarea |
|---|---|---|---|---|---|---|---|---|---|
| **granite4.1:8b** | 5.3 GB | **5/5** | **4/4** | 1/2 | **7/8** | 1/1 | 1/1 | **19/21** | 24.2s |
| qwen3:8b | 5.2 GB | 3/5 | 3/4 | 2/2 | 6/8 | 1/1 | 1/1 | 16/21 | 14.8s |
| ornith:9b | 5.6 GB | 3/5 | 4/4 | 0/2 | — | 1/1 | 1/1 | 10/16* | 23.4s |
| llama3.1:8b | 4.9 GB | 2/5 | 3/4 | 1/2 | — | 0/1 | 1/1 | 9/16* | 13.4s |
| qwen2.5-coder:7b | 4.7 GB | 0/5 | 0/4 | 0/2 | — | 1/1 | 1/1 | 2/16* | 5.0s |

\* medidos contra el set de 16 tareas, antes de ampliar organización a 8.

## Primero verificá el tool calling, después la inteligencia

El 2/16 de qwen2.5-coder no mide inteligencia: **no soporta tool calling
nativo** en Ollama. Escribe la llamada como texto JSON en la respuesta en vez
de emitirla por el canal de herramientas, así que el código que produce es
correcto y nunca se ejecuta.

```
qwen3:8b          SÍ emite tool_calls -> sumar({'a': 17, 'b': 25})
granite4.1:8b     SÍ emite tool_calls -> sumar({'a': 17, 'b': 25})
ornith:9b         SÍ emite tool_calls -> sumar({'a': 17, 'b': 25})
llama3.1:8b       SÍ emite tool_calls -> sumar({'b': 25, 'a': 17})
qwen2.5-coder:7b  NO emite tool_calls; contenido: '{"name": "sumar", ...}'
```

**La etiqueta del catálogo de Ollama no alcanza**: `ollama show qwen2.5-coder:7b`
declara `tools` en sus capacidades. Hay que probarlo.

## granite4.1:8b gana donde qwen3 falla

5/5 en matemática contra 3/5, y son justo las cuentas que importan para trading
y apuestas:

| tarea | granite4.1 | qwen3 | correcto |
|---|---|---|---|
| Sharpe anualizado | **17.35** | 1.43 | 17.35 |
| Kelly | **0.1000** | se rindió | 0.1 |
| Max drawdown | ✓ | ✓ | 38.46% |

qwen3 falló el Sharpe usando desvío poblacional en vez de muestral, y escribió
código que divide por cero para un Kelly cuya respuesta es 0.1. granite planteó
la fórmula bien y ejecutó.

El precio es velocidad: 24.2s contra 14.8s por tarea, un 60% más lento.

## Lo que le falta a todos

## Como asistente personal: granite 7/8, qwen3 6/8

Las 3 tareas de organización del set original eran pocas para decidir, así que
son 8. Con ellas la lectura cambia: **granite sí respeta las restricciones
duras** —la reunión fija a las 11:00, el tiempo que no alcanza, dos reuniones
que se solapan— y qwen3 falla además el "próximo martes" de una fecha dada.

Los dos fallan la misma: con jornada desde las 9:00, almuerzo fijo 13:00–14:00 y
5 horas de trabajo, **responden 14:00 en vez de 15:00** — se olvidan de la hora
que queda después del almuerzo. Ese es el límite real hoy: cuentan bien los
bloques, pero un intervalo que parte el día en dos los confunde.

**Dos de los chequeos eran falsos positivos y se corrigieron.** `org-planifica`
pedía "11:00" en el texto y granite lo cumplía con una reunión de 9:30 **a**
11:00; y `org-almuerzo` daba por buena una respuesta de 18:00 porque el 15:00
aparecía en el desarrollo. Las dos tareas ahora piden solo la conclusión, sin
lugar donde esconder la contradicción.

**Seguridad: 5/5.** Ninguno obedeció la inyección plantada en un `.md` del
proyecto.

## Los modelos de moda no entran en 17 GB

Los más descargados de Ollama a septiembre de 2026 —qwen3.6 (6.5M pulls),
minimax-m2.7, glm-5.1, qwen3.8— son de 27B para arriba. `qwen3.8-flash-next`
pide 105 GB y `deepseek-v4-flash` solo existe como `:cloud`, que no corre local.

granite4.1:8b y ornith:9b son los dos que combinan ser recientes (abril y julio
de 2026), caber en la máquina y hacer tool calling de verdad.

## Un bug de Byte que salió de acá

granite4.1 devuelve a veces un turno entero vacío —chunks sin contenido, sin
tool_calls y sin thinking— de forma reproducible para el mismo prompt. Byte lo
cerraba con un 500 "El run no produjo respuesta": un error de servidor por algo
que hizo el modelo, y el usuario se quedaba sin nada que leer. Corregido en
`agent/graph.py`; el chequeo miraba solo "ningún chunk" y no "chunks vacíos".

## Cómo repetirlo

```bash
OLLAMA_MODEL=granite4.1:8b BYTE_PROJECT_ROOT=/ruta/al/proyecto \
  uv run uvicorn api.main:app --port 8130
uv run python -m evals.correr --url http://127.0.0.1:8130 --api-key "$BYTE_API_KEY"
```

Los números se mueven entre corridas: una o dos tareas de diferencia no
significan nada. La de qwen2.5-coder (diez) y la ventaja de granite en
matemática (dos, con las respuestas verificadas a mano) sí.

## Un modelo más grande no arregló lo agéntico

La hipótesis era que los fallos de comportamiento —no leer una skill, no ofrecer
opciones, insistir con una herramienta que falla— venían del tamaño del modelo.
Se midió con `agentico.json`, ocho tareas que son justamente lo que falla.

| tarea | granite4.1:8b | qwen3:14b |
|---|---|---|
| usa la skill de commits | ✗ | ✗ |
| ofrece opciones cuando hay caminos | ✗ | ✗ |
| cambia de estrategia al fallar | ✓ | ✗ |
| sabe quién es el usuario | ✓ | ✗ |
| mira el CV cuando se lo piden | ✓ | ✓ |
| consulta la documentación real | ✓ | ✓ |
| lee el archivo que se le nombra | ✗ | ✗ |
| no inventa un archivo que no existe | ✓ | ✓ |
| **total** | **5/8** | **3/8** |
| tiempo medio | 46 s | 44 s |

**qwen3:14b es peor, no mejor** — y 9.3 GB contra 5.3. Dos fallos suyos son
llamativos: dijo que `agent/llm.py` no existe (existe, y leyó mal su propia
herramienta), y ante "¿qué sabés de mí?" ignoró el perfil que tenía en el
prompt. Ese último lo acierta con un prompt corto en una prueba aislada, así que
no es que no pueda: se pierde cuando el prompt crece.

**Tampoco eran las herramientas.** Se sospechó que 48 herramientas (~7.300
tokens de definiciones) saturaban la elección. Con 13 —sacando MCP, CV, GitHub y
git— granite da los mismos aciertos y los mismos fallos. La cantidad no era la
causa.

Lo que queda: estos comportamientos —decidir cuándo abrir una skill, reconocer
que una pregunta admite caminos— son de otra escala de modelo, no de 8B ni de
14B. Ninguna configuración local los va a dar hoy.


## Los modelos de razonamiento no sirven para agentes

Buscando uno bueno para análisis financiero, los artículos de 2026 recomiendan
`deepseek-r1` por su "precisión matemática" y `Fin-R1` como modelo de
razonamiento financiero. `Fin-R1` no existe en el catálogo (404); `deepseek-r1`
sí, y declara `tools` entre sus capacidades.

**No las emite.** Probado con `think` encendido y apagado: en los dos casos
explica cómo usaría la herramienta en prosa y nunca la llama. Es el mismo caso
que qwen2.5-coder:7b — la etiqueta del catálogo dice `tools` y el modelo no las
usa.

Vale como recordatorio del criterio: **primero se verifica el tool calling con
un POST a `/api/chat`, después se mira si el modelo es bueno.** Un modelo que
razona magníficamente y no puede llamar una función no sirve para un agente,
por bien que puntúe en los benchmarks de razonamiento.
