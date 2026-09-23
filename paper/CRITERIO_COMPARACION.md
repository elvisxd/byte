# Criterio de la comparación entre modelos

Escrito el 2026-09-15, ANTES del código que lo aplica y en su propio commit.
Igual que `CRITERIO_ABORTO.md`, `CRITERIO_CADENCIA.md` y `CRITERIO_GESTION.md`:
si el criterio se escribe después de ver los resultados, no es un criterio, es
una justificación.

## Por qué comparar

Todo lo medido hasta hoy se midió con UN modelo, `qwen3:14b` con razonamiento,
en una Mac de 16 GB donde no cabe nada más grande. Cuando el agente insiste en
un nivel ya ocupado, mezcla marcos o cierra una tesis de 4h a los 17 minutos,
no hay forma de saber si el techo es el modelo o es lo que le damos a mirar.
Las dos explicaciones piden trabajos distintos —cambiar de modelo, o preparar
mejor el gráfico— y hasta ahora se elegía a ciegas.

Un segundo modelo con EL MISMO prompt, LAS MISMAS herramientas y LOS MISMOS
eventos separa las dos cosas: lo que falla en los dos es del gráfico o del
prompt; lo que falla en uno solo es del modelo.

## Qué se compara, y qué se mantiene igual

- **Mismo prompt** (`INSTRUCCION` de `paper/sesion.py`), sin adaptaciones por
  modelo. Adaptar el prompt a cada uno mediría dos prompts, no dos modelos.
- **Mismas herramientas** y mismos números: el mapa, las métricas, los
  rechazos. El modelo elige cuándo y por qué; el resto es aritmética.
- **Mismos eventos**: los dos brazos corren en modo vigía sobre el mismo
  mercado, con la misma ventana y el mismo tope diario. Se despiertan por los
  mismos cierres de 4h y las mismas cercanías a niveles vivos. Las cercanías
  a SUS PROPIAS órdenes y predicciones difieren por construcción, y eso es
  parte del modelo, no un sesgo.
- **Registro propio por brazo**: `operaciones.db` para el local,
  `operaciones-gemini.db` para el remoto. Dos modelos sobre un mismo registro
  se pisan las predicciones —«ya hay una viva a 1 ATR»— y ninguno de los dos
  lee el estado que él mismo dejó.
- **Columna `modelo`** sellada en cada operación y predicción, como ya se hace
  con `+razona`. Es lo único que permite separar los brazos si alguien junta
  las bases dentro de un mes.

## El brazo remoto y la rotación de modelos

El brazo remoto usa la capa gratuita de la API de Gemini. Esa capa tiene
límites por minuto y por día que Google no publica por modelo, y que además
cambian: un modelo puede contestar a las 08:00 y devolver 429 a las 14:00, y
`gemini-3.8-flash` devolvió 503 «high demand» en la primera sonda.

Por eso el brazo no es UN modelo sino una LISTA ordenada, y ante un 429 o un
503 se pasa al siguiente en la misma vuelta, sin perderla. El orden lo fija
quien lanza, de más a menos capaz según las sondas del 2026-09-15:

    gemini-3.8-flash › 3.7-flash › 3.5-flash › 3-flash-preview ›
    3.5-flash-lite › 3.1-flash-lite

`3.6-flash` va fuera: filtró borradores de su pensamiento a la respuesta
(«Check Draft 1/…»). `gemma-4-26b` también: contesta con ecuaciones a una
pregunta que pedía una línea. Los Pro no entran en la capa gratuita.

⚠ CADA OPERACIÓN LLEVA EL MODELO QUE LA ESCRIBIÓ, NO EL PRIMERO DE LA LISTA.
Si el 3.8 se agotó a media mañana y el 3.5-lite abrió la operación de la
tarde, decir que la abrió «el brazo Gemini» mezcla dos modelos de calidad
distinta bajo un nombre. Es la misma razón por la que `+razona` va en la
etiqueta del local.

⚠ ENTRE LLAMADAS, UNA ESPERA MÍNIMA. Una vuelta son hasta seis llamadas en
segundos; la capa gratuita tiene un tope por minuto. Se espacian a propósito
—unos segundos entre una y otra— para no rotar de modelo por un tope de
minuto que no dice nada del modelo.

## Qué se mide, y cómo

Lo mismo que ya mide el experimento, brazo por brazo, con la tabla de
`predicciones` y `por_eje()`:

1. **Brier por marco** y cuántas predicciones resolvió cada uno. Es el
   criterio principal: la predicción se hace en TODAS las vueltas y no depende
   de que haya entrada.
2. **R por motivo de cierre** (`stop`, `objetivo`, `manual`, `tiempo`), sin
   promediar ejes entre sí.
3. **Rúbrica sobre las trazas**, a mano y con las dos trazas delante:
   - ¿reacciona a un rechazo bajando de marco, o insiste?
   - ¿las razones recorren los ejes o son una plantilla? (razones distintas
     sobre razones totales)
   - ¿cuántas vueltas chocan con el tope de iteraciones sin registrar nada?
   - ¿juzga una abierta en su marco, o la cierra mirando 15m?

## Qué NO se hace

- **No se elige el brazo mirando la tabla.** Con veinte predicciones el mejor
  por azar parece bueno. La decisión se toma con **50 predicciones resueltas
  por brazo**, y hasta entonces los dos corren.
- **No se ordena por resultado** en ningún sitio: ni los ejes, ni los brazos.
- **El brazo remoto no publica al panel.** El panel enseña UN historial y UNA
  traza; dos brazos publicando alternados dejarían la página contando una
  historia con dos narradores. Su traza va a archivo local
  (`paper/trazas/`), y el historial vive en su base. Cuando haya algo que
  enseñar, será una página que sepa que hay dos.
- **No se le adapta nada al modelo remoto** por el camino. Si el prompt cambia,
  cambia para todos y en el mismo commit, y sube `VERSION_PROMPT`
  (`paper/prompt.py`): cada escritura la lleva sellada en su contexto, así
  que las muestras de antes y de después se pueden separar. Lo que no se
  hace es comparar un brazo con el prompt 1 contra otro con el 2.

## Qué decide

Tres desenlaces posibles a las 50 predicciones por brazo:

- **Los dos fallan en lo mismo** (insisten, mezclan marcos, cierran antes de
  tiempo): el problema es lo que se les da a mirar. El trabajo siguiente es el
  gráfico —FVGs, liquidez, estructura preparada— y no el modelo.
- **Uno falla y el otro no**: el problema es el modelo, y ya se sabe cuál
  sirve. Si es el remoto, se decide si pagar por él con números delante.
- **Ninguno resuelve mejor que el otro pero los dos resuelven**: la
  diferencia es coste y velocidad, y la Mac gana por ser gratis y local.

## 2026-09-17: los brazos remotos se mudan a la nube, y eso cambia UNA cosa

Desde hoy `gemini` y `groq` corren en Railway (`railway-vigia-service/` del repo
del dashboard), no en la Mac. El motivo es que nunca la necesitaron —van por
API, sin GPU y sin Ollama— y sí la sufrían: con la Mac dormida de noche y
apagada cuando el usuario no está, cada ausencia se llevaba los tres brazos.

Esto **no** es una adaptación al modelo remoto de las que este criterio
prohíbe, y no toca nada de lo que se compara: mismo prompt, mismas
herramientas, mismos eventos, misma ventana (08:00–20:30), mismo tope diario,
registro propio por brazo y cada operación sellada con el modelo que la
escribió. Lo que cambia es qué máquina hace la llamada HTTP, y eso el modelo no
lo ve.

⚠ **LO QUE SÍ CAMBIA ES LA DISPONIBILIDAD, Y SE DECLARA EN VEZ DE DISIMULARSE.**
Hasta hoy los tres brazos compartían las ausencias de la Mac: cuando ella no
estaba, no había muestra de ninguno, así que los tres cubrían exactamente los
mismos días. Desde hoy el local sigue atado a la Mac y los remotos no, o sea
que **habrá días con muestra remota y sin muestra local**.

Eso obliga a algo al comparar, y es lo único que obliga: **la comparación se
hace sobre los días en que los DOS brazos escribieron**, no sobre todo lo que
haya en cada base. La fecha de cada fila (`hecha_en` en `predicciones`) es lo
que permite hacer el corte, y las 50 predicciones del umbral se cuentan sobre
ese subconjunto. Lo que sobre del brazo remoto no se tira —es muestra buena
para mirar al modelo por su cuenta— pero no entra en el contraste entre brazos.

Se consideró y se descartó la alternativa: encender los brazos remotos solo
cuando la Mac está apagada. Sería peor, no mejor — los días de uno serían
exactamente los que le faltan al otro y no habría ni un día en común que
comparar.

⚠ **Y LA ZONA HORARIA DEL CONTENEDOR ES PARTE DEL EXPERIMENTO.** `vigia.py` abre
y cierra su jornada con `datetime.now()`, la hora de SU máquina. Un contenedor
en UTC correría la ventana desplazada respecto a la Mac y los brazos dejarían de
despertarse por los mismos cierres de 4h, que es lo primero que este criterio
exige mantener igual. El servicio lleva `TZ` fijada a la de la Mac; si alguien
la cambia, cambia los eventos.

## 2026-09-18: los brazos no emiten igual, y eso obliga a un SEGUNDO corte

Salió de una sospecha del usuario —«Gemini está sobre-tradeando»— y se midió con
`railway-vigia-service/perfil.py` del repo del dashboard, que cuenta emisión sin
leer `ocurrio` ni `brier` ni `precio_al_cerrar`: por eso se pudo correr con Gemini
en 42 resueltas sin romper la regla de no mirar antes de las 50.

Medido sobre 53 predicciones de `gemini` y 18 de `groq`, mismos días:

| | gemini | groq |
|---|---|---|
| predicciones · por día | 53 · 13,2 | 18 · 4,5 |
| por vuelta (máximo) | 1,3 (2) | 1,0 (1) |
| operaciones abiertas | **2** | **8** |
| 15m / 1h / 4h | **53% / 36% / 11%** | 39% / 33% / **28%** |
| confianza dominante | **0,2–0,4 (36%)** | 0,4–0,6 (44%) |
| discrepa del régimen medido | **45%** | **6%** |

⚠ **NO ES QUE UNO SEA PEOR: ES QUE NO ESTÁN HACIENDO LA MISMA AFIRMACIÓN, Y EL
BRIER NO LO SABE.** El patrón dominante de Gemini es «15 minutos + probabilidad
baja», o sea *no va a tocar ese nivel en un cuarto de hora*, que es casi siempre
verdad por física del precio. Diecinueve predicciones así resuelven bien y dejan
un Brier excelente sin haber demostrado nada. Groq emite menos, más repartido
hacia 4h y con la confianza en el centro: menos afirmaciones, más comprometidas.
Comparar los dos Briers al llegar cada uno a 50 resueltas compararía a un
scalper con un analista y llamaría ganador al que eligió las preguntas fáciles.

⚠ **Y LA DIRECCIÓN DEL «SOBRE-TRADEO» SE INVIERTE SEGÚN QUÉ SE CUENTE.** Gemini
emite 3× más predicciones; Groq abre 4× más operaciones (8 contra 2). Quien
pronostica de más y quien opera de más son brazos distintos. La palabra sola no
dice nada; el número sí.

### Lo que esto obliga

La comparación de las 50 se hace igual sobre el total —es lo que este criterio
congeló y no se cambia a posteriori— pero **se reporta también sobre un
subconjunto filtrado**, y las dos cifras se ponen una al lado de la otra:

- **fuera las de 15m**, que son otra cosa: quedan 25 de las 53 de Gemini.
- **solo donde `regimen_medido` y `regimen_dicho` coinciden**: 29 de 53. El
  esquema los guarda separados justamente «porque la pregunta interesante es si
  acierta más cuando coinciden».

Si el total y el filtrado dicen lo mismo, la conclusión es robusta. **Si
discrepan, esa discrepancia ES el resultado**: significa que el brazo que gana lo
hace por el tipo de pregunta que elige y no por acertar mejor, y eso decide
distinto que un Brier a secas.

### Dos avisos sobre estos números

⚠ **EL RITMO ABSOLUTO ESTÁ INFLADO Y LA CULPA ES DEL OPERADOR, NO DEL MODELO.**
El 2026-09-17 y el 18 el servicio se reinició muchas veces (brazos nuevos, sondeos
de proveedores, cambios de imagen). Cada arranque dispara una «vuelta de lectura»
que **no cuenta para el tope de 8 diarias**, así que 13,2 por día es imposible sin
esos extras: 8 vueltas × 1,3 por vuelta ≈ 10. **La proporción entre brazos sí
aguanta**, porque los reinicios les pegaron a los cuatro por igual. Quien repita
esta medición en una semana limpia obtendrá el ritmo de verdad.

⚠ **Y EL BRAZO `gemini` NO ESTÁ MIDIENDO A GEMINI-3.8.** Quién firmó sus 53
predicciones: `3-flash-preview` 40%, `3.5-flash` 38%, `3.5-flash-lite` 13%,
`3.7-flash` 8%, y **`3.8-flash` UNA (2%)**. Entre `reservar_primero` —que lo
guarda para los cierres de 4h— y sus 503 constantes, el primero de la lista casi
no ha escrito. `groq` en cambio es 67% su `120b`. Así que lo que salga a las 50 es
el Brier de *la lista* de Gemini tal como el relevo la recorre, no el de su mejor
modelo, y el informe tiene que decirlo con esa letra.

## 2026-09-18: el catálogo de modelos, y lo que sí toca a la comparación

`agent/catalogo.py` guarda en el volumen la ficha de cada modelo de cada
proveedor: qué lista su `/v1/models`, qué contestó una vuelta de verdad y qué
quedó descartado y por qué. Antes eso vivía en memoria y se perdía en cada
reinicio.

Lo hizo falta un caso concreto. El 2026-09-18 a las 12:00 EDT el brazo openrouter
recibió de `z-ai/glm-5.2:free` un **404 «No endpoints found that support tool
use»**: un modelo sin llamada a herramientas, que es lo único que este agente
hace. No es cuota ni carga: no va a servir nunca. El relevo lo mandó a cuarentena
permanente —bien— pero esa cuarentena dice literalmente «no vuelve en esta
sesión», así que el reinicio siguiente lo habría reintentado. Lo quitó una
persona a mano.

### Qué entra en el catálogo, y qué no

Solo lo estructural: el 404 y el 402 que el relevo ya separa como `permanente`.
**Un 429 es cuota, un 503 es carga ajena y un 413 es el presupuesto por minuto
del proveedor**, y ninguno dice nada del modelo. El brazo `groq` da varios 413
al día y es el brazo con más muestra: anotarlo como descartado lo habría borrado
del relevo por ser el que más trabaja. Hay un test por cada uno de esos tres
códigos.

⚠ **CORRECCIÓN DEL MISMO DÍA: el 413 NO es el tamaño de la petición**, y esta
sección decía que sí. Medido el 2026-09-18 entre las 15:12 y las 15:17 EDT:

```
15:17:35  20b   contesta   7283 de entrada
15:17:42  20b   413                         ← 7 s después, mismo modelo
15:17:45  120b  contesta   7604 de entrada   ← MÁS grande, y pasa
```

Lo que discrimina es cuántos tokens lleva gastados el minuto, **y la salida
cuenta**: en esa misma vuelta una sola llamada gastó 6.097 tokens de salida —el
76% de los 8.000 del minuto— mientras las otras gastaron entre 505 y 1.373.

Esto cambia dos conclusiones. La primera: **esperar SÍ lo arregla**, al contrario
de lo que decía `relevo.py`; la espera corta del relevo es precisamente lo que
salvó esa vuelta. La segunda: **acortar el prompt no es el arreglo**, o no el
principal — lo que se come el presupuesto de ese brazo es una salida ocasional
que se desborda, no la entrada, que lleva días estable entre 6k y 7,6k.

Lo que lo escondió fue el log: `_agotar` recortaba el error a 80 caracteres y
«tokens per minute (TPM): Limit 8000, Requested N» cae pasado ese corte. Ahora
las cifras de presupuesto se imprimen enteras.

Y dentro de lo permanente hay grados, porque la clasificación es asimétrica:
`sin_herramientas` no se revisa nunca, `sin_acceso` se revisa al mes (puede ser
un typo o un catálogo que cambió), `hay_que_pagar` espera a que cambie la cuenta.
Cualquier 404 que no coincida con una frase conocida cae en `sin_acceso`, o sea
en el lado revisable: **equivocarse hacia «revisable» cuesta una petición al mes;
equivocarse hacia «nunca» tira un modelo que funcionaba y nadie se enteraría**.

### Lo que esto sí le cambia a la comparación

El filtro se aplica a los seis brazos, `gemini` y `groq` incluidos, y eso hay que
justificarlo porque el criterio dice que su conducta no se toca a mitad de
muestra. Se aplica porque **lo único que quita son modelos que no pueden
contestar**: ninguna predicción que habría existido se pierde. Lo que cambia es
una sola cosa, y en la dirección que este criterio ya pedía.

`Relevo._disponibles` aplica la reserva de `CRITERIO_HORARIOS.md` —guardar el
primero de la lista para los cierres de 4h— solo cuando el primero de los VIVOS
sigue siendo el primero de la LISTA. Con el primero en cuarentena permanente eso
no se cumple nunca, así que el modelo bueno se gastaba en vueltas de gestión.
Filtrando la lista antes de construir el relevo, el primero vuelve a ser un
modelo que existe y la reserva vuelve a funcionar.

⚠ **Y SI EL CATÁLOGO DESCARTA A TODOS, MANDA LA LISTA ORIGINAL.** Una ficha vieja
—un 404 puntual clasificado como `sin_acceso`— dejaría al brazo sin una sola
vuelta en el día, que es peor que gastar una llamada comprobándolo. Con la lista
entera el relevo reintenta, y si de verdad están todos rotos su propio error ya
dice qué hacer.

### El sufijo `:free`, ahora en código

La lista de cada brazo entra por una variable de entorno de Railway
(`MODELOS_OPENROUTER`), así que un id al que se le caiga el `:free` al editarla es
una petición **cobrada** sin que falle nada: el mismo id sin sufijo existe y
contesta 200. Hasta hoy la regla solo la comprobaba el sondeo del dashboard.
Ahora el brazo se niega a llamarlo, y si eran todos, se niega a arrancar: un brazo
muerto se ve en el log y una factura no.

## 2026-09-21: el 413 de Groq se resuelve recortando la vuelta, y eso es un régimen declarado

La corrección del 18 se quedó corta, y hay que decirlo con las cifras que la
refutan. Dije que el 413 era el reloj y que «esperar sí lo arregla». Con el log
ampliado (byte#126) se ve lo que de verdad cuenta Groq:

```
12:18:39  120b  contesta   7187 de entrada / 1756 de salida
12:19:35  120b  413  «Limit 8000, Requested 9266»
12:19:36  20b   contesta   7510 de entrada        ← la MISMA petición
```

9266 − 7510 = **1756: la salida anterior del 120b, exacta.** Groq le suma a
cada petición la última salida de ESE modelo. El `history_budget` del grafo es
12288 × 0,6 = 7372 tokens —dimensionado para el contexto del LOCAL— y
`groq_num_predict` es 8192, así que el choque estaba garantizado: un
razonamiento de 8144 del 20b (12:03) dejó la siguiente en «Requested 15778» y
**la vuelta 11 se perdió entera**; la 15 se perdió el 21 a las 20:07, justo tras
el cierre de 4h. El viernes «ninguna vuelta se perdía»; el sábado ya sí.

### Lo que se hace, y por qué no es una adaptación al modelo

Un 413 que trae cifras se reintenta **una vez, el mismo modelo**, con el
excedente (`pedido − tope` + margen) quitado del historial de la vuelta: los
pasos más viejos de esa vuelta, del más antiguo al más nuevo, cada AIMessage con
sus ToolMessages enteros. **No se toca el system, ni el primer mensaje humano
—el mapa del mercado—, ni el último paso.** Si con eso no alcanza, rota como
antes. Es lo que el usuario propuso el 18: «acortar antes de descartar».

Esto no viola «no se le adapta nada al modelo remoto por el camino», y hay que
decir exactamente por qué:

- **Lo impone el proveedor, no lo elige el experimento.** Solo se dispara cuando
  Groq rechaza la petición; gemini nunca lo ve porque su contexto es mayor. La
  alternativa no es «la misma vuelta sin recorte»: es **ninguna vuelta**, y una
  muestra que pierde justo las vueltas largas —las de más pasos, las de los
  cierres— está más sesgada que una con las vueltas largas recortadas.
- **Se declara, no se disimula.** El relevo imprime cada recorte con su tamaño y
  lleva la cuenta (`recortes_413`). Cuando el brazo groq llegue a las 50, el
  informe tiene que decir cuántas de sus vueltas fueron recortadas y de cuánto.
- **No cambia el prompt ni el orden de los ejes**: quita pasos ya hechos de la
  misma vuelta, que el modelo ya vio y cuyo resultado ya está en el historial
  más reciente cuando importa.

### Lo que NO se hace todavía, y es decisión del usuario

Bajar `groq_num_predict` (8192) acotaría la salida que Groq suma después y haría
el recorte casi innecesario, pero cambia una variable de un brazo de la
comparación con gemini ya en el umbral: se propone con las cifras y no se toca.

## 2026-09-23: prompt v5 para los cuatro brazos, y la comparación v4 se cierra donde estaba

Decisión del usuario, no del criterio: «quisiera aplicarlas todas». Las seis
mejoras de paper/HIPOTESIS_PROMPT_V5.md más las de la revisión de trucos de
prompt del mismo día entran en `VERSION_PROMPT = "5"` (paper/prompt.py), en un
solo commit y para gemini, groq, openrouter y nvidia a la vez. Lo que eso
cambia de la comparación, dicho con las cifras:

- **La muestra v4 de groq queda sellada donde estaba** (33 resueltas el 21 a
  las 06:57 EDT; el recuento de esta noche dice dónde llegó). No cruza las 50 en
  v4 y **la comparación gemini-groq sobre v4 no se lee nunca**: gemini v4 (65
  resueltas, Brier 0.2664 contra 0.2400 del ingenuo) queda como línea base de
  gemini, no como comparación. Se dice para que nadie la busque.
- **La comparación v5 arranca en cero para los cuatro a la vez**, que es más
  limpio que lo que había —gemini llevaba 30 de ventaja—. La puerta sigue siendo
  50 resueltas **por brazo y por versión**: el resumen que va al panel
  (`paper/comparar.py`, `por_version`) y la lectura del arranque (`leer.py`)
  parten por versión, y el panel enseña v4 y v5 en columnas aparte.
- **Cada cambio se puede medir por separado**, porque cada uno deja su rastro en
  el sello (`extra`): `revision` (tasa base leída, ajuste, sí/no por eje,
  contra), `analista` (los niveles, las muestras y su media) y
  `calibracion_vista`. La pregunta «¿aporta la media del analista sobre el
  número del trader?» se contesta con `por_version.analista` sin haberlos
  mezclado: el Brier de uno y del otro sobre las mismas predicciones.

### El presupuesto de Groq manda sobre el tamaño del prompt

Groq rechaza cualquier pedido de más de 8000 tokens y el de v4 ya llegaba a
7004-7510 de entrada (2026-09-21). Así que **v5 no es más largo que v4**: la
instrucción pasa de 8388 a 8245 caracteres (se acortó lo que los campos ahora
exigen), las descripciones de las herramientas se recortaron para pagar los
cinco campos nuevos, y la lectura del analista se corta a 1100 caracteres. Lo
que sí crece es el número de LLAMADAS por vuelta: una lectura más
`BYTE_MUESTRAS_ANALISTA − 1` muestras (3 por defecto) antes del turno del
trader, cada una con el mapa entero. En Groq son minutos (60 s entre llamadas);
el usuario lo aceptó desde el principio («no importa que tarde»). La lectura de
esta noche tiene que mirar las cifras de «de entrada» de groq en el log: si
pasan de 8000, el recorte del 413 no puede con un primer mensaje y hay que
acortar más.

### Lo que sigue igual

Nada de esto cambia el tope diario, la ventana, la cadencia ni las listas de
modelos. H6 (los cierres de 4h y los plazos fuera del tope) sigue propuesta en
CRITERIO_CADENCIA.md y la decide el usuario.
