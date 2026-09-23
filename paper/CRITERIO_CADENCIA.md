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

## Subir el umbral: qué lo justificaría y qué no

Queda anotado que 0,5 ATR puede quedarse corto —y **subirlo** es la dirección
que tendría sentido, no bajarlo—. Pero el motivo importa:

**Sí lo justifica** que el agente siga repitiendo con gráfico nuevo. Si con 0,5
ATR vuelve a dejar órdenes al mismo nivel o a escribir razones calcadas, es que
medio recorrido de vela no basta para que él vea algo distinto, y el umbral está
midiendo mal lo que pretende medir. Se comprueba mirando el registro: dos
apuestas con el mismo nivel y distinto `hecha_en` son la prueba.

**No lo justifica** que la sesión dé pocas vueltas, ni que la muestra crezca
despacio, ni que «con más movimiento decidiría mejor». Esas tres son razones de
comodidad, y bajar o subir un número por comodidad es exactamente el
sobreajuste que convirtió −96R en +88R en `rangeSweepCombo.ts`.

La diferencia en una frase: se sube si el umbral **no está consiguiendo** que
cada vuelta sea una observación distinta. No si consigue menos observaciones de
las que uno querría.

Y si se sube, se anota aquí el valor viejo, el nuevo y qué se vio en el registro
—igual que esta sección—, para que el historial distinga «se midió» de «se
tocó».

## 2026-09-14 · el marco de la espera pasa de 15m a 1h

**Valor viejo:** la vuelta esperaba a que cerrara una vela de **15m** o a 0,5
ATR de 15m. **Valor nuevo:** vela de **1h** o 0,5 ATR de 1h. El 0,5 no se
toca.

**Lo que se vio en el registro y en las trazas (sesiones 7 a 10, con
razonamiento):** una vuelta del modelo dura entre 25 y 40 minutos. Con la
espera en 15m, «que cierre una vela» se cumplía siempre antes de terminar de
pensar: la espera no esperaba nada. Y lo que el modelo relee en cada vuelta
—el rango de 4h, los pools, el régimen de 1h— no había cambiado entre una
vuelta y la siguiente. Diez vueltas sobre el mismo gráfico general no son diez
observaciones, que es lo que este archivo ya decía del 15m contra el 15m.

> Corrección del mismo día: la primera versión de esta sección decía que «la
> sesión 10 pidió el mapa dos veces seguidas». Es falso —la traza enseña una
> sola llamada; se leyeron mal las marcas de tiempo de las velas— y se quita.
> El argumento no dependía de eso, pero un criterio no puede apoyarse en un
> dato que no ocurrió.

**Por qué no es «da pocas vueltas»:** las vueltas no bajan por la espera —ya
las limita el modelo—; lo que cambia es que cada una empieza con algo
distinto que mirar en el marco que decide. Es el argumento de la sección
anterior aplicado un marco más arriba: el umbral de 15m no consigue que cada
vuelta sea una observación distinta.

**Lo que NO cambia:** las órdenes límite y las predicciones se siguen
resolviendo contra velas de 15m —ahí la granularidad fina importa, porque un
toque de un minuto es un toque—. Y el 15m sigue siendo el marco del timing
de la entrada: lo que cambia es cuándo se vuelve a mirar, no qué se mira.

**Cada marco con su pregunta**, que es lo que faltaba escribir: 4h es la
estructura —dónde está el precio en el rango, los pools y FVGs grandes, la
tesis de fondo—; 1h es el régimen y si el impulso sigue o se agota, y el marco
por defecto de las predicciones; 15m es solo el timing —la vela en curso, el
barrido, la entrada—. No se busca en 15m lo que es de 4h, ni al revés.

## 2026-09-14 · modo vigía: el modelo no corre 24/7, despierta por eventos

**Lo que se vio:** al final de la sesión 11 había diez predicciones vivas y
una orden puesta. Las últimas vueltas fueron chocar contra sus propios
niveles hasta encontrar hueco: el modelo ya había dicho lo que tenía que decir
sobre esa estructura, y el 4h —que decide la estructura— cambia cada cuatro
horas, no cada media. Volver a leerlo antes de que cambie es la misma
redundancia que este archivo describió para el 15m y luego para el 1h, un
marco más arriba. Y las órdenes límite ya hacen la espera por él: «entro si
vuelve al borde» no necesita al modelo despierto, necesita la orden puesta.

**La regla:** un vigía sin modelo mira cada 15 minutos, resuelve lo mecánico
—órdenes disparadas, stops tocados, predicciones vencidas o cumplidas— y
despierta al modelo para UNA vuelta solo si ocurre alguna de estas tres cosas:

1. **Cierra una vela de 4h.** La estructura cambió; toca releerla.
2. **El precio se acerca a un nivel vivo** —una orden, una predicción, un
   pool— a menos de 1 ATR de 15m. Es el timing, el único papel del 15m.
3. **Hay una posición abierta** y el precio se acerca a su stop o a su
   objetivo a menos de 1 ATR de 15m.

Con un **tope diario de vueltas** (8) para que un día nervioso no lo vuelva
24/7 por la puerta de atrás, y una **ventana activa**: fuera de ella el vigía
sigue resolviendo lo mecánico, pero no despierta al modelo.

**La ventana, y por qué esa:** 08:00–20:30 hora local de la máquina
(EDT, UTC−4). Cubre los cierres de 4h de las 08, 12, 16 y 20 local —12, 16,
20 y 00 UTC— y la sesión americana, que es cuando BTC se mueve (ver el cron
`sesion-papel.yml`, que ya eligió las 13:30 UTC por eso). Los cierres de las
00 y las 04 local se saltan: la orden puesta hace la guardia de noche y lo
mecánico pone al día a las 08. El reposo garantizado es 20:35 → 07:59, y es
un requisito y no un efecto: la máquina se usa para otras cosas, y hay que
saber cuándo se puede.

**Lo que NO cambia:** los 0,5 ATR y los marcos de la espera de arriba siguen
valiendo para las vueltas dentro de una sesión larga lanzada a mano; el vigía
es otra forma de lanzarlas, no otra regla de cuándo hay gráfico nuevo. Órdenes
y predicciones se siguen resolviendo contra velas de 15m.

**Por qué no es «da pocas vueltas»:** las vueltas no bajan porque el modelo
sea lento, bajan porque el gráfico que decide no cambió. Una vuelta con un 4h
nuevo, o con el precio encima de un nivel que él mismo eligió, vale más que
cuatro sobre el mismo 4h. Y lo que el experimento mide —si las razones
selladas discriminan— no gana nada con presencia.

**Cuándo se revisaría:** si las órdenes o los stops se disparan con el
modelo dormido y al despertar hay tesis que habría querido revisar antes
—medible: cierres por `evaluar_abiertas` en horas de reposo con R que
contradiga la tesis—. Eso sería un hallazgo sobre la ventana, no un ajuste de
comodidad, y se anota aquí con el valor viejo y el nuevo.

## 2026-09-23: una vuelta que falló sin escribir no gasta tope

Medido el 2026-09-22. NVIDIA retiró `deepseek-v4-flash-0731` el día anterior
(410 «Gone»), y como el relevo solo conocía el 404 y el 402, cada vuelta del
brazo nvidia subía el error, se perdía y **se cobraba contra las ocho del día**.
A media tarde el brazo estaba capado sin haber escrito una sola predicción.
Gemini gastó las suyas en 503 de Google. A las 20:00, ante el cierre de 4h, los
**cuatro** brazos dijeron «tope diario alcanzado»; y la operación #21 agotó su
plazo a las 18:30 y el modelo **no pudo decidir sobre ella durante dos horas**
—seis avisos «pero tope diario alcanzado»— por lo mismo.

Dos arreglos, los dos bugs y no cambios de criterio:

- **El 410 es `permanente`** en `agent/relevo.py` (rota y entra al catálogo como
  `retirado`, que no se revisa nunca). Un modelo retirado es tan permanente como
  uno que no existe.
- **Una vuelta que falló sin escribir nada no cuenta contra el tope.** El tope
  existe para acotar el gasto en vueltas que SÍ corren; una que murió antes de
  la primera escritura no gastó nada de lo que el tope acota. Si escribió algo
  antes de morir, sí cuenta: ya produjo muestra. Se mide con
  `registro.ultima_escritura()` antes y después.

Lo que NO se cambia acá y queda propuesto en `paper/HIPOTESIS_PROMPT_V5.md`
(H6): que los cierres de 4h y los plazos agotados **no cuenten para el tope**
aunque la vuelta corra bien. Eso sí es un cambio de cadencia de los brazos de la
comparación, y lo decide quien decide.
