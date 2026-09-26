# La mesa de analistas — fijada ANTES de la primera ronda

> En su propio commit y antes del código, como los otros `CRITERIO_*.md`: si
> las reglas de abajo se escribieran después de ver qué familia acertó, el
> historial no podría distinguir «se decidió antes» de «se eligió porque
> cuadraba». Decidido con Elvis el 2026-09-25.

## La pregunta

¿Los analistas de familias distintas **coinciden** cuando leen el mismo mapa?
¿Y el **consenso** de varios acierta más que cada uno por separado? Si sí, el
trader podría recibir la mesa entera en vez de la lectura de su propio modelo.

Hoy no se puede responder con lo que hay. La fase de analista del prompt v5
(`paper/analista.py`) usa el MISMO modelo del brazo, y cada analista elige sus
propios niveles: dos lecturas sobre niveles distintos no se comparan.

## Fase 1 — la mesa en sombra (desde el 2026-09-25)

- **Cuándo:** después de cada cierre de 4h DENTRO de la ventana del vigía
  (08, 12, 16 y 20 h locales), con `MESA_ESPERA_MIN` de retraso (50 por
  defecto). Los brazos despiertan en ese mismo cierre y su vuelta dura 25-40
  min; la mesa va detrás para no competir con ellos por la cuota por minuto
  (Groq: 8.000 tokens/min).
- **Quién:** un analista por familia de los brazos que estén en `BRAZOS`
  (gemini, groq, nvidia, openrouter), con la lista de modelos de ese brazo.
- **La misma pregunta para todos, fijada por el CÓDIGO:** marco 1h, arriba =
  precio + 1 ATR(1h), abajo = precio − 1 ATR(1h), plazo 24 h (el
  `PLAZO_POR_MARCO` de 1h). Cada analista da su probabilidad de que el precio
  TOQUE cada nivel antes de vencer, y un veredicto: alcista, bajista o
  neutral.
- **El mapa:** el mismo `_mapa` de los brazos, sin el estado de ningún
  registro (la mesa no tiene operaciones).
- **Una muestra por familia y ronda.** Son ~4 llamadas por ronda, 16 al día.
- **Se resuelve por código** contra velas de 15m, como las predicciones: tocó
  o no tocó. El modelo nunca se puntúa a sí mismo.
- **Aviso por Telegram aparte**, uno por ronda: el veredicto y las dos
  probabilidades de cada familia, el consenso y cuánto discrepan. Lo pidió
  Elvis el 2026-09-25.
- **Vive en `$DATOS/mesa.db`**, en el volumen, separada de los registros de
  los brazos.

### ⚠ EL TRADER NO VE LA MESA

Es la condición que hace posible correrla a mitad de la muestra v5: los brazos
siguen exactamente igual (mismo prompt, mismo analista propio, mismas
herramientas) y la comparación v5 no se toca. Lo único compartido es la cuota
de las claves, y por eso la mesa va detrás de la vuelta del cierre y una clave
agotada se salta sin reintentos.

### ⚠ SIN BRIER ANTES DE 50

La misma puerta que los brazos: el Brier de una familia como analista no se
mira ni se enseña hasta que tenga **50 preguntas resueltas** (100 niveles).
Antes solo se cuentan las rondas y la **discrepancia** entre familias, que no
depende del resultado y no invita a ajustar contra ruido.

## Fase 1b — el contexto externo, en A/B dentro de la mesa (desde el 2026-09-26)

Elvis pidió el 2026-09-26 probar con los analistas lo que el mapa no ve: la
macro, el oro, el petróleo, el dólar, las acciones que son BTC en bolsa, los
flujos, los derivados, las opciones, el resto de cripto, lo on-chain, el
sentimiento, el calendario y los titulares. Con una condición suya: **no
saturar el mensaje**.

### Qué se prueba

¿El contexto externo mejora al analista? Se responde con un A/B **dentro de
la misma ronda**: cada familia contesta la MISMA pregunta dos veces,

- `mapa`: solo el mapa, igual que en la fase 1;
- `mapa+externo`: el mapa y, al final, el bloque de contexto externo.

Misma familia, mismo modelo, mismo instante, misma pregunta: lo único que
cambia es el bloque. Las dos llamadas son independientes (la segunda no ve la
primera). La diferencia de Brier entre variantes, por familia, es la medida.

### El bloque, y su tope

Una línea por familia de ejes, solo el valor y su cambio reciente, sin prosa:

- **Macro:** Nasdaq-100, S&P 500, VIX, DXY, bono de 10 años de EE. UU.
- **Refugio y energía:** oro, plata, WTI, Brent.
- **Divisas:** USD/JPY, EUR/USD, USD/CNY.
- **Acciones BTC:** MSTR, COIN, IBIT, MARA, NVDA.
- **Flujos:** ETF spot del día anterior, oferta de stablecoins (7 d),
  Coinbase premium.
- **Derivados:** funding (y su media de 7 d), open interest (4 h), long/short
  de cuentas, base de CME.
- **Opciones:** DVOL, put/call de open interest, el vencimiento grande más
  próximo.
- **Cripto:** ETH/BTC, SOL/BTC, dominancia de BTC y de USDT (USDT.D: si
  sube, el dinero se refugia en stablecoins; la pidió Elvis el 2026-09-26,
  antes de la primera ronda A/B).
- **On-chain y sentimiento:** hashrate, comisiones, Fear & Greed.
- **Calendario:** eventos de EE. UU. de impacto alto hoy y mañana, sesión,
  fin de semana, días a fin de mes.
- **Titulares:** los 3 más recientes de cripto, cortados, sin repetir la
  misma noticia: Finnhub, los RSS de CoinDesk y Cointelegraph y el canal
  público de Telegram de Watcher Guru. X no: pide login y Firecrawl no lo
  lee; las cuentas de noticias rápidas publican lo mismo en Telegram.

**Tope: 1.600 caracteres** (~450 tokens) para todo el bloque. Lo que no quepa
se corta por el final (titulares primero). Una fuente que no responde se
omite en silencio del bloque y queda anotada en `mesa.db`: el bloque nunca
dice «sin dato» once veces.

⚠ **SON DATOS DE AFUERA Y VAN MARCADOS COMO TALES.** El bloque entra con el
encabezado de CONTENIDO EXTERNO: los titulares los escribe un tercero y no
son instrucciones.

⚠ **NINGÚN NÚMERO LO CALCULA UN MODELO.** Cambios, medias, premium y base los
calcula el código a partir de las fuentes; el bloque se guarda entero con
cada ronda para poder revisar después qué vio cada analista.

### Coste

Ocho llamadas por ronda en vez de cuatro, 32 al día. Para Groq (8.000
tokens/min) la segunda tanda espera un minuto a la primera. Los datos salen
de fuentes gratis sin clave, más Firecrawl (capa gratis, 1.000 créditos al
mes: ~4 por ronda) y Finnhub para titulares.

### Qué no cambia

La fase 1 sigue siendo la variante `mapa`, así que su serie no se corta. El
trader sigue sin ver la mesa. Y la puerta de 50 vale **por familia y por
variante**: no se mira ninguna diferencia antes de 50 rondas resueltas con
las dos variantes.

## La tasa base, como un analista más (desde el 2026-09-26)

Decidido con Elvis el 2026-09-26, tras la evaluación de qué pagar: antes de
pagar un modelo hace falta la vara con que medirlo. El mapa ya le da a cada
analista la tasa base de su pregunta —cuántas veces, en las últimas velas de
1h, un nivel a 1 ATR se tocó dentro de 24 velas— y la instrucción le pide
partir de ella. Pero no se guardaba: no había forma de saber si un analista
le gana al número que ya tenía delante.

- **Qué se guarda:** en cada ronda, la tasa base de 1h a 1 ATR que trae el
  mapa de ESA ronda, leída del texto del mapa (el mismo número que leyeron
  los analistas, no uno recalculado). Va como una fila más de `respuestas`,
  familia `tasa base`, modelo `mapa`, con la misma probabilidad arriba y
  abajo (la tasa base promedia los dos lados). Una fila por variante de la
  ronda, para que cada variante se compare en sus mismas rondas.
- **No cuenta como analista:** no entra en el consenso, en la discrepancia
  ni en el «contestaron» del registro. En el aviso de Telegram va en una
  línea aparte.
- **La medida:** para cada familia, el Brier Skill Score contra la tasa base
  en las MISMAS rondas y variante: `1 − Brier(familia) / Brier(tasa base)`.
  Positivo, la familia le gana; cero o negativo, no aporta nada que el mapa
  no dijera ya.
- **La misma puerta:** nada de Brier ni de skill antes de 50 resueltas por
  familia y variante. La tasa base también la cruza: su Brier sale con las
  mismas 50.
- **Las rondas anteriores no se rellenan:** el texto del mapa no se guardó y
  la tasa base cambia con las velas.
- ⚠ **ATR distinto, por poco:** la tasa base del mapa usa el ATR de cada
  vela de producción (`calculateATR`), y los niveles de la pregunta usan el
  ATR simple de 14 velas de `paper/mesa.py`. La diferencia es pequeña y es la
  misma para todos: la vara es la que el analista tenía delante.

Es la línea base que pide la literatura de pronóstico («copy the market»):
un modelo de pago solo se evalúa si le gana a esta.

## Fase 2 — la mesa como dato del trader (prompt v6)

**No empieza antes** de que los brazos crucen las 50 predicciones resueltas en
v5. Con los datos de la fase 1 se decide:

- si el consenso de la mesa tiene mejor Brier que cada familia sola y que el
  analista propio de cada brazo (`extra.analista` de v5);
- si la discrepancia sirve: ¿cuando la mesa discrepa mucho, fallan más?

Si la respuesta es sí, v6 le da al trader la mesa (consenso + quién discrepa)
en lugar de su analista propio, con **su propia muestra de 50**. Si es no, no
se cambia nada y el experimento a ciegas se ahorró. Es una variable de
conducta: la decide Elvis con las cifras delante.

## Dónde está cada cosa

- `paper/mesa.py`: la ronda, el registro (`mesa.db`), la resolución, el aviso
  y `--informe`.
- `railway-vigia-service/arrancar.sh` (repo `mi-dashboard-trading`): la lanza
  junto a los brazos con las claves de cada uno. Si la mesa cae, los brazos
  siguen: no está en la lista de procesos que tumban el contenedor.
