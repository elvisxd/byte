# Trampas — cómo lee mal el modelo, anotado ANTES de saber si eso predice algo

> En su propio commit y antes de cualquier código que lo aproveche, por lo mismo
> que `CANDIDATOS.md`: si esto se escribiera después de ver qué operaciones
> ganaron, el historial no podría distinguir «se observó» de «se eligió porque
> cuadraba».

## Qué es esto y qué NO es

Es un catálogo de **errores de lectura** del modelo, con fecha, cita y lo que
falsaría cada uno. Dos usos legítimos:

1. **Quitarle al modelo los cálculos que hace mal**, dándole el número hecho.
   Eso no es un filtro ni una estrategia: es lo mismo que darle el ATR en vez
   de las 200 velas. Se anota aquí qué métrica se añadió y por qué.
2. **Hipótesis para después de las 100 operaciones**: si un error de lectura
   se repite y sus operaciones pierden de forma consistente, «operar al revés
   del modelo cuando lee así» es un candidato a eje, como `anti-smc`. Con las
   mismas reglas: no se activa antes, no se calibra, no se elige mirando la
   tabla.

**Lo que NO es:** una lista de «en qué dirección apostar». Con una operación
cerrada, «el modelo confunde piso con techo, así que apostá al techo» es
exactamente el sobreajuste que `CRITERIO_ABORTO.md` prohíbe. Cada entrada de
abajo tiene n=1 salvo que diga otra cosa.

## 2026-09-14 · `qwen3:14b` con razonamiento, Mac

### 1. Lee la ganancia de un short al revés

Al cerrar la operación #1 (short a 78860.84, salida 78803.28, **+0,057R**):

> «Con el precio en 78801.88 (1000+ puntos por debajo del entry), […] La
> operación está en pérdida y el contexto no justifica mantenerla abierta.»

Dos errores en una frase: la distancia era **57 puntos**, no «1000+», y un
short con el precio por debajo de la entrada **gana**. Cerró creyendo que
perdía. El motivo de cierre —«el patrón no se cumple»— era correcto; el estado
de la posición, al revés.

**Qué se hizo:** `estado_paper` enseña el R abierto de cada posición con signo
y palabra —«+0,06R, a favor»—. El modelo ya no calcula la dirección del P&L.

**Qué lo falsaría como trampa útil:** que con la métrica delante siga
cerrando ganadoras «por pérdida». Entonces no era lectura: era otra cosa.

### 2. Confunde dónde está el precio dentro del rango

Al abrir: «El precio está en el techo del rango actual (79827.4)» — el precio
era 78860, un **1,2 % por debajo** del techo. Al cerrar, diecisiete minutos
después: «El precio está en el piso del rango (76077.62-79827.4)» — estaba al
**73 %** del recorrido, más cerca del techo que del piso.

**Qué se hizo:** `mirar_mercado` dice en qué porcentaje del rango de 20 velas
está el precio —«al 73 % del rango: piso 76077.62, techo 79827.4»—.

**Qué lo falsaría:** lo mismo: que con el porcentaje delante siga diciendo
«techo» a un 73 %.

**Visto (n=1, sesión 8, vuelta 1):** con «al 72% del rango» delante en
`mirar_mercado`, la razón dijo «el precio está en el techo del rango
(79827.4)» con el precio en 78769. Puede ser abreviatura de «cerca del
techo»; con n=1 no se decide. Se sigue mirando.

### 3. Entra sin el patrón del eje, y lo reconoce después

`range-sweep` exige que el techo o el piso **se barra con mecha y el precio
cierre de vuelta adentro**. La razón de entrada no describe ningún barrido:
describe «la mecha de las últimas velas apunta hacia abajo» y FVGs. El propio
modelo, al cerrar: «el patrón 'range-sweep' no se cumple: no hay mecha que
barra el techo ni señal de reversión».

Es el mismo fallo que `granite4.1:8b` en la primera sesión local (entró al
revés del eje). Con dos modelos distintos ya no es una anécdota de uno.

**Qué se hizo:** nada en código. El protocolo de lectura del prompt («eje por
eje, sí o no, con nivel e invalidación») existe justamente para esto, y en la
vuelta 2 no se siguió. Se mide si con más vueltas lo sigue.

**Qué lo falsaría:** que las entradas sin patrón y las entradas con patrón no
se aparten en R/trade. Si da igual, el patrón del eje no importaba.

### 4. Razona un indicador al revés

«FVGs alcistas no rellenados que sugieren presión de venta». Un FVG alcista
sin rellenar es, en la lectura estándar, un hueco al que el precio tiende a
**volver desde arriba** — soporte, no presión de venta. Puede que la lectura
estándar esté mal (es SMC, y `anti-smc` existe por algo), pero el modelo la
usó al revés de como la describe la literatura que la inventó.

**Qué se hizo:** nada. Es una hipótesis, no un error de cálculo.

**Qué lo falsaría:** que las entradas apoyadas en un FVG leído al revés no se
aparten de las demás.

### 5. Ignora el volumen que al 27B lo frenaba

Volumen relativo al abrir: **0,28x**. `qwen3.6:27b` se abstuvo cuatro vueltas
seguidas el 2026-09-13 citando 0,11x–0,34x. El 14B ni lo mencionó.

**Qué se hizo:** nada. Es una diferencia entre modelos, y el registro la
guarda en la columna `modelo`.

**Qué lo falsaría:** que las entradas con volumen < 0,5x no se aparten en
R/trade de las demás. Ojo: el repo de trading ya midió que el volumen solo no
predice dirección (ver la memoria del spike guard); esto es otra pregunta —si
predice que la *razón* del modelo valga menos—.

### 6. El régimen dicho y el medido

Ayer discreparon dos veces (modelo RANGE, código TREND). Hoy coincidieron
(RANGE/RANGE). Las dos columnas viajan al panel a propósito; con n=3 no hay
nada que decir todavía.

### 7. Baja de marco para colar la misma apuesta — y el agujero era mío

Sesión 8, vuelta 1, la primera con la pista de 15m. Rechazada en 4h por
chocar con la #2 (76077.62, abajo, 0,7), la pista le dijo que en 15m entraba
y el modelo la repitió tal cual: 76077.62, abajo, 0,7, **«15m»… y
`horas_vigencia=96`**. Vence el mismo día que la #2. Es la #2 con otro nombre.

Y lo más revelador está en su pensamiento, en la sesión 8:

> «The original had 96 hours, but for 15m, maybe 6 hours? […] However, the
> system didn't mention changing that, just the timeframe.»

**Vio el problema y siguió la instrucción literal de la herramienta.** Sigue a
la herramienta incluso contra su propia duda —que es exactamente la razón por
la que la pista funciona, y por la que el arreglo va en la herramienta y no
en el prompt—.

Dos partes son mías: `predecir` aceptaba cualquier plazo explícito sin
mirar el marco —`PLAZO_POR_MARCO` solo aplicaba con 0—, y la pista decía
literalmente «repetí la llamada con 15m» sin decir que el plazo cambia.

**Qué se hizo:** el plazo no puede superar el de su marco (acortar sí,
alargar no), con un rechazo que lo explica; la pista pide `horas_vigencia=0`
y dice que vence en 6 h, que es OTRA pregunta. La #7 se queda: el registro no
se edita, y su Brier a 96 h es un dato.

**Qué lo falsaría:** que con el plazo capado siga poniendo 0,7 a tocar un
nivel a 12 ATR de 15m en seis horas. Eso ya no sería un agujero: sería una
calibración, y Brier la mide.

## Cómo se usa esta lista

Cada sesión con razonamiento se lee entera —el trace lo permite ahora— y lo
que aparezca se anota aquí con cita. Lo que sea un cálculo se convierte en
métrica de herramienta y se marca «qué se hizo». Lo que sea una hipótesis
espera a las 100. Nada de aquí entra al prompt como «no hagas X»: el
experimento mide cómo lee el modelo, y taparle la lectura sería medir otra
cosa.
