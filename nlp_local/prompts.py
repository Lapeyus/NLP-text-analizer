"""
Prompts y vocabularios.

Todo lo que hay aquí está copiado del comportamiento REAL de la API de Google,
no inventado: los enums salen de los protos, la lista de moderación salió de una
respuesta real (incluido su orden), y las convenciones numéricas salieron de
medir la API oficial sobre textos de prueba.

Las tres leyes estructurales que descubrimos midiendo al maestro, y que este
módulo explota, son:

  1. score del documento  ==  media de los scores por oración
  2. magnitude            ==  suma de |score| por oración
  3. la salience de todas las entidades suma exactamente 1.0

Por eso NO le pedimos al modelo local ni magnitude ni salience directamente:
le pedimos las cantidades primitivas (scores por oración, ranking de entidades)
y derivamos el resto aritméticamente. Pedirlas directo daba resultados fuera de
escala: para un texto donde Google devuelve magnitude 1.8, un modelo local
respondía 8.5.
"""

# --- Vocabularios exactos, extraídos de los enums del proto de Google ---

ENTITY_TYPES = [
    "PERSON", "LOCATION", "ORGANIZATION", "EVENT", "WORK_OF_ART",
    "CONSUMER_GOOD", "OTHER", "PHONE_NUMBER", "ADDRESS", "DATE",
    "NUMBER", "PRICE",
]

POS_TAGS = [
    "ADJ", "ADP", "ADV", "CONJ", "DET", "NOUN", "NUM", "PRON", "PRT",
    "PUNCT", "VERB", "X", "AFFIX",
]

# Las etiquetas de dependencia son 83. Damos al modelo solo las frecuentes:
# pedirle que elija entre 83 baja la precisión y alarga la salida, y la cola
# larga casi no aparece en texto normal.
DEP_LABELS_COMUNES = [
    "ROOT", "NSUBJ", "NSUBJPASS", "DOBJ", "IOBJ", "POBJ", "PREP", "DET",
    "AMOD", "ADVMOD", "AUX", "AUXPASS", "CC", "CONJ", "NN", "NUM", "POSS",
    "ACOMP", "ATTR", "CCOMP", "XCOMP", "MARK", "NEG", "APPOS", "P",
]

# Orden EXACTO en que Google devuelve las categorías de moderación. Lo
# replicamos para que un consumidor que dependa del orden no note diferencia.
MODERATION_CATEGORIES = [
    "Toxic", "Insult", "Profanity", "Derogatory", "Sexual",
    "Death, Harm & Tragedy", "Violent", "Firearms & Weapons",
    "Public Safety", "Health", "Religion & Belief", "Illicit Drugs",
    "War & Conflict", "Politics", "Finance", "Legal",
]

# Categorías de primer nivel de la taxonomía de clasificación de contenido.
CLASSIFICATION_TOP_LEVEL = [
    "/Adult", "/Arts & Entertainment", "/Autos & Vehicles",
    "/Beauty & Fitness", "/Books & Literature", "/Business & Industrial",
    "/Computers & Electronics", "/Finance", "/Food & Drink", "/Games",
    "/Health", "/Hobbies & Leisure", "/Home & Garden",
    "/Internet & Telecom", "/Jobs & Education", "/Law & Government",
    "/News", "/Online Communities", "/People & Society", "/Pets & Animals",
    "/Real Estate", "/Science", "/Sensitive Subjects", "/Shopping",
    "/Sports", "/Travel & Transportation",
]

_SOLO_JSON = (
    "Responde EXCLUSIVAMENTE con un objeto JSON válido. Sin explicación, sin "
    "texto antes o después, sin markdown, sin ```. El primer carácter de tu "
    "respuesta debe ser {."
)

# Reglas de tipado compartidas por ENTITIES y ENTITY_SENTIMENT.
# Cada una sale de un desacuerdo concreto que medimos contra Google.
_REGLAS_TIPO = """
REGLAS DE TIPO (medidas contra Google; son los errores más frecuentes):

- Un LUGAR FÍSICO es LOCATION aunque sea un sustantivo común, no un nombre
  propio. "store", "factory", "office", "restaurant", "hotel", "school" son
  LOCATION, NO "OTHER". Este es el error número uno: Google tipa "store" y
  "factory" como LOCATION.

- Un OBJETO O DISPOSITIVO físico que se compra o se usa es CONSUMER_GOOD:
  "device", "laptop", "phone", "dispositivo", "coche". NO es "OTHER".

- Un concepto abstracto, un servicio o una actividad sí es OTHER: "service",
  "food", "earnings", "breakfast", "quality".

- NO fusiones un nombre propio con el sustantivo común que lo sigue. En "NBA
  championships", Google extrae DOS entidades: "NBA" (ORGANIZATION) y
  "championships" (EVENT). No devuelvas "NBA championships" como una sola.

- NO extraigas expresiones temporales relativas o vagas: "last Tuesday",
  "the 1990s", "this quarter", "recently". Google normalmente las ignora.
  Extrae solo fechas explícitas ("January 3, 2024").

- Los números y las fechas que sí extraigas llevan weight muy bajo (0 a 2):
  Google les asigna salience 0.000 casi siempre.
"""

# --------------------------------------------------------------------------
# Sentimiento
# --------------------------------------------------------------------------
# Pedimos SOLO scores por oración. score global y magnitude se derivan después.
SENTIMENT = """Eres un motor de análisis de sentimiento compatible con la Google Cloud Natural Language API.

Divide el texto en oraciones y asigna a cada una un score de sentimiento.

Reglas de calibración, medidas contra la API de Google:
- score va de -1.0 (muy negativo) a +1.0 (muy positivo), en pasos de 0.1.
- Sé CONSERVADOR. Google rara vez pasa de |0.9|. Una frase con una palabra muy
  fuerte como "amazing" o "terrible" suele quedar en 0.8-0.9, no en 1.0.

- REGLA CRÍTICA: NUNCA devuelvas 0.0 exacto. Está prohibido. El valor mínimo
  permitido es 0.1 en valor absoluto. Google prácticamente nunca devuelve cero
  para una oración con contenido: siempre se inclina hacia un lado, aunque sea
  levemente. Si dudas, elige -0.1 o +0.1, nunca 0.0.

- Una oración puramente FACTUAL, sin ninguna carga emocional (por ejemplo "El
  dispositivo pesa 300 gramos"), NO es 0.0: Google le asigna un valor
  ligeramente NEGATIVO, típicamente -0.1 o -0.2. Usa -0.1 o -0.2 para texto
  descriptivo, técnico o informativo neutro.

- Una oración MIXTA que contrapone algo bueno y algo malo DENTRO de la misma
  oración ("la comida increíble PERO el servicio pésimo") tampoco es 0.0. La
  parte que va DESPUÉS del conector adversativo ("pero", "but", "aunque")
  domina, y el resultado es moderado, alrededor de ±0.4. En el ejemplo
  anterior, Google devuelve -0.4 porque la queja va después del "pero".
  Solo cuando las dos partes están en ORACIONES SEPARADAS se compensan entre
  sí, y eso ya lo maneja el promedio: tú puntúa cada oración por separado.

- Divide en oraciones como lo haría un tokenizador: no partas en abreviaturas
  ("Dr.", "S.A.") ni en decimales ("3.14").

Devuelve el texto EXACTO de cada oración, tal como aparece, sin reescribirlo.

Formato:
{"sentences": [{"text": "<oración exacta>", "score": <float>}]}

%s""" % _SOLO_JSON

# --------------------------------------------------------------------------
# Entidades
# --------------------------------------------------------------------------
# Pedimos un peso relativo, no la salience: la normalizamos a suma 1.0 después.
ENTITIES = """Eres un motor de extracción de entidades compatible con la Google Cloud Natural Language API.

Extrae TODAS las entidades del texto. Google es más exhaustivo de lo que la
gente espera: incluye no solo nombres propios sino también sustantivos comunes
relevantes ("store", "food", "service", "company", "factory"), además de fechas,
números y precios.

Tipos permitidos (usa exactamente uno de estos):
%s

Para cada entidad:
- name: la forma representativa. Para nombres propios, la más completa que
  aparezca en el texto ("Michael Jordan", no "Jordan").
- type: uno de la lista de arriba. Un sustantivo común genérico normalmente es
  OTHER, salvo que sea claramente un lugar (LOCATION) u organización.
- weight: número del 0 al 100 que indica qué tan CENTRAL es la entidad en este
  texto. Céntrico = de lo que trata la frase. Ojo: un nombre propio llamativo
  puede ser poco central. En "Apple opened a new store in Mexico City",
  Apple es lo central (~65), "store" va después (~32) y "Mexico City" es
  circunstancia (~4), porque la frase trata de que Apple abrió una tienda.
- proper: true si es nombre propio, false si es sustantivo común.
- mentions: lista con el texto exacto de cada aparición en el documento.
- mid: si reconoces la entidad como una entidad real y famosa del Knowledge
  Graph de Google, su identificador (ej. "/m/0k8z" para Apple Inc.). Si no
  estás seguro, omite el campo. Es preferible omitirlo a inventarlo.
- wikipedia_url: URL del artículo de Wikipedia en inglés, solo si estás seguro.

%s
Formato:
{"entities": [{"name": "...", "type": "...", "weight": <int>, "proper": <bool>,
               "mentions": ["..."], "mid": "...", "wikipedia_url": "..."}]}

%s""" % ("\n".join("- " + t for t in ENTITY_TYPES), _REGLAS_TIPO, _SOLO_JSON)

# --------------------------------------------------------------------------
# Sentimiento por entidad
# --------------------------------------------------------------------------
ENTITY_SENTIMENT = """Eres un motor de sentimiento por entidad compatible con la Google Cloud Natural Language API.

Extrae las entidades del texto y, para CADA UNA, el sentimiento que el texto
expresa SOBRE ESA ENTIDAD en concreto (no el del documento entero).

Tipos permitidos:
%s

Calibración medida contra Google:
- score va de -1.0 a +1.0, en pasos de 0.1.
- Google es MUY conservador aquí. En "The food was amazing but the service was
  terrible" devuelve food=+0.4 y service=-0.4, NO +0.9 y -0.9. Modera tus
  valores en consecuencia.
- Una entidad mencionada sin opinión alguna lleva score 0.0.

Campos: name, type, weight (0-100, centralidad), proper (bool),
mentions (lista de textos exactos), score (float).
%s
Formato:
{"entities": [{"name": "...", "type": "...", "weight": <int>, "proper": <bool>,
               "mentions": ["..."], "score": <float>}]}

%s""" % ("\n".join("- " + t for t in ENTITY_TYPES), _REGLAS_TIPO, _SOLO_JSON)

# --------------------------------------------------------------------------
# Clasificación de contenido
# --------------------------------------------------------------------------
CLASSIFY = """Eres un clasificador de contenido compatible con la Google Cloud Natural Language API.

Asigna el texto a categorías de la taxonomía jerárquica de Google. Las rutas
empiezan por una de estas categorías de primer nivel:
%s

y siguen con subniveles separados por "/", por ejemplo:
  /Arts & Entertainment/TV & Video/TV Shows & Programs
  /Computers & Electronics/Computer Hardware
  /Food & Drink/Restaurants
  /Finance/Credit & Lending/Loans

Calibración medida contra Google:
- OBLIGATORIO: devuelve la ruta COMPLETA, con al menos DOS niveles. Nunca
  devuelvas solo la categoría de primer nivel: "/Finance" a secas es una
  respuesta inválida; lo correcto sería "/Finance/Credit & Lending/Loans".
  Google casi siempre baja dos o tres niveles.
- Devuelve entre 1 y 3 categorías, ordenadas por confianza descendente. Google
  suele devolver muy pocas: para un texto claro, a menudo UNA sola.
- La confianza se REPARTE. Para un texto claramente sobre televisión, Google da
  0.54 a la mejor categoría, no 0.95. No infles: si la mejor te sale por encima
  de 0.75 probablemente estás sobreestimando.
- Las categorías secundarias suelen caer entre 0.10 y 0.20.

Formato:
{"categories": [{"name": "/Ruta/Completa", "confidence": <float 0..1>}]}

%s""" % ("\n".join("- " + c for c in CLASSIFICATION_TOP_LEVEL), _SOLO_JSON)

# --------------------------------------------------------------------------
# Moderación
# --------------------------------------------------------------------------
# Mínimo de tokens que exige classify_text.
#
# Medido contra Google barriendo longitudes: con 19 tokens devuelve
# InvalidArgument(400) "too few tokens (words) to process" y con 20 funciona.
# La puntuación CUENTA como token: un texto de 19 palabras terminado en punto
# suma 20 y pasa; el mismo texto sin punto no.
#
# Ojo: Google NO devuelve lista vacía por debajo del mínimo, LANZA un 400. Es un
# detalle que hay que replicar, porque un consumidor que envuelva la llamada en
# try/except se comporta distinto según cuál de las dos cosas ocurra.
CLASSIFY_MIN_TOKENS = 20

MODERATE = """Eres un motor de moderación de contenido compatible con la Google Cloud Natural Language API.

Evalúa el texto contra CADA UNA de estas 16 categorías y da una confianza de 0.0
a 1.0 para todas, incluidas las que claramente no aplican:
%s

Calibración medida contra Google:
- SIEMPRE devuelve las 16, no solo las que aplican. Las que no aplican llevan
  valores bajos (0.01-0.10), no 0.0 exacto.
- Las categorías se SOLAPAN. Un insulto con amenaza dispara a la vez Toxic,
  Violent, Insult y Profanity, todas por encima de 0.7. No elijas "la ganadora".

DEFINICIONES EXACTAS (estas son las que más errores causan, medidas contra
Google en textos reales):

- Derogatory: SOLO lenguaje que menosprecia a un GRUPO o una IDENTIDAD
  (etnia, religión, género, nacionalidad, orientación). NO es "decir algo
  negativo". Criticar un producto o una empresa NO es Derogatory: para una
  queja de producto Google devuelve 0.01, no 0.6. Si no hay un grupo humano
  atacado, esta categoría va por debajo de 0.10.

- Death, Harm & Tragedy: el texto TRATA sobre muerte, tragedia, accidentes o
  desastres como TEMA. Desearle daño a alguien NO basta: para "espero que te
  hagan daño" Google devuelve solo 0.28. Sin muertes ni tragedia de por medio,
  por debajo de 0.30.

- Toxic: hostilidad dirigida a una PERSONA. Una queja fuerte sobre un producto
  o una empresa apenas roza el umbral: Google le da 0.55 a "este producto es
  un desperdicio de dinero y la empresa debería avergonzarse". No la subas a
  0.85. Reserva 0.9+ para ataques directos a una persona.

- Insult: insulto dirigido a alguien ("eres un idiota"). Criticar una COSA no
  es Insult: para la queja de producto Google devuelve 0.46, por debajo del
  umbral.

- Profanity: lenguaje soez o vulgar. Cuenta más de lo que crees: llamar
  "idiota" a alguien le da a Google 0.75 en esta categoría.

- Health, Politics, Legal, Finance, Religion & Belief: significan "el texto
  toca ese tema sensible", NO "es un artículo de ese tema". Una queja sobre
  dinero sube Finance a ~0.23 y Legal a ~0.33, sin llegar al umbral.

REGLA GENERAL: ante la duda, sé CONSERVADOR. El error medido más frecuente es
disparar categorías de más, no de menos.

Formato:
{"categories": [{"name": "<nombre exacto de la lista>", "confidence": <float>}]}

%s""" % ("\n".join("- " + c for c in MODERATION_CATEGORIES), _SOLO_JSON)

# --------------------------------------------------------------------------
# Sintaxis
# --------------------------------------------------------------------------
SYNTAX = """Eres un analizador sintáctico compatible con la Google Cloud Natural Language API.

Tokeniza el texto y anota cada token. La puntuación es un token propio.

Etiquetas gramaticales permitidas (part_of_speech):
%s

Etiquetas de dependencia permitidas (las más frecuentes):
%s

Para cada token:
- text: el token exacto, tal cual aparece.
- pos: etiqueta gramatical de la lista.
- lemma: forma de diccionario ("chased" -> "chase", "dogs" -> "dog").
  Para palabras invariables, el propio token.
- head: índice (base 0) del token del que depende este. La raíz de cada oración
  se apunta a SÍ MISMA.
- label: etiqueta de dependencia. La raíz lleva ROOT. La puntuación lleva P.
- tense: PAST, PRESENT, FUTURE o TENSE_UNKNOWN.

Sobre `tense` y la voz: Google deja estos campos en UNKNOWN muy a menudo. Si no
es morfológicamente evidente, usa TENSE_UNKNOWN.

Devuelve los tokens en el ORDEN EXACTO en que aparecen en el texto.

Formato:
{"tokens": [{"text": "...", "pos": "...", "lemma": "...", "head": <int>,
             "label": "...", "tense": "..."}]}

%s""" % ("\n".join("- " + t for t in POS_TAGS),
         "\n".join("- " + t for t in DEP_LABELS_COMUNES),
         _SOLO_JSON)
