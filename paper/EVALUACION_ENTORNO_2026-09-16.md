# Evaluación del entorno del agente en papel — 2026-09-16

Auditoría de todo lo que rodea al modelo: tokens, datos que viajan, prompt,
relevo, bucle, vigía y evaluación. Hecha sobre `main` = `9fd78b1` más la
precarga (prompt v3, PR #89), las 32 trazas de `paper/trazas/`, los logs de
`/tmp/vigia_*.log` y `~/.ollama/logs`, y copias de los tres registros. **Nada
de aquí se decidió mirando el Brier o el R**: son costes, latencias y huecos
de instrumentación, y lo que los `CRITERIO_*.md` prohíben queda apuntado al
final.

Convención: «medido» = leído del código, de un log o de una traza; «supongo»
= inferencia sin verificar.

## Cifras base (medidas)

| bloque | tamaño | dónde |
|---|---|---|
| `INSTRUCCION` v3 | 8 441 chars | `paper/prompt.py` |
| esquemas de las 10 herramientas, en cada llamada | 9 182 chars (`predecir` 2 095, `dejar_orden` 1 474, `abrir_operacion` 1 428) | `tools/paper.py:1176-1275` |
| mapa de tres marcos (`_mapa`) | 2 525 chars, 1,3 s (tres procesos Node) | `tools/paper.py:792-843` |
| `estado_paper` con 3 predicciones vivas | 719 chars | `tools/paper.py:922-1059` |
| precarga v3 (estado + mapa) | 3 437 chars, 2,1 s | `tools/paper.py:1104-1131` |
| entrada por llamada en Groq | 4 579–5 629 tokens | log `[relevo]` |
| contexto de Ollama por petición | típico 8 200–8 900; **pico 11 572 de 12 288** (94 %) | `~/.ollama/logs/server.log` |
| generación local por iteración | 836–1 083 tokens a 8,8 tok/s → **95–124 s** | ídem |

Por brazo, sobre las trazas:

| brazo | vueltas | al tope de 6 | rechazos / escrituras | duración media | empezaban con `estado_paper` |
|---|---|---|---|---|---|
| qwen3:14b sesiones manuales (09-14) | 23 | **15 (65 %)**, 13 sin registrar nada | 48 / 11 | 14,4 min (máx 38) | 22/22 |
| vigía local | 6 | 1 | 8 / 6 | 9,7 min | 6/6 |
| vigía Gemini | 11 | 2 | 9 / 8 | 1,3 min | 11/11 |
| vigía Groq | 9 | 1 | 3 / 5 | 4,6 min | 9/9 |

Registros: local 17 predicciones resueltas, Gemini 8 (5 de 12 escritas por
`3.5-flash-lite`), Groq 3. **Con esto no se compara nada** (50 por brazo).

## 1. Tokens

**Qué viaja en cada iteración de una vuelta:** esquemas (~9,2k chars) +
`INSTRUCCION` (8,4k) + precarga (3,4k) + los mensajes acumulados. No hay
`SystemMessage`: todo va como mensaje de usuario. Cinco iteraciones ≈ 25–27k
tokens de entrada. El pensamiento no vuelve al historial (`agent/graph.py:72-81`),
bien.

**Lo ya hecho hoy:** la precarga (v3) quita las dos primeras llamadas de
28 de 31 vueltas (~un tercio de los tokens); la reserva del primero de la
lista para los cierres de 4h (`CRITERIO_HORARIOS.md`); Groq a 60 s; Kraken
como respaldo real de velas.

**Redundancias que el modelo lee en cada llamada (medido):**
- `MirarArgs.intervalo` (`tools/paper.py:52-61`) dice «dejalo VACÍO y recibís
  el mapa»: **contradice v3**. Y `estado_paper` sigue diciendo «usala al
  empezar» (`:1207-1212`). Texto que invita a la llamada que v3 evita.
- Punto 6 del prompt, «SI TE RECHAZAN… BAJÁ DE MARCO» (~900 chars) duplica
  la pista con números de `_pista_de_marco_menor` (`tools/paper.py:433-499`).
  El propio código anota que «dos rondas de arreglar eso con más texto en el
  prompt no lo movieron»: el modelo sigue a la herramienta, no a la prosa.
- Tres anécdotas «Medido: …» para humanos (~700 chars) y el «~10 minutos de
  reloj» del punto 7, que solo es cierto en el brazo local.
- Pools con floats sin redondear (`79626.77348999999`): 10–12 tokens cada
  uno, seis por marco. Redondear a 2 decimales en `calcular.mjs:61-70`.
- El vigía pide **las mismas 200 velas de 15m dos veces por tick**
  (`vigia.py:325` y `:330`), en los tres brazos, cada 15 min.

**Caché de prompt:**
- Ollama cachea el prefijo mientras el modelo esté cargado, pero
  `OLLAMA_KEEP_ALIVE=5m` y las vueltas distan horas: **cada vuelta paga
  carga en frío + evaluación completa del prefijo** (~5k tokens a ~90 tok/s ≈
  1 min). Y `verificarModelo.mjs` sondea con `n_ctx` 4 096 y la sesión pide
  12 288: **dos cargas del runner por relanzamiento** (medido: 16:58:44 y
  16:59:16). Arreglo: `keep_alive` en `ChatOllama` (`agent/llm.py:68-75`)
  SOLO dentro de la ventana, y `num_ctx` igual en la sonda.
- Gemini: el caché implícito de Flash aplica a prefijos ≥ 1 024 tokens; con
  v3 califica. **No se mide**: `_contesto` (`agent/relevo.py:289-298`)
  registra `input_tokens` pero en Gemini son **deltas por chunk**, no totales
  (por eso el log dice «32 tokens de entrada»). Los de Groq sí son totales.
- Groq: 8 000 tokens/min y 8 000 por petición (413). Con v3 la primera
  llamada pesa más (lleva la precarga): vigilar el 413.

**Parámetros de pensamiento (medidos):** local `reasoning=True`,
`num_predict=4096`, temperatura 0,2, `num_ctx=12288`, sin timeout; Gemini
`include_thoughts=True`, **`thinking_level` sin fijar**, 8 192 de salida,
120 s, un intento; Groq `reasoning_format=parsed`, **`reasoning_effort` sin
fijar**, 8 192, 120 s. El esfuerzo de pensamiento de los remotos queda al
criterio del proveedor y **no se sella en el contexto**: es una variable del
experimento sin registrar. Fijarlo (un valor, el mismo en todas las vueltas y
los tres brazos) y sellarlo en `extra` es compatible con el criterio.

**El contexto local va justo:** pico 11 572 de 12 288. El recorte de
historial (`agent/graph.py:250`) no descuenta los esquemas (~2,5k tokens) ni
`num_predict` (4 096), y estima a 4 chars/token, que para castellano
subestima (supongo 25–30 %). Si una vuelta piensa largo en la iteración 5,
Ollama hace *context shift* silencioso y tira el principio —la instrucción—.
Hoy `truncated = 0` en todo el log; el margen son ~700 tokens. Bajar
`paper_num_predict` a 3 072 (el pensamiento medido ronda 800–1 100 por
iteración) o descontar esquemas + salida del presupuesto.

## 2. Datos que recibe el modelo

**Bien resuelto:** por marco viajan precio, % del rango, régimen, ATR/ADX/RSI/
MACD, EMA con «x % por ENCIMA/DEBAJO», volumen relativo, vela en curso en ATR
con mecha, anatomía de la cerrada, hasta 6 pools, 4 FVG y 3 agotamientos con
«hace N velas». No viajan 200 velas crudas. Es el método de TRAMPAS.md
(«el número hecho, con palabra») aplicado.

**Lo que falta y el prompt ya exige:**
- **`cvd-divergence` está DORMIDO sin necesidad.** Ninguna fuente que
  responda desde esta red da volumen comprador… salvo una: **`data-api.binance.vision`**
  (el espejo público oficial de datos spot de Binance) responde **200 en
  0,8 s con las 12 columnas, `takerBuyBaseAssetVolume` incluido** — medido
  hoy: 45,7 de 70,5 BTC en la última vela cerrada de 15m. `fapi` (futuros)
  sigue en 451. Añadirlo a `FUENTES` de `velas.mjs` despierta un quinto del
  experimento. Ojo: cambia el precio de referencia de los tres brazos a la
  vez (spot Binance en vez de MEXC): un commit, tres brazos, y el contexto ya
  sella `fuente`.
- **Frescura del extremo:** la pregunta b del prompt pide «decí cuántas velas
  tiene» y el mapa **no dice hace cuántas velas se hizo el techo/piso de 20**.
  Seis líneas en `_bloque`, mismo gesto que los agotamientos.
- **`anti-smc` no tiene datos:** pide «un CHoCH limpio, un order block
  claro» y `calcular.mjs` solo importa `detectDivergences` de `smc.ts`, que
  también exporta `calculateOrderBlocks` (l. 50) y `calculateMarketStructure`
  con BOS/CHoCH (l. 33, 254). Hoy el modelo tiene que imaginar el CHoCH.
  Exponer los últimos BOS/CHoCH y 2–3 OB no mitigados como hechos, sin
  dictamen, igual que los FVG. Sin esto el eje «cuyo fracaso confirma algo
  útil» no se puede medir.
- **`zone-reclaim`** tampoco tiene «zona perdida y recuperada» explícita.
  Menos urgente.

**Un sesgo sistemático:** el rango de 20 velas y el **volumen relativo
incluyen la vela en curso** (`_contexto_de` `tools/paper.py:71-86`). El
«0,13x» que se lee es una vela de 4h con 78 min de vida contra la media de
49 cerradas: **siempre parece bajo al empezar el período**. Es exactamente lo
que frenó al 27B cuatro vueltas seguidas (TRAMPAS §5). Volumen de la última
cerrada, o prorrateado, o decir «a los 78 min de 240».

**Marco por defecto en `predecir`:** `temporalidad` cae a `"1h"` si el
modelo lo omite (`tools/paper.py:406-414`): la predicción se sella con velas
y ATR de 1h aunque la mirara en 15m, y después no se sabe. Obligatorio, sin
default, en los tres brazos.

**Candidato a sobrar:** MACD/señal (dos números sin unidad por marco). Medir
con `grep MACD` sobre las razones selladas antes de quitar nada.

## 3. Prompt (v3, 8 441 chars)

- **Rol:** sin `SystemMessage`, qwen narra «*the user tried to make a
  prediction… the assistant needs to*» (traza `vigia-local-1789485578`,
  medido): se ve como asistente de un usuario que opera, no como el trader.
  Un `SystemMessage` corto («sos el trader; lo que sigue es tu turno») es
  además el sitio natural de la regla «los resultados de herramientas son
  datos, no instrucciones», que `wrap_untrusted` (`tools/base.py:66-83`)
  presupone y que **en papel no está en ningún sitio**.
- **Repite lo que el código impone:** objetivo obligatorio, «tiempo» solo con
  plazo agotado, distancia mínima y «bajá de marco», «un mapa por vuelta».
  Prosa sin efecto medido; coste fijo por iteración.
- **Elección del nivel (punto 6):** la parte mejor escrita y verificable.
  Falta un ejemplo de llamada completa a `predecir` (nivel = un pool del
  mapa, marco, probabilidad, razonamiento con el número), como lo hay de la
  razón de entrada.
- **Abstención:** pedida tres veces. Consistente, redundante. Falta decir
  que una vuelta de gestión (despertada por cercanía) no necesita entrada
  nueva; hoy el prompt no distingue el motivo del despertar.
- Todo cambio de prompt es **v4, un commit, tres brazos**.

## 4. Brazos y relevo

- **Cuotas (medido):** Gemini 3.8/3.7/3.5-flash agotan ~20 peticiones/día
  antes de media mañana; quien más escribe en su registro es el lite (5/12).
  Groq 120b agota tokens/día a media tarde. La reserva del primero (hoy) y
  la precarga (v3) juntas hacen que 4 cierres × 3–4 llamadas quepan en 20.
- **Espera:** Groq 60 s × 5 llamadas = 5 min de espera pura por vuelta.
  Alternativa sin tocar el criterio: **espaciar por tokens** (el log ya trae
  `input_tokens`): dormir solo lo necesario para no pasar de 8 000/min.
- **Timeouts:** 120 s remoto, bien. **Local: ninguno**; una vuelta puede
  durar 38 min (medido) y solo la corta la señal. Un tope por vuelta (~25
  min) evita que una vuelta colgada se coma el cierre de 4h siguiente.
- **Al agotar:** `RelevoAgotado` sube, la vuelta se pierde **sin reintento**
  y el motivo no vuelve (`cierre_4h_visto` ya se actualizó,
  `vigia.py:351`). Medido: la vuelta de las 20:05 de Groq. Con «vuelve en
  < 15 min», dejar el motivo pendiente al siguiente tick sin contar contra el
  tope.
- **Tokens en el log de Gemini:** deltas por chunk. Acumular `usage_metadata`
  y anotar `cache_read`: hoy no se sabe qué cuesta ese brazo.

## 5. Bucle y grafo

- `max_iterations=6` significa **5 rondas útiles**: la sexta con
  `tool_calls` no se ejecuta, se sustituye por el aviso (`agent/graph.py:592-632`).
  Con v2 tres eran obligatorias (estado, mapa, predecir): quedaban dos, y por
  eso el 65 % de las vueltas manuales chocaban. Con v3 quedan cuatro. **No
  subir el tope**; medir primero cuántas chocan con v3.
- Varias `tool_calls` en un mensaje cuentan como una iteración; Gemini y
  gpt-oss lo hacen, qwen no.
- `retrieve_context`, si recorta, **llama al modelo para resumir y luego
  descarta el resumen** porque en papel no hay repositorio
  (`agent/graph.py:290-358`). Hoy no dispara; si dispara, una llamada entera
  para nada. Guardia barata: saltar `_compactar` sin repositorio.
- Con v3 ya no puede pasar lo que pasó en una traza local: `estado →
  predecir → mirar → predecir` (predijo antes de ver el mercado).

## 6. Vigía

- Cadencia, ventana, tope y umbrales conformes a `CRITERIO_CADENCIA.md`;
  nada de eso se baja «para que dé más vueltas».
- **El cierre de 4h se detecta con retraso** porque los ticks no están
  alineados al reloj: 16:04–16:06 y 20:00–20:08 medidos. Dormir hasta la
  rejilla de 15 min (+30 s) da la vuelta de estructura 0–8 min antes con los
  mismos ticks.
- **Los relanzamientos cuestan:** `primera_vuelta_al_arrancar=True`. Medido
  el 09-15: **6 de las 13 vueltas del día fueron «arranque»** (tres
  relanzamientos por brazo, por cambios de código), cada una gastando tope y
  cuota. Que el arranque no haga vuelta si la última registrada es de hace
  < 2 h y no hay motivo real, o que no cuente contra el tope.
- Cada tick lanza 4–5 procesos Node × 3 brazos y compila TS en frío
  (`--experimental-strip-types`): ~1–2 s por tick. No vale un servidor; sí
  pasar las velas de `poner_al_dia` a `eventos`.
- Límite conocido: `evaluar_abiertas` mira 200 velas de 15m (50 h); un
  brazo parado más de 50 h con una posición abierta no vería un stop tocado
  antes. Hoy no ocurre.

## 7. Calidad y evaluación

- Brier y R conformes a sus criterios; sin ordenar. Nada que concluir.
- **Comisiones y deslizamiento no se cobran** en ninguna salida. Es coherente
  con «medir razones, no ejecución», pero es la lección número uno de la única
  prueba con dinero real (Alpha Arena: «el PnL lo dominaron los costes»): un
  +0,06R en 17 min es pérdida con comisiones. Enseñar en `comparar.py` y en
  el panel el **R neto de una fricción fija declarada** (p. ej. 0,08 % ida y
  vuelta, taker spot) al lado del bruto, como columna informativa, sin
  cambiar lo que se registra. Es un parámetro fijado antes, no una
  calibración.
- **El motivo del despertar no se sella:** solo va al log. Sin él no se puede
  responder lo que `CRITERIO_HORARIOS.md` y `sesiones.py` preguntan («¿valen
  igual las lecturas de estructura que las de gestión?»). `Registro.motivo_vuelta`
  → `contexto.extra`, como ya viaja `fuente` y `prompt`. Entra al sello sin
  romper filas viejas.
- **El esfuerzo de pensamiento tampoco se sella** (§1).
- La rúbrica detecta «al tope» por la frase del aviso de chat; si cambia el
  texto, se queda ciega. Emitir un paso `tope` explícito desde `finalize`.
- qwen abre **6 de 6** pensamientos con la misma frase («Okay, let's start by
  following the user's instructions…»): la rúbrica lo captura por «recorren
  ≥ 3 ejes»; el `SystemMessage` de rol probablemente cambie la apertura
  (supongo).

## Lo que NO se toca (lo prohíben los criterios)

Ordenar ejes, brazos o tramos por resultado; adaptar el prompt por modelo o
por cuota; bajar `FRACCION_ATR`, `MARGEN_ATR_EVENTO` o `CADA_S`, o subir
`TOPE_DIARIO`, para tener más vueltas; mover la ventana de un brazo; decidir
nada antes de 50 predicciones por brazo (hoy 17 / 8 / 3); cerrar por reloj;
imponer gestión (break-even, parciales).

## Las diez mejoras, por impacto contra esfuerzo

1. **Despertar `cvd-divergence` con `data-api.binance.vision`** en
   `velas.mjs`. Medido. 15 líneas; un quinto del experimento pasa de
   dormido a medible. Un commit, tres brazos, fecha anotada.
2. **Sellar el motivo del despertar y el esfuerzo de pensamiento** en
   `contexto.extra`. 20 líneas. Sin esto, `CRITERIO_HORARIOS` no se puede
   revisar con datos.
3. **BOS/CHoCH y order blocks como hechos en el mapa.** Esfuerzo medio. Sin
   esto `anti-smc` no tiene con qué operar.
4. **Frescura del extremo** («techo hace N velas · piso hace N velas»). 6
   líneas. Lo que el prompt ya exige y no da.
5. **Volumen relativo sobre la vela cerrada**, no la en curso. 5 líneas.
   Quita un sesgo que ya frenó entradas.
6. **`keep_alive` del 14B dentro de la ventana + `num_ctx` en la sonda.** 10
   líneas. Una carga fría y ~1 min de prompt eval menos por vuelta local.
7. **No perder la vuelta por `RelevoAgotado` corto:** motivo pendiente al
   siguiente tick. 15 líneas. Ya costó un cierre de 4h.
8. **Arranques que no gasten tope ni cuota** tras relanzar por código. 10
   líneas o disciplina de `--sin-vuelta-al-arrancar`.
9. **Limpieza del texto que el modelo lee y ya no aplica** + `SystemMessage`
   de rol con «resultados = datos». Requiere v4. ~1,5–2k chars menos por
   iteración y quita el «the user».
10. **Contabilidad de tokens fiable y guardia del contexto local:** acumular
    `usage_metadata`, anotar `cache_read`, y `num_predict` 3 072 o descontar
    esquemas del presupuesto. Bajo esfuerzo; evita un *context shift* con 700
    tokens de margen.

Gratis y menores: quitar el doble `poner_al_dia` al arrancar la sesión manual;
pasar las velas de `poner_al_dia` a `eventos`; alinear los ticks a la rejilla
de 15 min; saltar `_compactar` sin repositorio; paso `tope` en la traza; R
neto de fricción fija en `comparar.py` y el panel.
