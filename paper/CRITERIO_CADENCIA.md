# Cuándo mira el agente — fijado ANTES de medir si sirve

> En su propio commit y antes del código, por lo mismo que `CRITERIO_ABORTO.md`
> y `CRITERIO_PREDICCIONES.md`: el umbral de abajo es un PARÁMETRO, y los
> parámetros se calibran. Si se escribiera después de ver cuántas vueltas da,
> el historial no podría distinguir «se eligió antes» de «se bajó hasta que
> diera suficientes».

## El problema que resuelve

El bucle daba vueltas seguidas sin descanso. Medido el 2026-09-14 con
`qwen3:14b` en una Mac:

- **31 vueltas en 40 minutos**, y en 23 de ellas el modelo agotó sus 6
  iteraciones sin registrar nada.
- Entre la primera vuelta y la quinta, BTC se movió **20,7 dólares: un 0,03%**,
  con un ATR de 15m de 170. Una octava parte de lo que recorre una vela normal.
- Tres órdenes idénticas —mismo nivel, mismo stop— en tres vueltas seguidas.

El modelo no estaba fallando: estaba mirando **el mismo gráfico** una y otra
vez. Sin información nueva, repetir es la respuesta correcta.

Treinta y una observaciones del mismo instante no son treinta y una
observaciones. Son una.

## La regla

Entre vuelta y vuelta se espera a lo que ocurra PRIMERO:

1. **Que cierre una vela de la temporalidad que el agente esté mirando.** Por
   construcción, eso es información nueva: en 15m, cada cuarto de hora.
2. **Que el precio se mueva 0,5 ATR** desde la última vuelta.

Y un tope: si no pasa ninguna de las dos en **el doble de lo que dura una vela**
de su marco, se mira igual. Un mercado que no se mueve también es un dato, y una
sesión que no da ninguna vuelta no registra la abstención.

## Por qué en ATR y no en porcentaje

Lo mismo que la separación entre niveles: un 0,3% es enorme en un día tranquilo
y ridículo en uno volátil. El ATR se adapta solo, y además a la temporalidad
—hoy: 170 en 15m, 282 en 1h, 645 en 4h—.

## Por qué 0,5 y no otro número

Es medio recorrido de vela: lo bastante para que el gráfico haya cambiado, lo
bastante poco para no perderse un movimiento que empieza. Con la volatilidad
del 2026-09-14 serían ~85 dólares en 15m.

**No se toca para que dé más vueltas.** Si la sesión hace tres vueltas en
cuarenta minutos, eso es lo que el mercado ofrecía. Bajar el umbral porque
«da pocas» es exactamente el sobreajuste que `rangeSweepCombo.ts` documenta:
−96R convertidos en +88R recortando la muestra.

## Lo que esto NO es

**No es un filtro de entrada.** No decide cuándo operar ni qué mirar: decide
cuándo hay algo nuevo que mirar. El agente sigue siendo libre de abstenerse ante
un gráfico que cambió.

**Y no reduce la muestra: la mejora.** Diez vueltas sobre diez gráficos
distintos valen más que treinta y una sobre el mismo, porque el experimento
mide si las razones escritas discriminan — y treinta razones escritas sobre el
mismo instante no discriminan nada.

## Cuándo se revisaría

Solo si aparece evidencia de que la espera se traga movimientos que importan:
por ejemplo, órdenes que se disparan entre vuelta y vuelta con una tesis que el
agente habría querido revisar. Eso sería un hallazgo, no un ajuste de comodidad.
