# Arnés de paridad — Google como maestro

Mide cuánto se parece [`nlp_local`](../nlp_local) a la API oficial de Google, análisis por análisis, para saber si los dos sistemas son realmente intercambiables.

```bash
python -m parity.harness
```

```bash
python -m parity.harness sentiment entities --model qwen3.5:9b-mlx
```

---

## Resultados actuales

Modelo `qwen3.8:27b-mlx`, corpus de 20 textos:

| Análisis | Inicial | Actual | Qué lo arregló |
|---|---:|---:|---|
| `syntax` | 98.5 | **98.5** | nada — funcionó a la primera |
| `moderate` | 76.7 | **97.2** | definir cada categoría con datos del maestro |
| `classify` | 15.0 | **95.5** | corregir el umbral y exigir rutas profundas |
| `entity_sentiment` | 86.8 | **93.6** | reglas de tipado de entidades |
| `entities` | 82.5 | **91.8** | reglas de tipado de entidades |
| `sentiment` | 72.8 | **89.6** | prohibir el 0.0 exacto |
| **GLOBAL** | **74.8** | **94.4** | |

La sorpresa fue `syntax`: esperábamos que el análisis de dependencias fuera lo más difícil para un LLM y salió casi perfecto sin ajuste alguno. El desastre inicial de `classify` no era del modelo sino nuestro, ver abajo.

---

## El método: aprender del maestro, no adivinar

Cada ajuste salió de comparar salidas reales, nunca de suponer cómo se comporta Google. Las tres cosas más útiles que encontramos fueron **leyes estructurales**, no mejoras de prompt.

### Leyes descubridas midiendo la API oficial

Medidas sobre el corpus y verificadas en todos los casos:

| Ley | Error máximo observado |
|---|---|
| `score` del documento = **media** de los scores por oración | 0.03 |
| `magnitude` = **Σ \|score de oración\|** | 0.40 |
| La `salience` de todas las entidades **suma exactamente 1.0** | 0.0000 |

Explotarlas cambió el diseño: en vez de pedirle al modelo la `magnitude` y la `salience` —donde daba valores fuera de escala, como 8.5 donde Google decía 1.8— le pedimos solo las cantidades primitivas (scores por oración, pesos relativos) y **derivamos el resto aritméticamente**. La normalización a 1.0 hace que la salience sea correcta por construcción.

### Ajustes guiados por el error medido

- **Sentimiento (72.8 → 89.6).** Los tres fallos eran idénticos: el modelo devolvía `0.0` exacto donde Google daba un valor pequeño pero no nulo. Google casi nunca devuelve cero: incluso *"The device weighs 300 grams"* le sale **−0.20**. Prohibir el 0.0 y enseñar el sesgo levemente negativo del texto factual arregló el caso mixto de golpe (29 → 100).

- **Moderación (76.7 → 97.2).** El modelo interpretaba `Derogatory` como "dice algo negativo" y lo disparaba a 0.650 donde Google daba **0.011**. Google lo reserva para menosprecio a un grupo. Escribir la definición exacta de cada categoría, con los números del maestro como referencia, alineó las tres pruebas al 100 % en qué categorías se disparan.

- **Entidades (82.5 → 91.8).** Google tipa `store` y `factory` como `LOCATION` aunque sean sustantivos comunes; el modelo decía `OTHER`. Un dispositivo es `CONSUMER_GOOD`, no `OTHER`. Con esas reglas, dos de los seis casos pasaron a paridad casi exacta (99.9).

---

## Dos errores que encontró el arnés en *nuestro* código

Vale la pena separarlos, porque no eran del modelo local:

**1. `classify_text` no devuelve lista vacía: lanza un error.** Barriendo longitudes contra Google encontramos el umbral exacto: con **19 tokens** devuelve `InvalidArgument(400) "too few tokens (words) to process"` y con **20** funciona. La puntuación cuenta como token. Nuestra implementación devolvía una lista vacía, y la documentación del repo afirmaba lo mismo — ambas cosas eran falsas. Un consumidor con `try/except InvalidArgument` se comportaría distinto según la librería, que es justo lo que este proyecto quiere evitar. El arnés ahora compara también las excepciones, y "las dos lanzan el mismo error" cuenta como paridad.

**2. La métrica de sentimiento tenía un acantilado.** Clasificaba en cubetas de signo con un corte en 0.15, así que −0.10 y −0.20 —el mismo veredicto para cualquiera— puntuaban como desacuerdo total. Un cambio de prompt que no empeoró nada hundió un caso de 98 a 39. La métrica ahora acepta como acuerdo cualquier diferencia ≤ 0.2. **Sin arreglar esto, habríamos "optimizado" el modelo contra una regla inventada por nosotros.**

---

## Entity linking: de 0/7 a 7/7

El caso más instructivo del proyecto, porque el primer diagnóstico era correcto y la conclusión que saqué de él no.

**El problema.** Pedirle los MID del Knowledge Graph al modelo daba **0 aciertos de 7** contra Google. Todos con el formato correcto `/m/xxxxx`, todos apuntando a otra cosa. Un MID inventado es peor que ninguno: su uso principal es deduplicar entidades, así que produce uniones incorrectas en silencio. La primera reacción fue suprimirlos y documentar el linking como imposible en local.

**Por qué eso estaba mal.** El linking no requiere que el modelo *recuerde* identificadores: requiere una base de conocimiento y criterio. Los MID de Google son IDs de Freebase, y **Wikidata conserva ese mapeo en la propiedad P646**. Así que [`nlp_local/linking.py`](../nlp_local/linking.py) hace lo que hace un sistema de entity linking de verdad:

1. **Generar candidatos** — `wbsearchentities` de Wikidata por el nombre.
2. **Desambiguar** — el LLM elige entre candidatos *reales* usando el contexto de la frase.
3. **Resolver** — `P646` → MID, sitelink de enwiki → `wikipedia_url`.

El modelo nunca inventa un ID: solo escoge de una lista, o dice que ninguno encaja. El problema pasa de "memoria del modelo" a "búsqueda + juicio con contexto", que es lo que un LLM sí hace bien.

**Resultado: 7/7**, con la caché vacía y sin fallos de red. Y desambigua: en *"Apple released the iPhone… I ate an apple for breakfast"*, la empresa se enlaza a `/m/0k8z` y la fruta se queda sin enlazar, igual que Google.

Dos detalles que costaron encontrar:

- Una entidad puede tener **varios** `P646`. Apple tiene `/m/0k8z` y `/m/04st9hr`; Google usa el primero. Tomar el último daba un fallo que parecía un error de búsqueda.
- Wikidata devuelve **429** si la consultas seguido. Con una pausa insuficiente, un barrido perdía una entidad y el resultado se veía idéntico a "Wikidata no la conoce". Es la misma trampa del fallo silencioso que motivó todo esto, así que `EntityLinker` ahora cuenta `fallos_red` por separado de `rechazadas`.

Está **apagado por defecto** porque requiere red, y funcionar sin conexión es media razón de ser de esta librería:

```python
LanguageServiceClient(entity_linking=True)
```

---

## Divergencias conocidas que NO se pueden cerrar

Honestidad sobre los límites:

### Google no es consistente consigo mismo entre idiomas

*"La comida estuvo increíble pero el servicio fue pésimo"* → Google devuelve **+0.20**.
*"The food was amazing but the service was terrible"* → Google devuelve **−0.40**.

Misma frase, signo opuesto. Nuestro modelo aplica la regla lingüística correcta (lo que va después del "pero" domina) y por eso puntúa mal ese caso (29.5/100). Igualarlo exigiría replicar la peculiaridad de Google por idioma, y no parece que valga la pena perseguir un comportamiento que el propio maestro no sostiene.

### Diferencias de granularidad

Google separa `NBA` (ORGANIZATION) y `championships` (EVENT); el modelo tiende a fusionar en `NBA championships`. Mejoró mucho con reglas explícitas pero no desaparece del todo.

---

## Cómo está construido

- `corpus.py` — 20 textos, cada uno elegido para estresar un comportamiento medido de la API oficial, no para que el modelo local luzca bien. `PLAN` decide qué análisis corre sobre qué textos: correr los 7 sobre los 20 gastaría cuota sin aportar señal.
- `metrics.py` — una función por análisis. Las métricas pesan **lo que un consumidor notaría al cambiar de implementación**: el acuerdo de signo pesa más que el error absoluto, y en moderación importa qué categorías se disparan, no la confianza exacta.
- `harness.py` — corre ambos, compara, reporta. Cachea las respuestas de Google en disco (el maestro no cambia y cada llamada cuesta cuota) y las del modelo local en `nlp_local/.cache_llm.json`, con la clave incluyendo modelo y prompt, de modo que **editar un prompt invalida solo lo afectado**.

### Comparar modelos

```bash
python -m parity.harness --model qwen3.5:9b-mlx
```

Los prompts están calibrados contra `qwen3.8:27b-mlx`. Un modelo distinto puede necesitar recalibración: el 9b, por ejemplo, devolvía `magnitude` 8.5 donde Google decía 1.8.
