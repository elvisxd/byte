# Cómo se gestiona una operación abierta — fijado ANTES de medir si sirve

> En su propio commit y antes del código, por lo mismo que los otros
> `CRITERIO_*.md`: lo de abajo son parámetros y reglas, y si se escribieran
> después de ver qué operaciones ganaron, el historial no podría distinguir «se
> decidió antes» de «se eligió porque cuadraba».

## El caso que lo motiva

La primera operación del experimento (#1, 2026-09-14): short sobre 4h, **sin
objetivo**, cerrada por el modelo a los 17 minutos —0,07 velas de 4h— mirando
15m, con su invalidación intacta y creyendo que perdía cuando ganaba. Tres
cosas fallaron: no había destino, no había plazo, y se juzgó en el marco
equivocado. La tercera ya está arreglada (#60). Este archivo fija las otras
dos, y dice qué NO se hace.

## Lo que dice la evidencia, con números

**Salir por tiempo, a secas, es una salida mala.** El estudio más grande
encontrado —567.000 backtests sobre 40 futuros, 5 marcos de 60 min a diario,
10 años, con slippage y comisiones— clasificó 15 tipos de salida por beneficio
neto sobre drawdown: las **salidas por tiempo quedaron muy por debajo** del
stop-and-reverse; las mejores fueron el **objetivo fijo** y el stop a
break-even; las peores, trailing, chandelier y parabólico. Ojo con el
contexto: sistemas sistemáticos de futuros, no entradas discrecionales de
reversión. Lo que se rescata es la dirección del hallazgo: lo que funciona es
tener una salida DEFINIDA, y el reloj es peor que una señal.

**Los parciales suben el acierto y bajan la expectativa.** Escalar la salida
(50 % a 1R, 50 % a 3R contra 100 % a 2R): acierto 38 → 54 % (cripto 42 → 61 %),
retorno total 22 → 18,5 %, factor de beneficio 1,45 → 1,62, drawdown 14,2 →
8,5 %. Cambia la forma de la curva; no es un regalo. Y cada parcial paga.

**Cuánto tarda una reversión en jugarse —medido en BTC, no copiado.** La
literatura de reversión fija el tiempo máximo de una posición con la **vida
media** de la desviación (Ornstein-Uhlenbeck): cuántas velas tarda una
desviación en deshacerse a la mitad; si en 2-3 vidas medias no volvió, el
régimen cambió. Calculada el 2026-09-14 sobre las últimas ~500 velas de BTC,
desviación respecto a la media de 20 velas:

| marco | vida media | en horas |
|---|---|---|
| 15m | ~8 velas | ~2 h |
| 1h | ~8,5 velas | ~8,5 h |
| 4h | ~12 velas | ~48 h |

Los plazos que `CRITERIO_PREDICCIONES.md` ya dio a las predicciones —6 h, 24 h,
96 h— son **2 a 3 vidas medias** de cada marco. Se eligieron antes de medir
esto y cuadran; se dejan.

## Las reglas

1. **Una operación tiene el plazo de su marco**, el mismo que una predicción
   de ese marco: 6 h en 15m, 24 h en 1h, 96 h en 4h. No es un reloj que
   cierra: cuando lo agota sin tocar stop ni objetivo, el vigía **despierta al
   modelo** y este decide —seguir, con razón escrita, o cerrar con motivo
   `tiempo`—. El motivo `tiempo` existe para poder medir esas salidas aparte
   de los stops y los objetivos. El script sigue sin decidir nada.

2. **Objetivo obligatorio al abrir.** El objetivo fijo fue la segunda mejor
   salida del estudio, y una tesis de reversión tiene destino natural —el otro
   lado del rango, la media—. Se exige como se exige el stop. El modelo puede
   salir antes si su invalidación ocurre; lo que no puede es entrar sin saber
   adónde va.

3. **Break-even, parciales, trailing: sin regla.** El modelo tiene
   `mover_stop` y `salir_parcial`, cada cierre lleva su motivo, y `por_eje` los
   mide. Imponer una gestión sería elegir por él lo que la evidencia dice que
   depende del tipo de operación, y contaminaría el R con una decisión que no
   es suya.

4. **`estado_paper` enseña el plazo**: «lleva 17 min de 96 h» al lado de la
   edad en velas y la invalidación. Con eso delante, «pasaron minutos» no es
   un motivo.

## Lo que NO se hace

- **No se cierra por reloj.** Es la salida que peor rindió en el estudio.
- **No se calibra el plazo mirando resultados.** Si algún día se toca, se
  anota aquí el valor viejo, el nuevo y qué se vio, como en `CRITERIO_CADENCIA`.
- **No se ordena por motivo de cierre para elegir «el que va mejor».** Los
  motivos se miden para saber qué pasó, no para decidir qué imponer.

## Qué lo revisaría

Que las operaciones cerradas con motivo `tiempo` tengan un R/trade que no se
aparte del de las cerradas por stop u objetivo. Entonces el plazo no
discrimina nada y sobra el despertar.

## Fuentes (2026-09-14)

- 567.000 backtests sobre salidas: https://kjtradingsystems.com/algo-trading-exits.html
- Parciales, medidos: https://quantstrategy.io/blog/backtesting-partial-close-strategies-does-scaling-out/
- Vida media como tiempo máximo de una reversión: https://www.submillisecond.com/glossary/strategy/mean-reversion
- Triple barrera (stop, objetivo, tiempo): https://medium.com/@jpolec_72972/stop-loss-take-profit-triple-barrier-time-exit-advanced-strategies-for-backtesting-8b51836ec5a2
- Break-even, cuándo ayuda y cuándo cuesta: https://atas.net/blog/break-even-in-trading/
