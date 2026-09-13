# Criterio de las predicciones — fijado ANTES de la primera

> Va en su propio commit, **antes** de que exista una sola predicción, por la
> misma razón que `CRITERIO_ABORTO.md`: si se escribiera después de ver los
> resultados, el historial no podría distinguir «se decidió antes» de «se eligió
> la métrica que salía bien».

## Lo que NO se está probando

**No se prueba que el modelo prediga el precio.** Esa pregunta ya está
respondida y la respuesta es que no, ni él ni nadie:

- Walk-forward sobre 12 modelos —ARIMA, Prophet, Random Forest, XGBoost, LSTM—
  concluye que los modelos **ingenuos** (NaiveDrift, NaiveSeasonal) superan a
  todas las alternativas complejas, y que el forecasting univariante de cripto
  es «esencialmente comparable a predecir ruido puro».
- Un preprint de 2026 lo extiende a horizontes de 1-6 meses: sobre **40 modelos
  descubiertos por máquina y 18 configuraciones bayesianas**, en cinco ventanas
  de validación no solapadas (2016-2026), la optimización elige **corrección
  cero en todas las ventanas**. Ninguno bate al ingenuo en ningún régimen.
- KalshiBench midió frontier models contra mercados de predicción reales: **solo
  uno logra Brier Skill Score positivo**. El resto predice peor que decir la
  tasa base.

Así que **batir al baseline ingenuo NO es el criterio de éxito**, y fallar en
eso no es motivo de aborto. Se anota aquí para que nadie lo proponga después
como si fuera un descubrimiento.

## Lo que SÍ se está probando

**Que el modelo sepa cuándo no sabe.**

Es decir: que sus predicciones de 80% acierten más que sus predicciones de 55%.
Un modelo puede fallar la mitad de las veces y aun así ser útil si su propia
confianza discrimina — porque entonces no se opera siempre, se opera solo cuando
dice 80%.

En la descomposición de Murphy, `BS = Fiabilidad − Resolución + Incertidumbre`,
eso es la **RESOLUCIÓN**: cuánto separan sus probabilidades a los casos que
ocurren de los que no. Es independiente de batir al ingenuo, y es lo que casi
nadie ha medido en cripto con el razonamiento sellado de antemano.

Es la misma pregunta que hace `CRITERIO_ABORTO.md` —«¿las razones escritas antes
discriminan?»— pero con un número en vez de prosa, y acumulando muestra diez
veces más rápido: ~5 predicciones por sesión contra 0-1 operaciones.

## La regla de puntuación, dicha de antemano

**Brier score.** Se le dice al modelo en el prompt, y no es un detalle:
[How Proper Scoring Rules Shape LLM Forecasting] muestra que la regla que se
anuncia **cambia lo que el modelo reporta**. Si se le puntúa por acierto, exagera
su confianza; con Brier, se modera. Cambiar la regla a mitad de camino
invalidaría todo lo acumulado hasta ese punto.

    Brier = (probabilidad_dicha − ocurrió)²     ocurrió ∈ {0, 1}

Más bajo es mejor. 0.25 es lo que saca quien dice siempre 50%.

## Se aborta si, al llegar a 100 predicciones resueltas:

1. **No hay resolución.** Si al agrupar por la probabilidad que dijo, los grupos
   no se separan en tasa real de ocurrencia más de lo que se separarían
   barajando las probabilidades al azar (permutación, 1000 remuestreos,
   p > 0.05). Sin resolución, su confianza no informa y todo el ejercicio sobra.

2. **La resolución no sobrevive fuera de muestra.** Si aparece en las primeras
   70 y desaparece en las últimas 30 —las que ningún ajuste pudo ver—, era
   sobreajuste. Mismo criterio que para los ejes.

3. **El razonamiento no aporta sobre la probabilidad sola.** Si agrupar por lo
   que ESCRIBIÓ —régimen que dijo ver, eje que mencionó— no separa más que
   agrupar solo por el número, entonces la prosa es decorativa y lo único que
   valía era el porcentaje.

## Lo que NO es criterio de aborto

- **Perder contra el ingenuo.** Ver arriba: se da por supuesto.
- **Un Brier alto.** Un modelo mal calibrado pero con resolución se puede
  recalibrar; uno bien calibrado sin resolución no sirve para nada. La
  fiabilidad se arregla, la resolución no.
- **Rachas malas.** Con 20 predicciones, cualquier cosa.

## Cuándo ve el modelo sus propios resultados

**No antes de las 50 resueltas, y solo el Brier — nunca el % de acierto.**

Todos los frontier models medidos muestran sobreconfianza sistemática (ECE
0.12-0.40). Enseñarle pronto su tasa de acierto es invitarlo a ajustar su
criterio contra ruido: con 20 predicciones, el «patrón» que detecte será casi
seguro azar. Y la fiabilidad del Brier es **inestable con muestra pequeña**, así
que ni siquiera el número sería de fiar.

Es el mismo principio que ya rige los ejes: «no elijas el mejor mirando esta
tabla».

## Lo que se guarda de cada predicción

Lo mismo que de una operación, y por lo mismo: el contexto entero, sellado al
predecir. Para poder preguntar después «¿acertaba más en RANGE que en TREND?»
sin haber decidido de antemano que el régimen importaba.

- la probabilidad, el nivel y el horizonte
- el régimen que el código detectó y el que el modelo dijo ver
- el razonamiento, **sellado con el mismo hash que las razones de entrada**
- la resolución la calcula el CÓDIGO contra las velas, nunca el modelo

## Una trampa que ya está documentada y no se va a repetir

La autoconsistencia —preguntarle tres veces y ver si coincide— **no sirve como
control de calidad**. Está medido que cuando un modelo se compromete temprano
con un razonamiento equivocado, produce la misma respuesta errónea de forma
consistente, y la autoconsistencia no puede distinguir ese caso de estar en lo
cierto. Si alguien propone «que prediga tres veces y nos quedemos con la moda»,
esto es la respuesta.

## Fuentes consultadas el 2026-09-13

- Ingenuos que ganan: https://arxiv.org/html/2502.09079v1
- Calibración vía mercados de predicción: https://arxiv.org/html/2512.16030
- Reglas de puntuación y lo que el modelo reporta: https://arxiv.org/html/2608.28482
- Descomposición de Murphy: https://arxiv.org/pdf/0806.0813
- Errores autoconsistentes: https://arxiv.org/pdf/2505.17656
- Diagramas de fiabilidad revisados: https://arxiv.org/pdf/2008.03033
