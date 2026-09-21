# Qué mejorar del prompt v4 — hipótesis fijadas ANTES de la comparación

> Escrito el 2026-09-21 con gemini en 77 resueltas y groq en 30. Se fija ahora,
> antes de que groq llegue a las 50, por lo mismo que los otros `CRITERIO_*.md`:
> si estas hipótesis se escribieran después de ver la comparación, no se podría
> distinguir «lo pensé antes» de «lo elegí porque cuadraba». Ninguna se aplica
> todavía. Ver «Cuándo y cómo» al final.

## Lo que dicen los 77 de gemini

La lectura (`LEER=gemini`, con las fórmulas del panel):

```
resolución sobre 77: brier=0.2578 · dijo=41% · ocurrió=40%
decir SIEMPRE la tasa base habría dado 0.2405
```

**Gemini no discrimina.** Diga 29%, 47% o 64%, el nivel se toca ~40% de las
veces. Su probabilidad no lleva información: un modelo que repitiera la tasa
base ganaría. Y el segundo corte del criterio lo confirma: sin 15m sigue por
debajo del ingenuo (0.2466 contra 0.2373). No es el patrón «15m + probabilidad
baja» el que lo hunde; es la resolución entera.

⚠ El 40% NO es el acierto de gemini: es la tasa con la que los niveles que
elige se tocan. La mejora no es subir ese 40%; es que su 64% signifique más
que su 29%.

## Las hipótesis, con su número y su prueba

### H1 · Se ancla en la tasa base y no la mueve (prompt)

El prompt v4 le da la tasa base por marco y dice «tu número tiene que salir de
ahí y de lo que ves que lo cambia». Hace la primera mitad y no la segunda:
devuelve la tasa base (dijo 41%, ocurrió 40%) en todas las bandas.

**Cambio propuesto:** exigir que la predicción diga la tasa base que leyó, el
ajuste y su razón — «base 38%, +15 porque el barrido dejó atrapados en X» —, y
que `predecir` rechace un número sin ajuste declarado. La segunda mitad pasa de
implícita a obligatoria.

**Prueba:** que las bandas se separen. Hoy 20-40% → 41% y 60-80% → 40%; con la
hipótesis cierta, la de arriba tiene que ocurrir más que la de abajo.

### H2 · Ignora el marco por defecto (prompt o registro)

v4 dice que 1h es el marco por defecto de las predicciones. Gemini emite el 60%
en 15m, su peor marco (Brier 0.2653 contra 0.2447 en 1h).

**Cambio propuesto:** «15m solo si hay una operación abierta o una entrada
inminente en ese marco»; o, en el registro, menos sitio para 15m en
`_exigir_separacion`.

**Prueba:** la proporción de 15m baja y el Brier global no empeora. Aviso: el
corte «sin 15m» dice que quitar 15m NO arregla la discriminación (0.2466 sigue
peor que 0.2373). H2 reduce ruido; no cura H1.

### H3 · El indicador de régimen no aporta, o ancla mal (indicador)

Segundo corte: donde el régimen DICHO coincide con el MEDIDO (n=43), el Brier es
el peor del informe, 0.2779 contra 0.2466 del ingenuo. Y **31 de 77
predicciones no tienen régimen dicho o medido**: el 40%.

**Dos cosas, en orden:** primero instrumentar — saber si el modelo no lo dice o
el código no lo captura (`regimen_dicho` vacío)—. Después, si con groq también
sale que coincidir no ayuda, quitar el régimen del prompt: menos que leer y
menos a lo que anclarse.

**Prueba:** el subconjunto «coincide» deja de ser peor que el total.

### H4 · Sesgo direccional (modelo o gráfico — lo decide la comparación)

Hacia abajo dice 39% y ocurre 26%; hacia arriba dice 43% y ocurre 52%.
Sobreestima las caídas y subestima las subidas. v4 obliga a puntuar los dos
lados en cada vuelta, así que el sesgo es limpio de medir.

**No se propone cambio todavía.** Si groq muestra el mismo sesgo, es del gráfico
(qué pools se enseñan y cómo); si solo gemini, es del modelo. Es exactamente la
distinción que el criterio existe para hacer.

### H5 · Los ejes no sobran ni faltan: no los revisa (prompt)

La rúbrica sobre las 28 vueltas con traza en el volumen (medida el 2026-09-21
a las 04:03 EDT, con `RUBRICA=gemini`):

```
1. rechazos: reacciona / insiste            12 / 2
2. razones distintas / total                95 / 97   (la más repetida: 2 veces)
2. pensamientos que recorren ≥3 ejes         8 / 53
3. al tope de iteraciones sin registrar         0
4. cierres manuales antes de una vela        0 / 0
```

Todo limpio menos una línea. Reacciona a los rechazos (12 contra 2), no repite
una plantilla, nunca choca con el tope sin escribir, no cierra a destiempo. Pero
**solo 8 de 53 pensamientos recorren tres o más ejes**. El v4 pide «eje por
eje, los cuatro activos: ¿está ocurriendo AHORA su patrón? Contestá sí o no para
CADA uno»; lo hace el 15 % de las veces. Las 4 operaciones fueron
`range-sweep`, que es coherente con mirar un eje y parar.

**Así que la mejora no es quitar ni añadir un eje: es que revise los que hay.**
Es el mismo mecanismo que H1 —una lista de comprobación pedida en prosa que el
modelo se salta— y la misma solución: que el sí/no por eje sea un campo de
`abrir_operacion` y `predecir`, no un párrafo del prompt. Las dos hipótesis
convergen en un solo cambio de herramienta.

**Prueba:** que «recorren ≥3 ejes» suba de 8/53 a la mayoría, y que las
operaciones dejen de ser de un solo eje.

### H6 · El tope diario se come el cierre de 4h (cadencia, no prompt)

El 2026-09-19 un brazo llegó a 8/8 a las 16:30 por avisos de proximidad («el
precio está a 91 del pool») y **se quedó sin la vuelta del cierre de las 20:00**.
Es la vuelta que más vale y es libre de resultado.

**Cambio propuesto:** que los cierres de 4h no cuenten para el tope, como
`reservar_primero` reserva el modelo bueno para ellos. Va en
`CRITERIO_CADENCIA.md`, no aquí.

## Cuándo y cómo

- **Nada de esto se aplica a gemini solo.** Si el prompt cambia, cambia para
  todos los brazos en el mismo commit y sube `VERSION_PROMPT` a 5; cada
  escritura queda sellada con su versión y las muestras v4/v5 se separan.
- **Se aplica DESPUÉS de leer la comparación v4**, cuando groq cruce las 50
  (~3 días al ritmo de hoy). Cambiar antes partiría las 50 de groq entre dos
  prompts y dejaría la primera comparación sin leer nunca.
- **De una en una o todas juntas, pero medido:** H1 es la que ataca la
  resolución; H2 y H3 quitan ruido. Si van juntas, la prueba de cada una
  sigue siendo la suya.
