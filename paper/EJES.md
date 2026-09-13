# Los ejes — fijados antes de la primera operación

> Va junto al `CRITERIO_ABORTO.md` y por la misma razón: elegir los ejes después
> de ver cómo van es la forma más directa de convertir esto en un backtest
> calibrado con prosa encima.

Cinco hipótesis que corren **en paralelo, sobre los mismos datos, sin ajustarse**.
No se toca ninguna hasta las 100 operaciones. Al final se comparan, y el que
gane se vuelve a medir sobre las últimas 30 —las que ningún ajuste pudo ver—
como pide el criterio de aborto.

## De dónde salen

Los cuatro primeros son las familias más adoptadas del motor que se borró, por
cuántos símbolos las activaban:

| familia | símbolos | qué miraba |
|---|---|---|
| `rangeSweepCombo` | 136 | barrido del borde de un rango con vuelta adentro |
| `zoneReclaim` | 76 | recuperación de una zona perdida |
| `cvdSweepCombo` | 72 | barrido con divergencia de volumen comprador |
| `dipTrapCombo` | 68 | caída que atrapa vendedores y revierte |

El quinto no estaba, y es el que más me interesa medir.

---

## 1 · `range-sweep` — el barrido del rango

El piso (o techo) de un rango de consolidación se barre con mecha y el precio
cierra **de vuelta adentro** dentro de las siguientes velas. Entrada a favor de
la vuelta.

Es la familia que más símbolos usaban, y también la que dejó documentado que la
base pura daba **−96R** y solo funcionaba tras calibrar cinco dimensiones. Acá
va **sin calibrar**: sin excluir días, sin filtrar ancho, sin régimen de BTC. Si
la versión cruda no sirve, eso es el dato.

## 2 · `zone-reclaim` — recuperar lo perdido

El precio pierde una zona (soporte, nivel previo, media) y vuelve a cerrarla por
encima. La hipótesis es que la pérdida era falsa y quien vendió en ella se queda
afuera.

## 3 · `cvd-divergence` — el volumen no acompaña

El precio hace un nuevo extremo pero el volumen comprador agresivo no lo sigue.
**Solo se puede medir con Binance**: el `takerBuyVolume` no lo dan MEXC ni Bybit,
y Binance responde 451 desde acá. Este eje queda **dormido** hasta que haya una
fuente que lo exponga — se deja escrito para no reinventarlo después, y para que
su ausencia sea deliberada y no un olvido.

## 4 · `dip-trap` — la trampa de la caída

Caída brusca con volumen alto que se revierte dentro de pocas velas. La
hipótesis es que barrió stops y no había vendedores reales detrás.

## 5 · `anti-smc` — operar contra la señal de libro

**La hipótesis contraria a las cuatro anteriores.** Cuando aparece un patrón SMC
de manual —un CHoCH limpio, un order block claro— se opera **en contra**.

No es provocación. La evidencia de 2026 dice dos cosas que se complementan:

- Los setups SMC crudos, sin filtrar, dan **38-48% de aciertos** — los backtests
  que muestran 61% son estrategias ya calibradas, que es justo lo que no
  sobrevive a la ejecución real.
- *"Los bots de prop firms han vuelto obsoletas las etiquetas subjetivas de SMC,
  con algoritmos diseñados para fabricar CHoCHs falsos y atrapar a los que entran
  temprano."*

Si eso es cierto, la señal que un retail lee como "entrada institucional" es la
trampa, y operar contra ella tiene fundamento. Si es falso, este eje va a perder
de forma consistente — y eso también es un resultado: significaría que las
señales SMC conservan valor y los otros cuatro ejes tienen sentido.

Es el único eje cuyo fracaso confirma algo útil.

---

## Lo que ninguno hace

**Calibrarse.** Ni excluir días de la semana, ni filtrar por ancho de rango, ni
ajustar el multiplicador del stop. Todos usan stop al extremo del patrón y
objetivo a 2R, iguales para todos.

Esos filtros son exactamente los que convirtieron −96R en +88R sobre los mismos
datos en `rangeSweepCombo.ts`, con la muestra cayendo de 474 operaciones a 67.
Acá el punto no es que ganen: es saber cuáles razones escritas de antemano
predicen algo.
