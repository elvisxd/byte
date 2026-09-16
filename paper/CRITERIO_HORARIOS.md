# Criterio de horarios de los brazos

Escrito el 2026-09-15, ANTES del código que lo aplique y en su propio commit.
Igual que los demás `CRITERIO_*.md`: si se escribe después de ver el resultado,
no es un criterio, es una justificación.

## El problema que resuelve

Los brazos remotos van con capa gratuita, y la capa gratuita se acaba. Medido
hoy, primer día con tres brazos:

| brazo | modelo | cuándo se agotó | por qué |
|---|---|---|---|
| Gemini | 3.8-flash, 3.7-flash, 3.5-flash | antes de las 11:00 | peticiones/día (~20 por modelo); se consumieron en las sesiones de la mañana |
| Gemini | 3-flash-preview | intermitente | 503 «high demand» en hora punta de EE. UU. |
| Groq | gpt-oss-120b | 17:34 | tokens/día, tras ~3 vueltas de 5-6k tokens por llamada |
| Groq | gpt-oss-20b | 20:06 (un minuto) | tokens/minuto, al quedarse solo y encadenar 4 llamadas |

El resultado es que **la vuelta más importante del día —el cierre de 4h de las
20:00— la hizo el modelo más flojo de cada lista, o nadie**: en Gemini el
3.5-flash-lite, en Groq nadie. Y las vueltas de la mañana, que fueron de
lectura o de niveles cercanos, se las llevaron los modelos buenos.

Cada operación lleva el modelo que la escribió, así que la comparación no
miente. Pero sí mide algo distinto de lo que se quería: no «Gemini contra
Groq contra local», sino «lo que quedaba de Gemini a esa hora».

## La regla

**El modelo mejor de cada lista se reserva para las vueltas que más valen, y
las demás vueltas van al siguiente.** Concretamente:

1. Las vueltas de **cierre de 4h** (08:00, 12:00, 16:00, 20:00 locales) las
   abre el primero de la lista que esté disponible. Son las cuatro lecturas de
   estructura del día y las únicas que el criterio de cadencia llama «de
   estructura».
2. Las vueltas por **cercanía a un nivel** o **posición cerca del stop** las
   abre el segundo modelo de la lista en adelante, aunque el primero esté
   disponible. Son las de gestión, y hoy son la mayoría.
3. Si el primero ya se agotó cuando llega un cierre de 4h, sigue el siguiente,
   como ahora. La reserva evita gastarlo antes; no lo resucita.

Con esto, un modelo con ~20 peticiones al día llega a las cuatro vueltas de
estructura (4 × ~5 llamadas) sin agotarse por el camino.

## Por qué se aplica desde hoy, y qué lo revisaría

La regla se aplica **desde el primer día**, sin esperar más medida: lo de
hoy no es una muestra chica de un fenómeno dudoso, es la mecánica de la
cuota. Un modelo con ~20 peticiones al día y vueltas de 5-6 llamadas se
acaba en 3-4 vueltas haga lo que haga el mercado, y las vueltas de la mañana
son las de menos valor (lectura de arranque, niveles cercanos). Esperar dos
días para confirmarlo serían dos cierres de las 20:00 más hechos por el lite
o por nadie. Y **sin tokens no hay experimento**: lo que no se mida por falta
de cuota no se recupera.

Lo que sí se mide desde el commit hermano —cada línea `[relevo]` con hora, y
la cuarentena por modelo en `apis.modelos` de `/comparacion`— sirve para
REVISAR la regla, no para decidir si aplicarla:

- si con la reserva el primero de la lista sigue sin llegar al cierre de las
  20:00, la cuota no da ni para las cuatro vueltas de estructura y hay que
  bajar a tres (quitar la de las 08:00, que es la de menos volumen);
- si el segundo de la lista se agota antes de la tarde por cargar con las
  vueltas de gestión, el reparto pasa al tercero, no vuelve al primero;
- si un día no hay vueltas de gestión, el primero se queda sin usar en esas
  horas: es el precio de la reserva y se acepta.

## Qué NO se hace

- **No se mueven las ventanas de los brazos.** Los tres tienen que mirar los
  MISMOS eventos (paper/CRITERIO_COMPARACION.md); un brazo que corre a otras
  horas mide otro mercado. Lo que se reparte es qué modelo de la lista atiende
  cada evento, no cuándo mira el brazo.
- **No se adapta el prompt a la cuota.** Recortar el contexto para que quepan
  más llamadas mediría un prompt distinto.
- **No se decide mirando el Brier por modelo.** Con las cifras de hoy el mejor
  por azar parece bueno; la regla se aplica por horas medidas, no por
  resultado.
- **No se tocan las 50 predicciones por brazo** del criterio de comparación:
  esto cambia quién escribe dentro del brazo, y el criterio ya contempla que
  el brazo remoto es una lista.

## Lo que ya se cambió hoy, aparte de esto

- El relevo espera también cuando el último modelo cae **a mitad** de la
  vuelta y vuelve en segundos (antes solo esperaba al empezar): es lo que
  perdió la vuelta de Groq de las 20:05.
- `groq_espera_s` pasa de 45 a 60 s: con un solo modelo cargando con todo,
  dos llamadas por minuto de 4-6k tokens superan los ~8k tokens/min.
