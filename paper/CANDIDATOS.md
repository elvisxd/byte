# Candidatos a eje — anotados el 2026-09-13, NO aplicados

> Va en su propio archivo y con fecha por la misma razón que
> `CRITERIO_ABORTO.md` va en su propio commit: si estas hipótesis se escribieran
> después de ver cómo van los cinco ejes actuales, el historial no podría
> distinguir «se pensó antes» de «se eligió porque funcionó».

**Ninguno de estos está activo.** `EJES.md` dice que no se toca ningún eje hasta
las 100 operaciones cerradas, y al escribir esto van **cero**. Añadir hipótesis
ahora repartiría la muestra desde el principio, que es justo lo que el
experimento quiere evitar.

Esto es una lista de espera, no una propuesta de cambio.

## Por qué se anotan ahora

La primera sesión con el modelo grande (`qwen3.6:27b`, 4 vueltas, 2026-09-13)
cerró **sin abrir una sola operación**, y la traza dice por qué —siempre lo
mismo:

    vuelta 1 (1h):  «Volumen relativo: 0.11x (muy bajo, falta convicción)»
    vuelta 2 (1h):  «Volumen relativo: 0.21x → muy bajo, falta confirmación»
    vuelta 3 (15m): «Volumen relativo: 0.34x (bajo volumen)»

No fue incapacidad: nunca agotó sus iteraciones —tenía cinco más en cada vuelta—
y en la tercera cambió de temporalidad por su cuenta, de 1h a 15m, buscando en
un marco más fino lo que no veía en el general.

El dato interesante es **cuál es su filtro**: el volumen spot. Y el volumen spot
de BTC es la mitad del mercado — la otra mitad, la que mueve el precio a corto
plazo, está en derivados. De ahí sale el primer candidato.

## Lo que ya está en el repo de trading y ningún eje usa

`scripts/paper/indicadores/` tiene restaurados siete indicadores que ninguna de
las cinco hipótesis actuales toca:

| indicador | qué mide |
|---|---|
| `derivatives.ts` | Open Interest, Funding Rate |
| `liquidity.ts` | EQH/EQL, pools de liquidez |
| `vpvr.ts` | perfil de volumen, POC |
| `fvg.ts` | fair value gaps |
| `regime.ts` | RANGE/TREND con histéresis |
| `channels.ts` | canal de regresión |
| `pitchfork.ts` | tridente de Andrews (solo visual) |

`mirar_mercado` calcula siempre `atr`, `adx`, `rsi`, `macd` y `ema`. Los siete de
arriba no llegan al modelo, así que ningún candidato de abajo se puede probar sin
antes exponerlos en el contexto.

---

## 1 · `funding-extremo` — el posicionamiento como contraparte

Cuando el funding rate se mantiene en un extremo durante días, un lado del
mercado está pagando por sostener su posición. La hipótesis es que ese lado es la
gasolina del movimiento contrario: sus liquidaciones forzadas empujan el precio y
disparan la siguiente tanda.

**Por qué este y no otro indicador de derivados.** La cabecera de
`derivatives.ts` ya lo dice sin que nadie se lo pidiera: *«El mercado de
derivados es el motor real del precio de BTC a corto plazo. Ignorar el Open
Interest y el Funding Rate es ver solo la mitad del mercado.»* El modelo se
abstiene por volumen spot bajo; esto es exactamente lo que el volumen spot no le
cuenta.

**La evidencia de 2026, que son hechos y no win rates.** El funding de los
perpetuos de BTC encadenó 46 días con media negativa a 30 días —la racha más
larga desde noviembre de 2022— mientras el open interest *subía* en vez de bajar:
posiciones cortas nuevas abriéndose contra un funding que ya castigaba. El 20 de
agosto de 2026 eso se deshizo con 1.740 millones de dólares en liquidaciones de
cortos en 24 horas, el segundo mayor short squeeze registrado.

**Qué lo falsaría.** Que las entradas tras funding extremo no se aparten en
R/trade de las entradas en funding neutro. Si el posicionamiento extremo no
anticipa nada, este eje pierde como cualquier otro y eso cierra la pregunta.

**Qué hace falta antes.** Una fuente de funding y OI que responda desde esta red.
Es el mismo problema que dejó dormido a `cvd-divergence`: Binance responde 451 y
MEXC no expone `takerBuyVolume`. Sin verificar la fuente primero, este candidato
no se puede ni escribir.

## 2 · `poc-iman` — el precio vuelve a donde se negoció

El punto de control (POC) es el precio donde más volumen se cruzó. La hipótesis
es que en mercados en rango actúa como imán: el precio que se aleja tiende a
volver a visitarlo.

**⚠ OJO, ESTO YA SE MIDIÓ Y SALIÓ INERTE.** En el repo de trading está anotado
que CVD y POC **direccionales** no predicen nada a 72 horas (aporte máximo
+0.099% sobre 18 horizontes, medido con order flow real). Repetir ese
experimento sería gastar muestra en una pregunta ya respondida.

Solo vale la pena si se plantea **distinto**: no como señal de dirección —«el
precio va a subir porque el POC está arriba»— sino como **objetivo** de una
entrada que ya tiene su propia razón. Es decir, POC como destino del take
profit, no como motivo de entrada.

**Qué lo falsaría.** Que el R/trade con objetivo en el POC no mejore respecto al
objetivo fijo de 2R que usan todos los ejes hoy.

## 3 · `fvg-lvn` — el hueco sobre el vacío

Un fair value gap es un desequilibrio: el precio se movió tan rápido que dejó un
rango sin negociar. Un nodo de bajo volumen (LVN) es una zona donde apenas se
cruzó nada. La hipótesis es que cuando coinciden, el precio los atraviesa de una
vez en lugar de negociarlos.

**De dónde sale.** Es lo único de la búsqueda de literatura (2026) con una
mecánica explicable en vez de un porcentaje de aciertos: dos formas distintas de
medir «aquí no hubo negociación» apuntando al mismo precio.

**⚠ DESCONFIAR DE LOS NÚMEROS QUE LO ACOMPAÑAN.** Las fuentes que lo describen
venden win rates —«SMC 58%», «POC edge»— y `EJES.md` ya deja escrito por qué eso
no vale: *«los backtests que muestran 61% son estrategias ya calibradas, que es
justo lo que no sobrevive a la ejecución real»*. Lo que se rescata es la
mecánica, no la estadística.

**Qué lo falsaría.** Que las entradas en confluencia FVG+LVN no se aparten de las
entradas en FVG solo. Si el LVN no añade nada, sobra.

---

## Lo que NO se hace con esta lista

- **No se activa ninguno antes de las 100 operaciones.** Ni «solo para probar».
- **No se elige el mejor mirando resultados.** Si algún día entran, entran todos
  a la vez y sin calibrar, como los cinco actuales.
- **No se calibran umbrales.** Un `funding-extremo` con el umbral ajustado hasta
  que dé positivo es `rangeSweepCombo.ts` otra vez: −96R convertidos en +88R
  recortando la muestra de 474 operaciones a 67.

## Fuentes consultadas el 2026-09-13

- Funding negativo 46 días seguidos y OI al alza:
  https://phemex.com/blogs/bitcoin-funding-rates-negative-46-days-ftx-bottom
- Open interest de BTC, guía 2026: https://bitsgap.com/blog/bitcoin-open-interest-explained
- Qué señala el funding rate: https://bitsgap.com/blog/funding-rate-explained
- Liquidaciones y apalancamiento:
  https://cryptoslate.com/a-48-billion-bitcoin-leverage-trap-is-about-to-trigger-a-massive-forced-exit-the-moment-price-boundaries-break/
- POC, VAH/VAL: https://tradingsfx.com/blog/volume-profile-explained-poc-value-area
- Fair value gaps: https://forextester.com/blog/fair-value-gap/
