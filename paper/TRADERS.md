# Revisión de metodologías de traders — anotada el 2026-09-14, NO aplicada

> Lo mismo que `CANDIDATOS.md`: lista de espera, no propuesta de cambio. Y con
> una regla propia: la «rentabilidad» de un canal de YouTube no es verificable
> —casi nadie publica extractos auditados y hay sesgo de supervivencia—, así
> que lo que se evalúa es la METODOLOGÍA, contra lo que ya sabemos con datos.
> Para comparar con algo verificable se usan traders con registro auditado
> (campeonatos con extractos reales) cuyo método está documentado.

## 1 · Elite Crypto / @leakcrypto (YouTube, español)

**Qué se leyó:** los últimos 15 títulos y los subtítulos automáticos de dos
vídeos representativos (~5.000 palabras): «Bitcoin intenta reingresar al
rango… ¿patrón bajista?» y «Bitcoin confirma ruptura de soporte… ¡patrón de
manual!». Sin ver los vídeos: solo lo dicho.

**Qué mira, por frecuencia en el texto:** short/long (54), niveles (26), rango
(24), EMA (20), liquidez y liquidaciones (29), patrón (15), mecha (10),
estructura (9), Fibonacci (7), FVG (7), FOMO e índice de miedo y codicia,
calendario económico (inflación, Wall Street cerrado). Es análisis
discrecional de **liquidez + SMC**: mapa de liquidaciones para decidir
«adónde va a ir a liquidar», entradas en el FVG de 4h, el FVG diario «como
imán», Fibonacci, y la regla de «reingresa al rango → priorizar shorts».

**Reglas explícitas que aparecen:** entrada «en la parte alta del FVG de 4
horas», stop «corto», objetivo «el nivel de liquidación de shorts» o «atacar
el mínimo hasta 74-75.000», «horas más seguras para operar», cerrar a
break-even. Y una confesión útil: «unos seis trades han salido break-even y
tres stop loss» —una racha de 0 de 9—, dicha sin números de antes ni de
después.

**Qué de esto ya tenemos:** el rango y su reingreso (es `range-sweep`), los
pools de liquidez y los FVG (el mapa los enseña, sin dirección), el % del
rango, la EMA. **Qué es SMC sin evidencia:** los FVG como imán y los order
blocks como entrada —648 backtests, cero significativos; es lo que `anti-smc`
pone a prueba—. **Qué no aporta:** no hay regla de tamaño, ni de plazo, ni
estadística propia; los objetivos son «zonas» de 1.000 puntos.

**Veredicto:** nada que aplicar. Es la misma hipótesis SMC/liquidez que el
experimento ya contiene por las dos caras (`range-sweep` y `anti-smc`), con
menos precisión que la nuestra y sin registro.

## 2 · Linda Raschke — Turtle Soup (registro auditado, *Market Wizards*)

**Reglas originales (compra):** rango de **20 días**; el mínimo previo de 20
días tiene que ser de hace **≥ 3 días** (nivel «fresco»); el precio rompe por
debajo; orden de compra **5-10 ticks por encima** del mínimo roto; stop **1
tick por debajo** del mínimo del día; trailing cuando va a favor; **una
reentrada** permitida si el stop salta el primer o segundo día. «Plus One»:
esperar a que la vela cierre fuera del rango y usar el extremo de dos días.
Venta, espejo.

**Es nuestro `range-sweep`**, casi literal: barrer el extremo de un rango y
entrar a favor de la vuelta. Diferencias que valen como candidatos de
precisión —no de estrategia—: (a) exigir que el extremo barrido tenga **al
menos N velas de antigüedad** (nosotros no lo pedimos); (b) la reentrada
única si salta el stop; (c) el stop a un tick del extremo, no a un pool.

**Ojo:** probada mecánicamente en diario sobre USDJPY, oro y petróleo (4-5
años), salió **no rentable**; los autores del test advierten contra seguirla
sin lectura. O sea: la regla sola no basta, que es exactamente lo que el
experimento mide con las razones selladas.

## 3 · Los del campeonato: Minervini, Kullamägi, Darvas, O'Neil

**Registros:** Minervini, US Investing Championship 1997 (155 %) y 2021
(334,8 %), cuentas reales auditadas, 220 % anual compuesto en cinco años.
Kullamägi publica extractos. El campeonato exige cuenta real ≥ 20.000 $ y
auditoría.

**Método común, con números:** compran **fuerza** —acciones con +30-100 % en
12 semanas (Kullamägi), máximos de 52 semanas (Darvas), tendencia sobre la
MM200 y MM50 (Minervini)—; entran en la **ruptura** de una consolidación con
volumen +40-50 %; stop **ajustado, 2-8 %** bajo el mínimo de la ruptura;
venden en fuerza y dejan correr con trailing. Acierto **20-25 %**, ganadoras
de **5-20R**: la asimetría paga el acierto bajo.

**Qué dice esto de nuestro experimento:** es el **lado contrario** de todos
nuestros ejes. Los cinco apuestan a reversión (barrido, recuperación, dip,
anti-smc); ninguno a continuación. Con un acierto esperado bajo y ganadoras
largas, ese lado solo se puede medir con objetivos lejanos y trailing — y
nosotros exigimos objetivo fijo. No es un fallo: es que el experimento eligió
un lado. Queda anotado como el eje que falta si algún día se abre la lista.

**Candidato de precisión que sí es nuestro:** sus stops van a un porcentaje
del precio (2-8 %), los nuestros en ATR (1,5). Comparable, no mejor.

## Qué NO se hace con esto

- No se copia una «metodología de YouTube»: sin registro no hay nada que
  copiar, y lo que dice ya está en el experimento por sus dos caras.
- No se añade el lado de continuación antes de las 100. Se anota.
- No se ajusta `range-sweep` con las reglas de Raschke hasta que haya
  operaciones cerradas suficientes para saber si el nivel «fresco» separa
  algo. Es un candidato de precisión, con lo que lo falsaría: que las entradas
  sobre extremos de ≥ 3 velas no se aparten en R de las de extremos recientes.

## Fuentes (2026-09-14)

- Canal: https://www.youtube.com/@leakcrypto (subtítulos automáticos de
  dweg1ADPyo4 y 9BGlwuJ7or4)
- Turtle Soup, reglas y test: https://www.mql5.com/en/articles/2717 ·
  https://www.turtletrader.com/trader-raschke/
- Minervini, Kullamägi, Darvas, O'Neil:
  https://www.financialwisdomtv.com/post/how-legendary-traders-enter-breakouts-minervini-kullamagi-darvas-o-neil
- US Investing Championship: https://financial-competitions.com/rules
