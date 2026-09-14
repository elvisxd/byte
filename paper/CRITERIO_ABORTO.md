# Criterio de aborto — fijado ANTES de la primera operación

> Este archivo va en su propio commit, **antes** de que exista un solo registro.
> Es la corrección explícita de lo que quedó anotado en
> `scripts/spot/CRITERIO_ABORTO.md` del repo de trading: *"La próxima vez, el
> criterio va en su propio commit antes de bajar un solo dato."*

## Qué se está probando

Que un agente con un modelo de lenguaje, operando en papel sobre varios ejes de
RANGE-SWEEP a la vez, **registre algo que un backtest no registra**: el contexto
del momento y la razón de la entrada, sellados antes de conocer el resultado.

No se está probando que la estrategia sea rentable. Once rondas de investigación
ya concluyeron que las 34 estrategias de futuros no lo eran bajo ejecución real
(repo de trading, borrado del 2026-08-29). La hipótesis acá es distinta: **que
las razones escritas de antemano permitan distinguir por qué falla una entrada**,
que es lo que un backtest calibrado no puede decir.

## Se aborta si, al llegar a 100 operaciones cerradas:

1. **Las razones no discriminan.** Si al agrupar las operaciones por la razón
   que el agente escribió al entrar, ningún grupo se aparta del conjunto en
   R/trade más de lo que se apartaría agrupando al azar (permutación, 1000
   remuestreos, p > 0.05) — entonces la razón escrita no aporta información y
   todo el ejercicio es un backtest con prosa encima.

2. **El agente no distingue sus propios fallos.** Si al pedirle que explique por
   qué falló una operación cerrada en pérdida, sus explicaciones no predicen
   nada sobre las siguientes: se toman las 20 últimas perdedoras, se le pide una
   regla que las hubiera evitado, y se aplica esa regla a las 20 siguientes
   operaciones. Si el R/trade no mejora, el "aprendizaje" es narrativa.

3. **Un eje gana solo por haberse elegido después.** Todos los ejes corren en
   paralelo desde el principio y ninguno se ajusta a mitad de camino. Si al
   final el mejor eje deja de serlo al medirlo sobre las últimas 30 operaciones
   —las que ningún ajuste pudo ver— se aborta: es el mismo sobreajuste que
   convirtió -96R en +88R en `rangeSweepCombo.ts`.

## Se para —que no es lo mismo que abortar— si:

4. **El drawdown pasa de −30R acumulados.** No es un veredicto sobre la
   estrategia: es un freno para mirar qué está pasando antes de gastar más
   muestra. Se revisa, se anota qué se vio, y se decide si seguir o no. Si se
   sigue, se sigue sin tocar nada —ese es el punto—.

   ⚠ ESTO NO CONTRADICE LA SECCIÓN DE ABAJO, y la diferencia importa: perder
   plata simulada no aborta nada. Lo que este umbral vigila es otra cosa —que el
   agente entre en una racha destructiva que consuma las 100 operaciones sin
   producir variedad de razones, que es lo único que el experimento viene a
   medir—. Cien entradas idénticas perdiendo no son cien datos, son uno.

   El número sale de fuera: los agentes de trading en producción operan dentro
   de «contratos de autoridad» con umbrales de drawdown y escalado a un humano,
   y el único resultado en vivo de 2026 con pinta honesta reportaba ~7% a 30
   días **con un 22% de drawdown**. Acá no hay dinero, así que el umbral no
   protege capital: protege la muestra.

   Es un freno, no un ajuste. Bajar el umbral después de tocarlo sería calibrar.

## Lo que NO es criterio de aborto

Que el P&L simulado sea negativo. Un conjunto de ejes exploratorios puede perder
plata simulada y aun así producir el dato que se busca: cuáles razones fallan y
por qué. Confundir "no ganó" con "no sirvió" es lo que empuja a calibrar hasta
que gane, que es exactamente lo que se está tratando de no repetir.

Tampoco lo es tocar el freno del punto 4. Pararse a mirar y seguir es una
decisión válida; lo que no vale es seguir sin haber mirado.

## Qué se registra, y cuándo

El contexto del gráfico y la razón se sellan **en el momento de la entrada**,
con un hash que incluye el precio y el timestamp. El resultado se escribe en un
campo aparte, después. Un registro cuya razón se pueda haber escrito sabiendo el
resultado no vale nada, y la única forma de garantizarlo es que sea imposible.
