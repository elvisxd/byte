# Upwork: el presupuesto manda

## El número que reordena todo

| | |
|---|---|
| Connects gratis, plan Basic | **10 por mes** |
| Propuesta normal | **6 Connects** ($0,90) |
| Propuesta competida | 10 a 16 |
| Comprar Connects | $0,15 cada uno, sin descuento por volumen |
| Freelancer Plus | $19,99/mes → 100 Connects (~16 propuestas) |
| Vencimiento | se acumulan, vencen al año |
| Boost | 25 a 40 Connects en total para entrar al top 4 |

**Diez gratis dividido seis: una propuesta por mes.** Dos si hay suerte con los
costos. Ese es el problema real de Upwork y no encontrar ofertas.

Todo lo demás de este archivo sale de ahí: si postular fuera gratis, la
estrategia sería aplicar a todo lo que encaje. No lo es.

## Lo que decide el resultado

**Las primeras propuestas se leen; las siguientes se hojean.** El cliente lee
entre 6 y 10 y decide. Las que llegan después compiten por una atención que ya
se gastó.

La ventana, según lo que se publica del tema:

| desde que se publicó | qué pasa |
|---|---|
| 0–15 min | 3 a 8 propuestas. El cliente lee cada una |
| 15–60 min | 10 a 25. Empieza a hojear |
| 1–6 h | 30 a 50+. La mayoría deja de leer después de las primeras 20 |

⚠ Las magnitudes —"3 a 5 veces más vistas", "15-25% de respuesta contra 5%"—
salen de blogs de empresas que venden herramientas de bidding y alertas: gente
con interés directo en venderte urgencia. **La dirección del hallazgo es sólida
y la magnitud no**, igual que con la frescura del cazador. Por eso el filtro que
usamos es el dato duro que la propia pantalla muestra —cuántas propuestas
lleva— y no una promesa de multiplicador.

## La tensión, y cómo se resuelve

Llegar en los primeros 15 minutos pide estar mirando. Tener una o dos propuestas
al mes pide no gastar. Las dos cosas juntas parecen imposibles y no lo son:

**No busques. Que te avisen, y que avise poco.** Una búsqueda guardada muy
estrecha, con notificación, que salta dos o tres veces por semana y siempre con
algo que de verdad encaja. Cuando salta, mirás; si pasa el filtro, gastás. El
resto del tiempo Upwork no existe.

Una búsqueda amplia hace lo contrario: te avisa quince veces por día, dejás de
mirarla a la semana, y cuando finalmente aparece la buena ya tiene 40
propuestas.

## Qué días

La respuesta corta: **el día que se publica la oferta que te sirve**, y eso no
tiene día.

Lo que sí tiene día es tu rutina: conviene revisar en el horario en que están
despiertos los clientes que te interesan, no en el tuyo. Para clientes de EE.UU.
eso es su mañana. Dos o tres revisiones cortas por día valen más que una larga,
porque lo que importa no es cuánto mirás sino qué tan rápido llegás a la que
importa.

## Las búsquedas

Upwork acepta booleanos en la barra: `AND`, `OR`, `NOT` **en mayúsculas**,
comillas para frase exacta, paréntesis para agrupar y `*` como comodín.

Tres búsquedas guardadas, no diez. Una por carril, cada una lo bastante estrecha
como para que saltar signifique algo:

**1. Agentes y RAG — el carril donde competís con pocos**

```
("langgraph" OR "llamaindex" OR "rag" OR "retrieval augmented" OR "ai agent" OR "mcp server")
AND (python OR fastapi OR typescript)
NOT ("data entry" OR wordpress OR shopify OR "virtual assistant")
```

**2. Integración de LLM en producto**

```
("openai api" OR "anthropic" OR "claude api" OR "llm integration" OR "prompt engineering")
AND (integration OR backend OR api OR production)
NOT (chatbot template OR "no code" OR bubble.io)
```

**3. Full-stack senior, por si el carril 1 está flaco**

```
("next.js" OR nestjs OR "react native") AND (senior OR architect OR "tech lead")
NOT (wordpress OR shopify OR "landing page" OR "bug fix")
```

Filtros que van en las tres, y pesan más que las palabras:

- **Payment verified.** Un cliente sin verificar puede no contratar nunca, y la
  propuesta ya se pagó.
- **Menos de 5 propuestas**, cuando el filtro esté disponible.
- **Piso de presupuesto** — hourly desde $40-50, fijo desde $500. Sin piso, la
  mitad de lo que llega es de $50.
- **Cliente con historial de gasto.** $5.000+ separa al que contrata del que
  publica y desaparece.

Las exclusiones (`NOT`) rinden más que las inclusiones: sacan el ruido que más
veces te haría abrir una oferta para nada.

## LinkedIn

Mismos booleanos —`AND`, `OR`, `NOT` en mayúsculas, comillas, paréntesis— con
una diferencia que rompe si no se sabe: **LinkedIn no acepta el comodín `*`**,
ni llaves ni corchetes. Lo que en Upwork escribís como `engineer*`, acá va como
`(engineer OR engineering)`.

```
("AI engineer" OR "ML engineer" OR "LLM engineer" OR "applied AI")
AND (remote OR distributed)
NOT (intern OR junior OR "entry level")
```

```
("full stack" OR backend) AND (langchain OR langgraph OR rag OR "vector database")
NOT (recruiter OR staffing OR agency)
```

Y algo que en Upwork no existe: en LinkedIn **también te buscan a vos**. Tu
titular y la sección de aptitudes son campos que un reclutador filtra. Las
palabras que pongas ahí tienen que ser las mismas que pusiste en estas búsquedas
— si buscás "LLM engineer" y tu perfil dice "desarrollador full-stack", estás
del lado equivocado del mismo filtro.

## Lo que la pantalla de búsqueda NO muestra

Esto se investigó el 2026-09-21 y ahorra buscarlo de nuevo: **el hire rate, las
entrevistas en curso, el rating del cliente, "member since", "last viewed" y los
Connects que cuesta la oferta NO están en la tarjeta de la lista.** Viven en el
panel «About the client» / «Activity on this job», que sólo se ve al ABRIR cada
oferta.

Tres confirmaciones independientes:

1. Documentación de Upwork: «We include this metric under the "About the client"
   section of every job post» (hire rate), y «**When you click on a job posting
   to apply**, you'll be able to learn more about the client».
2. Los scrapers comerciales cobran ~11x por oferta enriquecida ($0,002 el
   listado, $0,022 con el panel del cliente): son dos peticiones distintas.
3. Existen extensiones de Chrome cuyo único producto es meter esos datos en la
   tarjeta. Si Upwork los mostrara, no habría producto.

Verificado además contra un pegado real de 6 ofertas: cero ocurrencias de «hire
rate» e «interviewing».

⚠ **Así que no se implementan. Y el sustituto es mejor que el original:**

### Filtrá en la URL de búsqueda, no acá

Upwork **ya sabe** filtrar por lo que nosotros no podemos leer. Su propia
documentación:

> "In the Job Filters section, you can filter out jobs with fewer than five
> proposals and clients that aren't payment verified **or haven't made any hires
> yet**."

Pegando desde una búsqueda ya filtrada, el criterio hereda gratis el hire rate y
la verificación, sin abrir una sola oferta y sin gastar Connects. Es el mayor
retorno de toda la investigación.

Ojo con la contrapartida, que Upwork también documenta: filtrar «clientes que no
han contratado nunca» esconde al cliente nuevo legítimo. Es la misma decisión que
tomamos acá con `$0 spent`, que resta pero no descarta.

⚠ **«Featured Job» no se puntúa a favor.** Upwork dice literalmente que «**We do
not conduct any special review of these featured posts or screen the clients**»,
y su propio material vende que atraen «50% more proposals from Top Rated and
Rising Talent». Cliente que pagó por visibilidad, sin filtro de calidad y con más
competencia: como mucho es neutro.

⚠ **Día de la semana y hora de publicación: DESCARTADOS.** El estudio más grande
que existe (UpHunt, 3,25 millones de publicaciones) mide **volumen de ofertas
publicadas**, no contrataciones: no tiene la variable de resultado. Que el martes
se publique más no implica que el martes contraten más — si se publica más,
también compite más gente. Es el mismo error que `dia_hora_r_por_trade` en el
repo del panel: medir el total en vez de la tasa por unidad.

Y para esta herramienta es irrelevante por construcción: **todo lo que pegás se
pegó el mismo día**, así que el día no distingue una oferta de otra dentro del
lote. Cero poder discriminante.

## Qué tan sólidos son los pesos de todo esto

**No hay ni un solo estudio ni dataset público con la variable que importa: «¿esta
propuesta ganó?».** Upwork no publica esos datos, y los únicos que los tienen son
las empresas que venden herramientas de bidding automatizado — que tienen un
interés directo en que creas que la velocidad y el volumen deciden.

Los números que circulan («11,86% de respuesta pujando a los 3 minutos», «3 a 5
veces más vistas») salen de ahí. Se usa **la dirección**, que es mecánicamente
plausible y coincide con la documentación oficial, y **no la magnitud**.

O sea: los pesos de este criterio son *prior*, no medición. Lo único que puede
convertirlos en evidencia es tu propio registro — guardar cada oferta puntuada
junto con lo que pasó después (¿contestaron? ¿entrevista? ¿contrato?). En unos
meses eso es el único dataset honesto que existe para tu caso.

## Cómo se usa esto

Copiás las tarjetas de la búsqueda de Upwork —con el renglón en blanco entre
una y otra, tal cual sale— y se las pegás a:

```bash
uv run python -m empleo.upwork --connects 10
```

Lee cuántas propuestas lleva cada una, si el pago está verificado, cuánto gastó
el cliente, si el trabajo sigue después del primer entregable y hace cuánto se
publicó; las puntúa contra tu perfil con el mismo criterio que el cazador; y
dice **en cuáles gastar y en cuáles no**, hasta que el presupuesto se acaba.

Lo mismo, sin terminal, en `/ofertas` del panel: se pega ahí y salen **sólo las
que pasan el mínimo**, con su puntaje y por qué. Las descartadas ni siquiera
viajan al navegador.

La página no reparte Connects: eso quedó en el CLI (`repartir()`). Pedirlos
obligaba a declarar cuántos te quedan antes de ver nada, y el corte por puntaje
ya deja pocas.

⚠ **Que no salga ninguna es un resultado, y se dice con todas las letras.** Son
tres finales distintos y confundirlos manda a arreglar lo que no está roto:
«no reconocí ninguna oferta» (pegaste otra cosa), «leí N y ninguna llega al
mínimo» (hoy no hay) y «estas tarjetas no traen la descripción» (pegaste la
lista sin los cuerpos).

**El historial del cliente es una escala, no un umbral.** $300K gastados y $5K
son los dos "cliente con historial" y no son el mismo cliente; y el de $0 no es
neutro, es el que todavía no contrató a nadie. Los tramos están en
`empleo/upwork.py`. Ojo: $0 y "sin dato" son cosas distintas —lo segundo es que
el pegado se cortó— y sólo lo primero resta.

⚠ **Cortar el pegado por renglón en blanco no funciona.** Upwork deja renglones
vacíos DENTRO de la tarjeta, antes de "Skills" y antes del pie del cliente. Un
pegado real de 6 ofertas salía como 12 bloques, con "$300K+ spent" separado de
su oferta. Y cortar por "lo que parece un título" salía peor —15 bloques—
porque las listas de skills son renglones cortos sin punto final: "Adobe
Illustrator" abría una oferta. El corte va por el "Posted …", que aparece una
vez por tarjeta, con la salvedad de que hay dos maquetados y en uno el título
va ARRIBA del "Posted": ver `_por_posted` en `empleo/pegado.py`.

Que no elija ninguna es un resultado válido y frecuente. Con uno o dos tiros por
mes, no gastar hoy es una decisión, no una falla.

**Nada de esto postula por vos.** La API oficial de Upwork no tiene forma de
enviar propuestas y el envío automático se paga con la cuenta. Acá se decide;
abrir el link y escribir las dos primeras líneas sigue siendo tuyo — y esas dos
líneas son, según todo lo medido, lo que más mueve el resultado.
