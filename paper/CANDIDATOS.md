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

## 4 · `vela-reversion` — la anatomía de la vela, no el nombre del patrón

Anotado el 2026-09-14 tras revisar la evidencia. **No se activa.**

**Lo que dice la literatura, con números.** En acciones, no: Marshall, Young
y Rose (2006), DJIA 1992-2002 contra series aleatorias por bootstrap —los
patrones no generan retorno distinguible del azar—, replicado en Japón y
Taiwán. Bulkowski da tasas por patrón sobre 4,7 millones de velas (morning
star 78 % de reversión, abandoned baby 70 %, engulfing 63 %), pero es acciones,
diario y a diez días. En cripto, dividido: el estudio de 2026 en ScienceDirect
(55 patrones de reversión, velas de 1h, ~400 monedas, 36 exchanges, 200 millones
de observaciones, con test SPA por data-snooping) encuentra que UNOS POCOS sí
preceden retornos —Harami y Hikkake alcistas; Harami bajista y Hanging Man—,
robustos entre exchanges y períodos (tamaño del efecto y costes no
consultables: de pago). La tesis de la Universidad Carolina (41 patrones, 5
conjuntos, t-test ajustado por asimetría y binomial): **8 de 41 útiles, solo 4
robustos**, y ninguno significativo en uno de los conjuntos. Un backtest de 43
patrones × 5 marcos en BTC con walk-forward: cero sobreviven (no publicado).

**La trampa, que es lo que vale de esta revisión.** En cripto, el Shooting
Star —catalogado como bajista— y la mecha superior larga **son alcistas**; la
tesis propone reclasificarlos y concluye que «el etiquetado tradicional de los
patrones es cuestionable en cripto». Es la misma lógica de `anti-smc`: el
manual dice una cosa y el mercado hace otra. Y los patrones con hueco
(Rising Window, Tasuki, Abandoned Baby) apenas existen: cripto no cierra.

**Por qué NO un eje de «patrones».** Tres razones. Las etiquetas no se
trasladan, así que un eje «hammer alcista» ya nace con la dirección puesta por
un libro de otro mercado. Probar 41 o 103 patrones es la trampa de
comparaciones múltiples que el repo de trading ya pagó (`mlb/Pitching Outs`,
de p=0,0099 a 0,804 al corregir). Y lo que el estudio grande encuentra son
anatomías —una vela pequeña dentro de la anterior, una mecha larga en un
extremo—, no nombres.

**El candidato, si algún día entra:** una vela de 1h con mecha ≥ 2 veces el
cuerpo, en un extremo del rango de 1h, **sin dirección presupuesta**: la
hipótesis es que el precio se aleja de la mecha (rechazo) y la contraria, que
la sigue (continuación, lo que la tesis vio en el Shooting Star). Las dos se
miden; ninguna se elige antes.

**Qué lo falsaría.** Que las entradas de `range-sweep` hechas tras una vela de
mecha larga en el extremo no se aparten en R/trade de las hechas sin ella. Se
puede medir SIN activar nada: el contexto sellado guarda las velas del
momento, y el mapa va a decir la anatomía de la última vela cerrada.

**Qué hace falta antes.** Que el mapa enseñe la anatomía de la última vela
cerrada como hecho —cuerpo, mechas en ATR, color— sin nombrar patrones. Eso
es «el número hecho, no el cálculo», y va en código aparte de este archivo.

## 5 · `turn-of-the-candle` — el reloj, no la vela

Anotado el 2026-09-14. **No se activa.** Salió buscando lo anterior y es más
robusto que cualquier patrón: en BTC, los retornos positivos —0,58 puntos
básicos por minuto— se concentran en los minutos **0, 15, 30 y 45** de cada
hora, con t > 9 en los siete exchanges estudiados (Bitfinex, Binance, Gemini,
Bitstamp, Bittrex, Kucoin, FTX), robusto a colas pesadas y outliers, y una
estrategia que lo explota rinde 74,18 % neto anual contra 60,27 % de comprar y
mantener, con costes y con capital desde 5.000 $. Los autores lo atribuyen a
algoritmos que reaccionan a la llegada de la vela de 15 min.

**Por qué está aquí y no en un eje.** No es una lectura del gráfico: es un
efecto de microestructura a un minuto, y el agente opera con velas de 15m en
adelante. Encaja mejor como regla de EJECUCIÓN —cuándo dentro del cuarto de
hora conviene que se dispare una orden— que como hipótesis de entrada.

**Qué lo falsaría.** Que el efecto haya desaparecido: el paper es de 2023 con
datos hasta 2022, y un efecto de algoritmos se arbitra. Se comprueba con
velas de un minuto de MEXC antes de hacer nada.

**Fuentes (2026-09-14):** Marshall, Young y Rose (2006)
https://www.researchgate.net/publication/223853109 · ScienceDirect 2026
https://www.sciencedirect.com/science/article/pii/S1059056026002716 · Tesis
Universidad Carolina
https://dspace.cuni.cz/bitstream/handle/20.500.11956/197060/130412926.pdf ·
Bulkowski https://thepatternsite.com/MorningStar.html · Turn-of-the-candle
https://pmc.ncbi.nlm.nih.gov/articles/PMC10015199/ · Backtest BTC 43×5
walk-forward https://www.youtube.com/watch?v=U_jhKw7rdB8

## Lo que NO se hace con esta lista

- **No se activa ninguno antes de las 100 operaciones.** Ni «solo para probar».
- **No se elige el mejor mirando resultados.** Si algún día entran, entran todos
  a la vez y sin calibrar, como los cinco actuales.
- **No se calibran umbrales.** Un `funding-extremo` con el umbral ajustado hasta
  que dé positivo es `rangeSweepCombo.ts` otra vez: −96R convertidos en +88R
  recortando la muestra de 474 operaciones a 67.

## Actualización 2026-09-14 — el bloqueo de `funding-extremo` se levantó

El candidato 1 decía «hace falta una fuente de funding y OI que responda desde
esta red». Ya la hay, con un matiz que importa.

**Binance responde, pero solo con VPN.** Medido hoy: sin VPN devuelve **451 en
todo** —spot, funding y OI—, y **también desde la Mac**, así que nunca fue cosa
del Codespace ni de la máquina: es geo-bloqueo. Con el VPN activo, los dos
endpoints que `derivatives.ts` ya declara en sus líneas 8-9 responden 200:

    GET fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT
      → [{fundingTime, fundingRate, markPrice}, ...]   ciclos de 8h
    GET fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h
      → [{sumOpenInterest, sumOpenInterestValue, timestamp}, ...]

**MEXC también los tiene, en otra API.** `contract.mexc.com/api/v1/contract/...`
—distinta de la de spot, `api.mexc.com/api/v3/...`, y por eso el fallback de
velas funcionaba mientras los derivados parecían inalcanzables—. Da
`fundingRate`, `holdVol` (el OI), `indexPrice` y `fairPrice`, sin VPN.

**Cuál sirve para esta hipótesis: Binance.** No por preferencia, sino por lo que
pide la tesis. `funding-extremo` dice «el funding lleva DÍAS en un extremo», y
eso es una serie temporal: Binance la trae hecha, MEXC solo da la lectura
puntual y habría que construirla guardando lecturas durante semanas antes de
poder medir nada. Además MEXC es una fracción del mercado de derivados, y el
posicionamiento de una fracción dice menos sobre las liquidaciones que mueven el
precio.

**Lo que esto NO cambia.** Sigue sin activarse hasta las 100 operaciones
cerradas —van cero—, y sigue pendiente el requisito previo de más arriba: los
indicadores de `derivatives.ts` no llegan a `mirar_mercado`, así que el modelo
no los ve. Esto solo tacha «no se puede ni escribir».

**La decisión que queda abierta, para cuando se active.** Si el VPN se cae en
mitad de una sesión, Binance vuelve a 451. Habrá que elegir entonces entre que
el eje se abstenga o que caiga a MEXC como fallback —como hace `velas.mjs` con
las velas—. No se decide ahora: se decide con el eje delante, y se anota aquí.

## Fuentes consultadas el 2026-09-13

- Funding negativo 46 días seguidos y OI al alza:
  https://phemex.com/blogs/bitcoin-funding-rates-negative-46-days-ftx-bottom
- Open interest de BTC, guía 2026: https://bitsgap.com/blog/bitcoin-open-interest-explained
- Qué señala el funding rate: https://bitsgap.com/blog/funding-rate-explained
- Liquidaciones y apalancamiento:
  https://cryptoslate.com/a-48-billion-bitcoin-leverage-trap-is-about-to-trigger-a-massive-forced-exit-the-moment-price-boundaries-break/
- POC, VAH/VAL: https://tradingsfx.com/blog/volume-profile-explained-poc-value-area
- Fair value gaps: https://forextester.com/blog/fair-value-gap/
