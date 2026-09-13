# paper/

Operaciones en papel registradas de forma que sirvan para aprender de los
fallos, no para justificarlos después.

`CRITERIO_ABORTO.md` va primero y está commiteado **solo**, sin una línea de
código, porque el repo de trading dejó anotado lo que pasa cuando no: *"este
archivo entró en el mismo commit que los resultados, así que el historial no
puede distinguir «se fijó antes» de «se escribió después»"*.

## Lo que se registra, y por qué así

Un backtest sabe que la señal se disparó y qué pasó después. No sabe **por qué**
se entró ni qué más ocurría en el gráfico. Eso es lo que falta para poder
preguntar "¿qué tienen en común mis últimas cinco pérdidas?".

- **La razón se sella al abrir**, con un hash del contexto y el texto. No como
  convención: `verificar_sellos()` devuelve los ids que cambiaron. La tentación
  real no es inventar una operación, es ajustar la razón cuando ya se sabe cómo
  salió.
- **Los números los calcula el código.** Precio, riesgo, R múltiplo. Ya está
  medido que un modelo de 8B da 1.43 donde el valor real es 17.35 — el modelo
  elige **cuándo y por qué**, el código calcula **qué pasó**.
- **El contexto se guarda entero**, aunque el eje que generó la señal use solo
  una parte: el objetivo es poder preguntar después "¿fallaba más los miércoles?"
  sin haber decidido de antemano que el día importaba.

## Los ejes

Salen de `rangeSweepCombo.ts` del repo de trading, donde ya están identificados:
velas mínimas en rango, ancho máximo, margen del stop, múltiplo del objetivo,
régimen de BTC, día de la semana. Corren **en paralelo y sin ajustarse**: elegir
el mejor mirando los resultados es el sobreajuste que convirtió -96R en +88R en
ese mismo archivo.

## Entre sesiones

`abiertas()` es lo que el agente lee al volver. El proceso muere entre una
sesión y la siguiente —en un Codespace, cada vez— y las órdenes que quedaron a
medias solo existen ahí.

## Las herramientas del agente

Cuatro, y el orden importa:

| | qué hace |
|---|---|
| `mirar_mercado` | precio, rango de 20 velas, volumen relativo, ATR/ADX/RSI/MACD/EMA |
| `abrir_operacion` | registra una entrada — **la razón es obligatoria** |
| `cerrar_operacion` | cierra al precio actual y calcula el R |
| `estado_paper` | qué quedó abierto y cómo va cada eje |

Que la razón sea un argumento obligatorio del esquema es lo que hace imposible
registrar una entrada sin justificarla. Si fuera opcional, el modelo la omitiría
en cuanto tuviera prisa.

`estado_paper` es lo que el agente lee al empezar: entre una sesión y la
siguiente el proceso muere —en un Codespace, cada vez— y el registro es lo único
que recuerda qué había a medias. Los ejes se listan **en orden alfabético**, no
por resultado: ordenarlos por R invita a elegir el mejor mirando la tabla, que
es el sobreajuste que el criterio de aborto prohíbe.

## Configuración

    BYTE_PAPER_DB=/ruta/a/paper/operaciones.db
    BYTE_PAPER_SCRIPTS=/ruta/al/repo-de-trading/scripts/paper

Las dos hacen falta: sin la segunda las herramientas fallarían en cada llamada,
así que no se registran.

## Probado de punta a punta

Con granite4.1:8b y datos reales de MEXC: miró BTCUSDT en 15m, decidió una
entrada long con su razón —«el precio tocó el fondo del rango (76509.9), ADX
indica tendencia débil, RSI bajo»—, la registró sellada, y en una **conversación
nueva** —sin memoria del chat anterior— recuperó la operación leyendo el
registro.
