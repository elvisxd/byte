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
