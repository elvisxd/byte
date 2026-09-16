# Cómo se aplica bien

Lo que sigue es lo que el código de `empleo/` da por cierto. Está acá y no en un
comentario porque si el modelo mental está mal, el puntaje mide lo que no es.

## El error de base: el ATS no es un portón, es un buscador

La creencia común —"el 75% de los CV los rechaza un robot antes de que los vea
un humano"— es falsa. Alrededor del **8% de los reclutadores habilita el
rechazo automático por contenido**; el resto usa el ATS para *parsear, guardar,
filtrar y ordenar*, y quien rechaza es una persona. Esa persona revisa
típicamente **las primeras 20 a 40 postulaciones** de la cola.

Eso cambia todo. No hay un umbral binario que superar: hay una cola en la que
estás más arriba o más abajo. "Optimizar para el ATS" no es pasar un filtro, es
**aparecer arriba en la búsqueda del reclutador**.

De ahí salen las dos únicas palancas mecánicas, y las dos están en el código:

| palanca | por qué | dónde vive |
|---|---|---|
| **Llegar temprano** | decide en qué parte de la cola caés | `[frescura]` en `busqueda.toml` |
| **Coincidir en términos** | decide dónde salís cuando el reclutador busca | `[stack]` y la brecha contra el CV |

## Llegar temprano

Postular dentro de las **primeras 24-48 horas** te pone en el lote inicial que
una persona efectivamente lee. El umbral que más aparece medido son las **96
horas** (análisis de TalentWorks sobre ~1.600 postulaciones).

Cuidado con los números que circulan: "8 veces más entrevistas", "2 a 3 veces
más" salen de blogs de empresas que venden urgencia y herramientas de
postulación. **La dirección del hallazgo es sólida; las magnitudes no.** Por eso
los tramos de `[frescura]` son tramos y no una curva fina: fingir precisión que
la evidencia no tiene sería inventar.

Consecuencia práctica: el cron corre tres veces por día, y una oferta de hace
tres horas vale más que una mejor de hace diez días. Eso ahora se ve en el aviso
—`105 · 2h`— y no hay que abrir el link para saberlo.

## Coincidir en términos

No es meter palabras clave. Dos cosas que no funcionan y conviene descartar de
una vez: **el texto blanco con keywords escondidas** (el ATS extrae todo el
texto, incluido el oculto, y queda a la vista como lo que es) y el relleno de
términos que no sabés sostener en una entrevista.

Lo que sí: que el CV **diga** lo que el reclutador va a buscar. `analizar_oferta`
lo resuelve como una diferencia de conjuntos contra `perfil/cv.md`:

```
Cobertura del CV: 67% de lo que pide (6 de 9).
PIDE Y TENÉS — con esto se abre la carta: aws, fastapi, langgraph, pgvector, python, rag
PIDE Y EL CV NO DICE: kafka, kubernetes, terraform
```

La lista de la izquierda es lo que va arriba de todo. La de la derecha no es una
lista de lo que te falta saber: es lo que **tu CV no dice**, que es otra cosa. Si
lo tenés y no está escrito, escribilo; si no lo tenés, ya sabés qué te van a
preguntar.

## Las dos primeras líneas

En Upwork el cliente ve **sólo las dos primeras líneas** en la lista de
propuestas, y decide en **8 a 12 segundos** si abre la tuya. El rechazo pasa casi
siempre en ese preview, antes de que nadie lea el cuerpo. Lo mismo vale, con
menos crudeza, para una carta de presentación.

Lo que funciona, y es al revés de lo que sale solo:

- **Abrir por el problema de ellos**, no por vos. Nada de "Dear Hiring Manager" ni
  un párrafo de biografía.
- **Una prueba, la que corresponde a ese problema.** Una, no el catálogo.
- **Cerrar con una pregunta fácil de contestar.**
- **Menos de 150 palabras.**
- **Reescribir las dos primeras líneas en cada postulación.** Es la parte que no
  se puede plantillar; todo lo demás sí.

## El caso concreto: contratar desde LatAm

Las empresas de EE.UU. aumentaron la contratación remota en Latinoamérica de
forma sostenida desde 2023, y las razones son dos y conviene conocerlas porque
definen cómo te van a evaluar:

- **Solapamiento horario.** Argentina y Brasil en UTC-3, Colombia UTC-5: mismo
  día laboral que el cliente. Es la ventaja frente a India o Europa del Este, y
  es la razón por la que "en qué huso vas a estar" es una pregunta real, no
  cortesía.
- **Costo.** Un senior en EE.UU. cuesta 150-200k; el mismo nivel desde LatAm,
  bastante menos. Esto es un dato del mercado, no una recomendación de a cuánto
  cobrar.

Los dos caminos por los que te contratan sin abrir entidad legal son
**contractor** (Deel, Remote.com; rápido) y **EOR** (te emplean legalmente en tu
país; le cuesta a la empresa unos cientos de dólares extra al mes). Por eso esas
señales suman en el criterio: una oferta que ya nombra Deel o EOR ya resolvió la
parte que suele matar la conversación.

Lo que evalúan, más allá del código: **diseño de sistemas**, inglés escrito y
hablado, y qué tan lista está la persona para trabajar remoto. Y un punto que
vale releer: **los mejores candidatos de LatAm no compiten por precio**, compiten
por confiabilidad y oficio. Competir por precio desde abajo es la estrategia
perdedora del que no tiene nada más; no es el caso de este perfil.

## Los ejes, ordenados por cuánto los controlás

1. **Cuándo postulás.** Control total, efecto medido. Es el más barato de todos y
   el que más se desperdicia.
2. **Qué dice tu CV.** Control total. No "qué sabés": qué está escrito.
3. **A qué postulás.** Control total, y es donde el puntaje ayuda: veinte
   postulaciones a ofertas que encajan valen más que doscientas a cualquiera.
4. **Las dos primeras líneas.** Control total, y es lo único que no se
   automatiza sin que se note.
5. **Tu diferencial.** IA en producción con medición real es escaso. El resto del
   CV compite con miles; esa parte, con muchos menos.
6. **Si contestan.** Cero control. Por eso el sistema se juzga por lo de arriba y
   no por el resultado de una postulación suelta.

## Lo que el código hace y lo que no

Hace: traer, ordenar por encaje y frescura, avisar, y decir qué pide una oferta
que tu CV no dice.

No hace: postular. Ni va a hacerlo — en Upwork eso se paga con suspensión
permanente, y en el resto, una carta de plantilla se nota y compite contra las
dos primeras líneas de alguien que sí leyó la oferta.

---

Fuentes de los datos citados: análisis de TalentWorks sobre tiempos de
postulación; relevamientos de uso de ATS y rechazo automático; guías de Upwork y
de terceros sobre cómo se leen las propuestas; y guías de contratación de LatAm
para empresas de EE.UU. Las magnitudes de los blogs comerciales se tomaron como
dirección, no como medición.
