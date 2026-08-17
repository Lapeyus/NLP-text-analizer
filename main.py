"""
Ejemplos de TODOS los análisis de la Google Cloud Natural Language API.

Guía rápida para elegir (el detalle está en el docstring de cada función):

    ¿Qué pregunta quieres responder?          -> Usa
    ----------------------------------------------------------------------
    "¿De QUÉ/QUIÉN habla este texto?"         -> sample_analyze_entities
    "¿El texto es positivo o negativo?"       -> sample_analyze_sentiment
    "¿Qué opinan de CADA cosa mencionada?"    -> sample_analyze_entity_sentiment
    "¿A qué TEMA pertenece este texto?"       -> sample_classify_text
    "¿Cómo está CONSTRUIDA la frase?"         -> sample_analyze_syntax
    "¿Este texto es DAÑINO?"                  -> sample_moderate_text
                                                 sample_moderate_text_v2
    "¿DÓNDE se tuerce este texto largo?"      -> sample_analyze_sentiment_by_sentence
    "¿A qué cosa del mundo se refiere?"       -> sample_link_entities_knowledge_graph
    (desambiguar: ¿'Jordan' el país o la persona?)

Y dos variantes que no cambian el análisis sino la forma de pedirlo:

    Necesito 2+ análisis del mismo texto      -> sample_annotate_text
                                                 (una sola llamada de red)
    Mi entrada es HTML crudo                  -> sample_analyze_html
    Mi entrada está en Cloud Storage          -> sample_analyze_gcs_file

IDIOMA
Las funciones heredadas fijan language="en". Si vas a analizar español, cambia
esa constante a "es" o elimina la clave "language" para que la API detecte el
idioma; si no, los resultados serán basura silenciosa (la API no falla,
simplemente interpreta mal). Las funciones de moderación y de sentimiento no
fijan idioma y funcionan con español tal como están.

COSTO
Se factura por cada 1.000 caracteres Y por cada tipo de análisis. Analizar el
mismo texto con cinco funciones cuesta cinco veces más que usar solo la que
necesitas. sample_annotate_text ahorra viajes de red, pero NO dinero: sigue
cobrando cada feature que actives.

VERSIONES DE LA API
Este archivo usa language_v1 salvo en sample_moderate_text_v2. La v2 tiene mejor
moderación y mejor cobertura de idiomas, pero le faltan cosas: no tiene
analyze_syntax ni analyze_entity_sentiment, y sus entidades no traen `salience`
ni `wikipedia_url` en la metadata. Por eso v1 sigue siendo la base aquí.

No es que esas features fueran de mala calidad: son subproductos del pipeline
clásico de NLP sobre el que corre v1 (tokenizar -> etiquetar -> árbol de
dependencias -> resolver entidades). La v2 corre sobre modelos basados en PaLM,
que llegan a la respuesta por otro camino y no calculan esos intermedios. Google
documenta QUÉ falta (notas de versión del 28/08/2023) pero no publica el porqué.

Cuidado al migrar: la ausencia de `wikipedia_url` es SILENCIOSA. El campo
`metadata` sigue existiendo en el Entity de v2, así que tu código no truena;
simplemente te llega vacío.
"""

import os
from google.cloud import language_v1
from google.cloud import language_v2
import six

# AUTENTICACIÓN
#
# Por defecto se usan las Application Default Credentials (ADC), que es la vía
# recomendada por Google para desarrollo local. Se configuran una sola vez con:
#
#     gcloud auth application-default login
#     gcloud auth application-default set-quota-project TU_PROYECTO
#
# Las ADC son credenciales de TU usuario, de vida corta y renovables. No hay
# ningún archivo de llave permanente que se pueda filtrar en un commit.
#
# Solo si existe un key.json de cuenta de servicio junto a este archivo lo
# usamos en su lugar; eso hace falta para desplegar en un servidor, donde no
# hay un humano que pueda hacer login.
#
# Importante: esta comprobación reemplaza a una línea que fijaba
# GOOGLE_APPLICATION_CREDENTIALS='./key.json' incondicionalmente. Eso rompía el
# flujo con ADC (apuntaba a un archivo inexistente) y además dependía del
# directorio desde el que ejecutaras el script, no de dónde vive el script.
_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "key.json")
if os.path.exists(_KEY_FILE):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _KEY_FILE

def sample_analyze_entities(text_content):
    """
    Extrae las "cosas del mundo real" mencionadas en el texto: personas,
    organizaciones, lugares, productos, eventos, fechas, direcciones, números.

    CUÁNDO USARLO
      - Quieres saber DE QUÉ o DE QUIÉN habla un texto sin leerlo.
      - Etiquetado automático de noticias, tickets de soporte o reseñas por las
        marcas/productos/lugares que mencionan.
      - Construir un índice o buscador: guardas las entidades como facetas de
        filtrado ("todos los tickets que mencionan la sucursal Polanco").
      - Enriquecer datos: muchas entidades conocidas traen `metadata` con su URL
        de Wikipedia y su MID del Knowledge Graph de Google, lo que te permite
        desambiguar ("Jordan" el país vs. "Jordan" la persona) y enlazar a una
        base de conocimiento externa.
      - Detectar PII en texto libre: los tipos PERSON, ADDRESS, PHONE_NUMBER
        sirven como primer filtro para anonimizar datos.

    CUÁNDO **NO** USARLO
      - Si lo que quieres es la opinión sobre cada entidad, esto no te la da:
        usa sample_analyze_entity_sentiment.
      - Si quieres el tema general del documento ("Deportes", "Finanzas"), usa
        sample_classify_text: las entidades son piezas sueltas, no un tema.

    CÓMO LEER LA SALIDA
      - salience (0.0 a 1.0): qué tan central es la entidad en ESTE texto. Es
        relativa al documento, no absoluta. Regla práctica: > 0.3 suele ser un
        tema principal; < 0.05 es una mención de pasada que casi siempre puedes
        descartar.
      - mentions: cada vez que la entidad aparece, con su posición. Útil para
        resaltar coincidencias en la interfaz.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_analyze_entities(
        ...     "Apple opened a new store in Mexico City last Tuesday."
        ... )
        Apple        ORGANIZATION  salience=0.648   # <- el texto trata de Apple
        store        LOCATION      salience=0.317
        Mexico City  LOCATION      salience=0.036   # <- casi irrelevante

      OJO CON LA INTUICIÓN: "Mexico City" es un nombre propio llamativo y aun
      así saca 0.036, por debajo del sustantivo común "store". La salience mide
      centralidad en la frase, NO lo importante que te parezca a ti la entidad.
      La frase trata de que Apple abrió una tienda; la ciudad es circunstancia.
      Si filtras por salience vas a descartar nombres propios, y normalmente eso
      es lo correcto.

      Uso típico: quedarte solo con lo importante y guardarlo como etiquetas.

        principales = [e.name for e in response.entities if e.salience > 0.2]
        # -> ['Apple', 'store']   (Mexico City NO entra)

    Args:
      text_content: El texto a analizar.
    """

    client = language_v1.LanguageServiceClient()

    # text_content = 'California is a state.'

    # Available types: PLAIN_TEXT, HTML
    type_ = language_v1.Document.Type.PLAIN_TEXT

    # Optional. If not specified, the language is automatically detected.
    # For list of supported languages:
    # https://cloud.google.com/natural-language/docs/languages
    language = "en"
    document = {"content": text_content, "type_": type_, "language": language}

    # Available values: NONE, UTF8, UTF16, UTF32
    encoding_type = language_v1.EncodingType.UTF8

    response = client.analyze_entities(
        request={"document": document, "encoding_type": encoding_type}
    )

    # Loop through entitites returned from the API
    for entity in response.entities:
        print("Representative name for the entity: {}".format(entity.name))

        # Get entity type, e.g. PERSON, LOCATION, ADDRESS, NUMBER, et al
        print("Entity type: {}".format(language_v1.Entity.Type(entity.type_).name))

        # Get the salience score associated with the entity in the [0, 1.0] range
        print("Salience score: {}".format(entity.salience))

        # Loop over the metadata associated with entity. For many known entities,
        # the metadata is a Wikipedia URL (wikipedia_url) and Knowledge Graph MID (mid).
        # Some entity types may have additional metadata, e.g. ADDRESS entities
        # may have metadata for the address street_name, postal_code, et al.
        for metadata_name, metadata_value in entity.metadata.items():
            print("{}: {}".format(metadata_name, metadata_value))

        # Loop over the mentions of this entity in the input document.
        # The API currently supports proper noun mentions.
        for mention in entity.mentions:
            print("Mention text: {}".format(mention.text.content))

            # Get the mention type, e.g. PROPER for proper noun
            print(
                "Mention type: {}".format(
                    language_v1.EntityMention.Type(mention.type_).name
                )
            )

    # Get the language of the text, which will be the same as
    # the language specified in the request or, if not specified,
    # the automatically-detected language.
    print("Language of the text: {}".format(response.language))

def sample_analyze_entity_sentiment(text_content):
    """
    Combina los dos análisis anteriores: encuentra las entidades Y calcula qué
    sentimiento expresa el texto sobre CADA UNA por separado.

    CUÁNDO USARLO
      - Este es el análisis que quieres para reseñas de producto, encuestas y
        NPS abiertos. El sentimiento global miente cuando una opinión es mixta:
        "La comida estuvo increíble pero el servicio fue pésimo" da un score
        global de ~0.0 (neutral), que es exactamente la conclusión equivocada.
        Aquí obtienes comida: +0.9 y servicio: -0.8, que es accionable.
      - Comparar cómo se habla de tu marca vs. la competencia dentro del mismo
        texto ("preferí X sobre Y").
      - Priorizar tickets de soporte por el componente que genera queja, no por
        el tono general del mensaje.

    CUÁNDO **NO** USARLO
      - Si el texto habla de una sola cosa y solo necesitas "¿positivo o
        negativo?", sample_analyze_sentiment es más barato y más simple.
      - Si no te interesa la opinión, solo qué se menciona: usa
        sample_analyze_entities.

    CÓMO LEER LA SALIDA
      Por cada entidad obtienes score y magnitude, además de salience:
      - score (-1.0 a 1.0): la dirección de la opinión sobre esa entidad.
      - magnitude (0.0 a +inf): la intensidad ACUMULADA. Crece con cada mención
        emocional, así que una entidad muy repetida tendrá magnitude alta aunque
        su score sea neutral por cancelarse opiniones opuestas.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_analyze_entity_sentiment(
        ...     "The food was amazing but the service was terrible."
        ... )
        food     type=OTHER  salience=0.730  sentiment=+0.4  magnitude=0.4
        service  type=OTHER  salience=0.270  sentiment=-0.4  magnitude=0.4

      Dos cosas que la intuición falla y conviene ver medidas:
      - Los scores son ±0.4, no ±0.9. "amazing" y "terrible" son palabras muy
        fuertes y aun así la API es conservadora. Si pones el umbral en ±0.5 te
        quedas sin detectar este caso, que es justo el que querías detectar.
        Por eso el snippet de abajo corta en ±0.25.
      - El `type` es OTHER, no algo lucido como FOOD: "food" y "service" son
        sustantivos comunes, no entidades del mundo. Aquí eso da igual, porque
        lo que te importa es el sentimiento, no el tipo.

      Uso típico: sacar el listado de aspectos que hay que arreglar.

        quejas = [
            e.name for e in response.entities if e.sentiment.score < -0.25
        ]

    Args:
      text_content: El texto a analizar.
    """

    client = language_v1.LanguageServiceClient()

    # text_content = 'Grapes are good. Bananas are bad.'

    # Available types: PLAIN_TEXT, HTML
    type_ = language_v1.types.Document.Type.PLAIN_TEXT

    # Optional. If not specified, the language is automatically detected.
    # For list of supported languages:
    # https://cloud.google.com/natural-language/docs/languages
    language = "en"
    document = {"content": text_content, "type_": type_, "language": language}

    # Available values: NONE, UTF8, UTF16, UTF32
    encoding_type = language_v1.EncodingType.UTF8

    response = client.analyze_entity_sentiment(
        request={"document": document, "encoding_type": encoding_type}
    )
    # Loop through entitites returned from the API
    for entity in response.entities:
        print("Representative name for the entity: {}".format(entity.name))
        # Get entity type, e.g. PERSON, LOCATION, ADDRESS, NUMBER, et al
        print("Entity type: {}".format(language_v1.Entity.Type(entity.type_).name))
        # Get the salience score associated with the entity in the [0, 1.0] range
        print("Salience score: {}".format(entity.salience))
        # Get the aggregate sentiment expressed for this entity in the provided document.
        sentiment = entity.sentiment
        print("Entity sentiment score: {}".format(sentiment.score))
        print("Entity sentiment magnitude: {}".format(sentiment.magnitude))
        # Loop over the metadata associated with entity. For many known entities,
        # the metadata is a Wikipedia URL (wikipedia_url) and Knowledge Graph MID (mid).
        # Some entity types may have additional metadata, e.g. ADDRESS entities
        # may have metadata for the address street_name, postal_code, et al.
        for metadata_name, metadata_value in entity.metadata.items():
            print("{} = {}".format(metadata_name, metadata_value))

        # Loop over the mentions of this entity in the input document.
        # The API currently supports proper noun mentions.
        for mention in entity.mentions:
            print("Mention text: {}".format(mention.text.content))
            # Get the mention type, e.g. PROPER for proper noun
            print(
                "Mention type: {}".format(
                    language_v1.EntityMention.Type(mention.type_).name
                )
            )

    # Get the language of the text, which will be the same as
    # the language specified in the request or, if not specified,
    # the automatically-detected language.
    print("Language of the text: {}".format(response.language))

def sample_classify_text(text_content):
    """
    Asigna el texto a categorías temáticas de una taxonomía predefinida por
    Google, jerárquica y con ~1.000 categorías
    (ej. "/Arts & Entertainment/TV & Video/TV Shows").

    CUÁNDO USARLO
      - Organizar automáticamente un archivo grande de documentos, artículos o
        páginas por tema, sin haber entrenado ningún modelo.
      - Enrutar contenido: mandar cada nota al canal, feed o equipo correcto.
      - Recomendación por contenido: dos artículos con categorías parecidas se
        parecen entre sí, aunque no compartan ni una palabra.
      - Moderación básica o segmentación publicitaria por tema sensible.

    CUÁNDO **NO** USARLO
      - Si tus categorías son propias del negocio ("devolución", "facturación",
        "cambio de plan"), esta taxonomía NO las contiene. Ahí necesitas un
        clasificador propio (AutoML / Vertex AI) o reglas sobre entidades.
      - Si el texto es corto. La API exige aproximadamente 20 palabras como
        mínimo; con menos devuelve una lista de categorías vacía. Un tuit o el
        asunto de un correo típicamente no alcanzan.

    CÓMO LEER LA SALIDA
      - Devuelve VARIAS categorías, ordenadas por `confidence` (0.0 a 1.0).
      - La ruta es jerárquica: puedes cortarla al primer nivel si no necesitas
        tanto detalle.
      - Este código usa el modelo V2, que cubre más idiomas y categorías que V1.
      - Sobre el umbral, ver el ejemplo: 0.5 es más agresivo de lo que parece.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_classify_text(
        ...     "That actor on TV makes movies in Hollywood and also stars in "
        ...     "a variety of popular new TV shows."
        ... )
        /Arts & Entertainment/TV & Video/TV Shows & Programs        0.537
        /Arts & Entertainment/Movies/Other                          0.141
        /Arts & Entertainment/Celebrities & Entertainment News       0.127
        /Arts & Entertainment/Entertainment Industry/Film & TV       0.111

      CALIBRA EL UMBRAL CON DATOS REALES, no con la intuición. Este texto es
      inequívocamente sobre televisión y la mejor categoría saca 0.537, no 0.9:
      la confianza se REPARTE entre categorías vecinas y plausibles. Consecuencia
      práctica: un corte en 0.5 aquí deja pasar UNA sola categoría, y con un
      texto un poco más ambiguo no deja pasar ninguna.
        - Si quieres UNA etiqueta, ordena por confidence y toma la primera, sin
          umbral fijo.
        - Si quieres varias, 0.1 - 0.2 es un corte más realista que 0.5.

      Uso típico: quedarte con el tema de primer nivel para agrupar.

        temas = {
            c.name.split("/")[1]
            for c in response.categories
            if c.confidence > 0.1
        }   # -> {"Arts & Entertainment"}

    Args:
      text_content: El texto a analizar (necesita ~20 palabras o más).
    """

    client = language_v1.LanguageServiceClient()

    # text_content = "That actor on TV makes movies in Hollywood and also stars in a variety of popular new TV shows."
    print(text_content)
    # Available types: PLAIN_TEXT, HTML
    type_ = language_v1.Document.Type.PLAIN_TEXT

    # Optional. If not specified, the language is automatically detected.
    # For list of supported languages:
    # https://cloud.google.com/natural-language/docs/languages
    language = "en"
    document = {"content": text_content, "type_": type_, "language": language}

    content_categories_version = (
        language_v1.ClassificationModelOptions.V2Model.ContentCategoriesVersion.V2
    )
    response = client.classify_text(
        request={
            "document": document,
            "classification_model_options": {
                "v2_model": {"content_categories_version": content_categories_version}
            },
        }
    )
    print(response)
    # Loop through classified categories returned from the API
    for category in response.categories:
        # Get the name of the category representing the document.
        # See the predefined taxonomy of categories:
        # https://cloud.google.com/natural-language/docs/categories
        print("Category name: {}".format(category.name))
        # Get the confidence. Number representing how certain the classifier
        # is that this category represents the provided text.
        print("Confidence: {}".format(category.confidence))

def sample_analyze_sentiment(content):
    """
    Da UN solo veredicto emocional para todo el documento.

    CUÁNDO USARLO
      - Es el análisis más barato y directo cuando el texto habla de un solo
        asunto y solo necesitas el termómetro: reseñas de una estrella a cinco,
        respuestas de encuesta, comentarios de redes sobre una campaña.
      - Métricas agregadas en el tiempo: promediar el score diario para ver si
        la percepción de un lanzamiento sube o baja.
      - Filtro de entrada: detectar los mensajes muy negativos de un buzón para
        atenderlos primero.

    CUÁNDO **NO** USARLO
      - Si el texto es largo o mezcla temas. El score se promedia y las
        opiniones opuestas se cancelan: usa sample_analyze_entity_sentiment.
      - No detecta sarcasmo ni ironía; en foros y redes eso es una fuente real
        de error.

    CÓMO LEER LA SALIDA (los dos valores se leen JUNTOS)
      - score (-1.0 a 1.0): la dirección del sentimiento.
      - magnitude (0.0 a +inf): cuánta carga emocional hay en total; crece con
        la longitud del texto.
      - El caso que confunde a todo el mundo: score ~0.0 es AMBIGUO por sí solo.
            score 0.0 + magnitude baja -> texto neutro, sin emoción (un manual).
            score 0.0 + magnitude alta -> texto MUY emocional pero mixto,
                                          positivo y negativo a partes iguales.
        Nunca reportes "neutral" sin mirar la magnitude.

      A diferencia del resto de funciones de este archivo, esta no fija el
      idioma: deja que la API lo autodetecte, así que funciona con español tal
      cual está escrita.

    EJEMPLO (salidas reales, medidas contra la API)
        >>> sample_analyze_sentiment("I loved it. Best purchase this year.")
        Score: 0.9   Magnitude: 1.9    # positivo claro

        >>> sample_analyze_sentiment("Great price. Awful build quality.")
        Score: 0.0   Magnitude: 1.8    # NO es neutral: es MIXTO.
                                       # La magnitude alta lo delata.

        >>> sample_analyze_sentiment("The device weighs 300 grams.")
        Score: -0.2  Magnitude: 0.2    # esto sí es "sin emoción"

      Fíjate en el tercero: el score NO es 0.00 exacto, es -0.2. Los textos
      neutros rara vez dan cero limpio, así que no compares con == 0. Lo que
      separa de verdad "neutro" de "mixto" es la magnitude (0.2 contra 1.8), no
      el score. Por eso el snippet de abajo pregunta primero por la magnitude.

      Uso típico: clasificar en tres cubetas sin caer en la trampa anterior.

        if sentiment.magnitude < 0.5:   etiqueta = "sin_emocion"
        elif sentiment.score > 0.25:    etiqueta = "positivo"
        elif sentiment.score < -0.25:   etiqueta = "negativo"
        else:                           etiqueta = "mixto"  # revisar a mano

    Args:
      content: El texto a analizar (acepta str o bytes UTF-8).
    """

    client = language_v1.LanguageServiceClient()

    if isinstance(content, six.binary_type):
        content = content.decode("utf-8")

    type_ = language_v1.Document.Type.PLAIN_TEXT
    document = {"type_": type_, "content": content}

    response = client.analyze_sentiment(request={"document": document})
    sentiment = response.document_sentiment
    print("Score: {}".format(sentiment.score))
    print("Magnitude: {}".format(sentiment.magnitude))

def sample_analyze_syntax(text_content):
    """
    Desarma la frase en piezas gramaticales: divide en oraciones y tokens, y de
    cada token da su categoría gramatical, su lema y de qué otra palabra depende
    (árbol de dependencias).

    CUÁNDO USARLO
      Este es el análisis de más bajo nivel: no te da una conclusión, te da
      materia prima para construir la tuya. Vale la pena cuando:
      - Necesitas extraer relaciones que ningún otro análisis te da, del tipo
        sujeto-verbo-objeto: "¿quién le hizo qué a quién?" en textos legales,
        médicos o notas periodísticas.
      - Quieres extraer sustantivos o adjetivos para una nube de palabras, un
        buscador de palabras clave o un glosario, ya lematizados: "corriendo",
        "corrió" y "corre" colapsan al lema "correr", así que cuentas conceptos
        y no conjugaciones.
      - Necesitas segmentar en oraciones de forma confiable (los puntos de
        "Dr.", "S.A." o "3.14" rompen cualquier `split(".")`).
      - Analizas estilo de escritura: proporción de voz pasiva, tiempos
        verbales, longitud media de oración, densidad de adjetivos.
      - Preprocesas texto antes de meterlo a un modelo propio.

    CUÁNDO **NO** USARLO
      - Si tu pregunta es de significado y no de forma, cualquiera de los otros
        cuatro análisis te la responde directamente y con mucho menos código.
        No reconstruyas sentimiento ni temas a partir de tokens.
      - Si solo quieres partir en palabras: un `.split()` o spaCy en local salen
        gratis; esta llamada se cobra.

    CÓMO LEER LA SALIDA
      - part_of_speech.tag: NOUN, VERB, ADJ, ADV, PRON, NUM, PUNCT...
      - part_of_speech.voice/tense: ACTIVE|PASSIVE, PAST|PRESENT|FUTURE.
      - lemma: la forma de diccionario del token.
      - dependency_edge.head_token_index: el ÍNDICE del token del que depende
        este, dentro de response.tokens; label es el tipo de relación (NSUBJ =
        sujeto, DOBJ = objeto directo, ROOT = verbo principal). Con esos dos
        campos reconstruyes el árbol de la oración.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_analyze_syntax("The dog chased the cat.")
        Token text: chased
        Part of Speech tag: VERB
        Voice: VOICE_UNKNOWN         # <- ojo, NO dice ACTIVE
        Tense: PAST
        Lemma: chase
        Head token index: 2          # ROOT: se apunta a sí mismo
        Label: ROOT
        ...

      SOBRE LOS CAMPOS "UNKNOWN": voice y tense vienen como VOICE_UNKNOWN /
      TENSE_UNKNOWN muy a menudo, incluso en verbos donde tú dirías que la voz
      es obvia (aquí "chased" es activa y aun así responde UNKNOWN). El campo
      solo se rellena cuando la morfología lo marca sin ambigüedad. Si vas a
      medir voz pasiva, cuenta la etiqueta de dependencia NSUBJPASS en lugar de
      fiarte de `voice`, o vas a subcontar muchísimo.

      Uso típico 1: sacar el verbo principal con su sujeto y su objeto.
      Hay que excluir la propia raíz (se apunta a sí misma) y quedarse con las
      etiquetas que te interesan; si no, también te llega la puntuación:

        raiz = next(i for i, t in enumerate(response.tokens)
                    if t.dependency_edge.label == language_v1.DependencyEdge.Label.ROOT)
        QUIERO = {"NSUBJ", "DOBJ"}
        partes = {
            language_v1.DependencyEdge.Label(t.dependency_edge.label).name:
                t.text.content
            for i, t in enumerate(response.tokens)
            if t.dependency_edge.head_token_index == raiz and i != raiz
            and language_v1.DependencyEdge.Label(t.dependency_edge.label).name in QUIERO
        }   # -> {'NSUBJ': 'dog', 'DOBJ': 'cat'}
        #
        # Sin el filtro QUIERO y sin `i != raiz`, lo que sale realmente es:
        #   {'NSUBJ': 'dog', 'ROOT': 'chased', 'DOBJ': 'cat', 'P': '.'}

      Uso típico 2: palabras clave lematizadas, sin conjugaciones repetidas.

        from collections import Counter
        NOUN = language_v1.PartOfSpeech.Tag.NOUN
        Counter(
            t.lemma for t in response.tokens if t.part_of_speech.tag == NOUN
        ).most_common(10)

    Args:
      text_content: El texto a analizar.
    """

    client = language_v1.LanguageServiceClient()

    # text_content = 'This is a short sentence.'

    # Available types: PLAIN_TEXT, HTML
    type_ = language_v1.Document.Type.PLAIN_TEXT

    # Optional. If not specified, the language is automatically detected.
    # For list of supported languages:
    # https://cloud.google.com/natural-language/docs/languages
    language = "en"
    document = {"content": text_content, "type_": type_, "language": language}

    # Available values: NONE, UTF8, UTF16, UTF32
    encoding_type = language_v1.EncodingType.UTF8

    response = client.analyze_syntax(
        request={"document": document, "encoding_type": encoding_type}
    )
    # Loop through tokens returned from the API
    for token in response.tokens:
        # Get the text content of this token. Usually a word or punctuation.
        text = token.text
        print("Token text: {}".format(text.content))
        print(
            "Location of this token in overall document: {}".format(text.begin_offset)
        )
        # Get the part of speech information for this token.
        # Part of speech is defined in:
        # http://www.lrec-conf.org/proceedings/lrec2012/pdf/274_Paper.pdf
        part_of_speech = token.part_of_speech
        # Get the tag, e.g. NOUN, ADJ for Adjective, et al.
        print(
            "Part of Speech tag: {}".format(
                language_v1.PartOfSpeech.Tag(part_of_speech.tag).name
            )
        )
        # Get the voice, e.g. ACTIVE or PASSIVE
        print(
            "Voice: {}".format(
                language_v1.PartOfSpeech.Voice(part_of_speech.voice).name
            )
        )
        # Get the tense, e.g. PAST, FUTURE, PRESENT, et al.
        print(
            "Tense: {}".format(
                language_v1.PartOfSpeech.Tense(part_of_speech.tense).name
            )
        )
        # See API reference for additional Part of Speech information available
        # Get the lemma of the token. Wikipedia lemma description
        # https://en.wikipedia.org/wiki/Lemma_(morphology)
        print("Lemma: {}".format(token.lemma))
        # Get the dependency tree parse information for this token.
        # For more information on dependency labels:
        # http://www.aclweb.org/anthology/P13-2017
        dependency_edge = token.dependency_edge
        print("Head token index: {}".format(dependency_edge.head_token_index))
        print(
            "Label: {}".format(
                language_v1.DependencyEdge.Label(dependency_edge.label).name
            )
        )

    # Get the language of the text, which will be the same as
    # the language specified in the request or, if not specified,
    # the automatically-detected language.
    print("Language of the text: {}".format(response.language))

def sample_moderate_text(text_content):
    """
    Detecta contenido dañino: clasifica el texto contra ~16 categorías de
    moderación (Toxic, Insult, Profanity, Violent, Sexual, Death/Harm & Tragedy,
    Firearms & Weapons, Illicit Drugs, War & Conflict, Politics, Religion,
    Health, Legal, Finance, Public Safety, Derogatory).

    CUÁNDO USARLO
      - Moderar contenido generado por usuarios ANTES de publicarlo: comentarios,
        reseñas, mensajes de chat, descripciones de perfil.
      - Filtrar la entrada o la salida de un chatbot.
      - Auditar un corpus histórico para encontrar el contenido problemático que
        ya está publicado.
      - Enrutar a revisión humana solo lo dudoso, en vez de revisarlo todo.

    CUÁNDO **NO** USARLO
      - Para saber el TEMA del texto: usa sample_classify_text. Ojo con la
        confusión fácil: aquí "Health" o "Politics" significan "esto toca un
        tema sensible que quizá quieras marcar", no "este artículo es de salud".
      - Para medir si un comentario es negativo: eso es sentimiento
        (sample_analyze_sentiment). Una queja furiosa puede ser perfectamente
        civilizada y no dar ninguna señal de toxicidad.
      - Como única barrera de seguridad. Es un clasificador probabilístico, no
        un veredicto; deja el corte final a una persona en los casos límite.

    CÓMO LEER LA SALIDA
      Esto sorprende: la API devuelve TODAS las categorías en CADA llamada, cada
      una con su `confidence` (0.0 a 1.0 = probabilidad de pertenecer a esa
      categoría). No te devuelve "las que aplican". Si imprimes todo vas a ver
      docenas de líneas con valores como 0.01, que significan "no aplica".
      Siempre tienes que filtrar por umbral. Punto de partida razonable: 0.5, y
      lo ajustas según cuántos falsos positivos toleres.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_moderate_text("You are an idiot and I hope you get hurt.")
        Toxic   : 0.97
        Violent : 0.95
        Insult  : 0.92
        Profanity: 0.75
        (el resto de categorías, por debajo del umbral, se omiten)

      Fíjate en que se disparan CUATRO categorías a la vez, no una. Las
      categorías se solapan mucho: casi nada es "solo Insult". Si construyes
      reglas, piensa en conjuntos de categorías, no en elegir "la ganadora".

      Uso típico: puerta de entrada para publicar o mandar a revisión.

        BLOQUEAR = {"Toxic", "Insult", "Profanity", "Violent", "Sexual"}
        riesgos = [
            c.name for c in response.moderation_categories
            if c.confidence > 0.5 and c.name in BLOQUEAR
        ]
        if riesgos:
            rechazar(motivo=riesgos)

    Args:
      text_content: El texto a moderar.
    """

    client = language_v1.LanguageServiceClient()

    type_ = language_v1.Document.Type.PLAIN_TEXT
    document = {"content": text_content, "type_": type_}

    response = client.moderate_text(request={"document": document})

    # La API devuelve TODAS las categorías siempre; filtramos las relevantes.
    umbral = 0.5
    relevantes = [
        c for c in response.moderation_categories if c.confidence >= umbral
    ]

    if not relevantes:
        print("Sin señales de moderación por encima de {}".format(umbral))
    for category in sorted(relevantes, key=lambda c: c.confidence, reverse=True):
        print("{:<24}: {:.2f}".format(category.name, category.confidence))


def sample_moderate_text_v2(text_content):
    """
    Igual que sample_moderate_text, pero contra la API v2 y pidiendo
    explícitamente el modelo de moderación versión 2.

    CUÁNDO USARLO
      - Es la opción preferible para moderación nueva: MODEL_VERSION_2 es el
        modelo más reciente y cubre más idiomas que el de v1. La v1 usa el
        modelo antiguo y no te deja elegir.
      - Si moderas contenido en varios idiomas, empieza por aquí.

    QUÉ CAMBIA ENTRE v1 Y v2 (importante antes de migrar todo el archivo)
      La v2 NO es simplemente "v1 mejorada": le quitaron cosas.
        - No existe analyze_syntax  -> el análisis sintáctico solo vive en v1.
        - No existe analyze_entity_sentiment -> el sentimiento por entidad solo
          vive en v1.
        - Entity ya no trae `salience` -> pierdes el ranking de importancia.
        - Document ya no lleva `language`, ahora es `language_code`, y la
          respuesta trae `language_supported` para avisarte si el idioma
          detectado realmente está soportado.
      Por eso este archivo se queda en v1 salvo para moderación: v1 sigue siendo
      la única versión con la superficie completa.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_moderate_text_v2("Eres un imbécil y espero que te vaya mal.")
        Insult           : 0.93
        Toxic            : 0.66
        Profanity        : 0.64
        Violent          : 0.51
        Religion & Belief: 0.50   # <- falso positivo, justo en el umbral

      Confirma que funciona con español sin configurar nada. Y ese último
      renglón es el mejor argumento para no automatizar a ciegas: la frase no
      tiene nada religioso, pero la categoría sale en 0.50. Filtra por las
      categorías que te importan (Insult, Toxic...) en vez de bloquear por
      "cualquier categoría por encima del umbral".

    Args:
      text_content: El texto a moderar.
    """

    client = language_v2.LanguageServiceClient()

    document = {
        "content": text_content,
        "type_": language_v2.Document.Type.PLAIN_TEXT,
    }

    response = client.moderate_text(
        request={
            "document": document,
            "model_version": language_v2.ModerateTextRequest.ModelVersion.MODEL_VERSION_2,
        }
    )

    umbral = 0.5
    for category in sorted(
        response.moderation_categories, key=lambda c: c.confidence, reverse=True
    ):
        if category.confidence >= umbral:
            print("{:<24}: {:.2f}".format(category.name, category.confidence))


def sample_annotate_text(text_content):
    """
    Ejecuta VARIOS análisis en UNA sola llamada, eligiendo cuáles con un mapa de
    features. Es el equivalente a llamar a las otras funciones por separado,
    pero con un único viaje de red.

    CUÁNDO USARLO
      - Siempre que necesites dos o más análisis del MISMO texto. Es el caso
        real más común y es justo lo que este repo no ofrecía: pedir entidades y
        sentimiento por separado son dos peticiones HTTP donde basta una.
      - Procesamiento por lotes de muchos documentos, donde la latencia por
        documento se multiplica: pasar de 4 llamadas a 1 recorta el tiempo
        total casi a la cuarta parte.
      - Cuando quieres una foto completa del documento para guardarla enriquecida
        en tu base de datos de una sola pasada.

    CUÁNDO **NO** USARLO
      - Si solo necesitas UN análisis: llama a su función directa, es más simple
        de leer y pesa lo mismo.

    OJO CON EL COSTO
      Ahorra latencia, **no** dinero. La facturación es por feature solicitada:
      pedir 4 features sobre un texto cuesta lo mismo que 4 llamadas separadas.
      Así que no actives features "por si acaso"; activa solo las que vas a leer.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_annotate_text("Apple's new laptop is fast but overpriced.")
        --- Sentimiento global ---
        score=-0.50  magnitude=0.50
        --- Entidades ---
        Apple (ORGANIZATION) salience=0.62 sentiment=0.00
        laptop (CONSUMER_GOOD) salience=0.38 sentiment=0.00
        --- Categorías ---
        --- Moderación ---

      Dos cosas que enseña esta salida real:
      - Categorías sale VACÍO por partida doble: en las features de abajo
        classify_text va en False, y además el texto no llega al mínimo de ~20
        palabras. Una sección vacía aquí casi nunca es un fallo.
      - El sentimiento por entidad es 0.00 en ambas pese a pedirlo. Que la
        feature esté activa no garantiza que haya señal: la frase opina sobre
        "the laptop" de forma difusa. No asumas que activar la feature siempre
        te devuelve números distintos de cero.

      Uso típico: elegir features según lo que tu pipeline necesite.

        features = {
            "extract_entities": True,
            "extract_document_sentiment": True,
            "classify_text": False,   # apagado = no se cobra
            "moderate_text": False,
        }

    Args:
      text_content: El texto a analizar.
    """

    client = language_v1.LanguageServiceClient()

    type_ = language_v1.Document.Type.PLAIN_TEXT
    document = {"content": text_content, "type_": type_, "language": "en"}
    encoding_type = language_v1.EncodingType.UTF8

    # Activa solo lo que vayas a usar: cada feature en True se factura aparte.
    features = {
        "extract_syntax": False,
        "extract_entities": True,
        "extract_document_sentiment": True,
        "extract_entity_sentiment": True,
        "classify_text": False,  # recuerda: necesita ~20 palabras o más
        "moderate_text": True,
    }

    response = client.annotate_text(
        request={
            "document": document,
            "features": features,
            "encoding_type": encoding_type,
        }
    )

    print("--- Sentimiento global ---")
    print(
        "score={:.2f}  magnitude={:.2f}".format(
            response.document_sentiment.score, response.document_sentiment.magnitude
        )
    )

    print("--- Entidades ---")
    for entity in response.entities:
        print(
            "{} ({}) salience={:.2f} sentiment={:.2f}".format(
                entity.name,
                language_v1.Entity.Type(entity.type_).name,
                entity.salience,
                entity.sentiment.score,
            )
        )

    print("--- Categorías ---")
    for category in response.categories:
        print("{} ({:.2f})".format(category.name, category.confidence))

    print("--- Moderación ---")
    for category in response.moderation_categories:
        if category.confidence >= 0.5:
            print("{} ({:.2f})".format(category.name, category.confidence))


def sample_analyze_html(html_content):
    """
    Analiza HTML crudo sin limpiarlo antes, usando Document.Type.HTML en lugar
    de PLAIN_TEXT.

    CUÁNDO USARLO
      - Analizas páginas web, correos en HTML o contenido salido de un CMS o de
        un editor de texto enriquecido.
      - Evitas escribir tu propio limpiador de etiquetas: la API descarta el
        marcado por ti, correctamente.
      - Los offsets que devuelve la API (begin_offset) siguen apuntando al HTML
        original, así que puedes resaltar la coincidencia en la página real.

    CUÁNDO **NO** USARLO
      - Si tu texto ya viene limpio: PLAIN_TEXT es lo correcto y evita que la
        API interprete como etiquetas cosas que solo son símbolos "<" y ">".

    CUIDADO
      El HTML cuenta ENTERO para la facturación, etiquetas incluidas. Una página
      con mucho <script> y <style> puede costar varias veces más que su texto
      visible. Si la relación marcado/contenido es mala, sale más barato extraer
      el <article> o el <main> antes de mandarlo.

    POR QUÉ IMPORTA DE VERDAD: el mismo HTML mandado como PLAIN_TEXT
      Esta es la comparación medida contra la API, y es el argumento real para
      usar Type.HTML:

        HTML  -> Tesla 0.418 | company 0.350 | factory 0.188 | Berlin 0.044
        PLAIN -> company 0.650 | factory 0.208 | 'Berlin.' 0.141

      Mandar HTML como PLAIN_TEXT no "ensucia un poco" el resultado: la entidad
      principal (Tesla) DESAPARECE del análisis, porque queda pegada al marcado
      y no se reconoce. Y aparece una entidad fantasma, 'Berlin.' con el punto
      dentro. No es una mejora marginal; es la diferencia entre un análisis
      correcto y uno inservible.

    LO QUE EL MARCADO **NO** HACE
      Podrías esperar que meter algo en un <h1> le suba la salience. Medido, no
      pasa: con el MISMO orden de palabras, Tesla saca 0.464 como texto plano y
      0.418 dentro de un <h1> — es decir, prácticamente igual, y si acaso un
      pelín menos. Lo que mueve la salience es la posición y la centralidad en
      la frase, no la etiqueta que la envuelve. No reestructures tu HTML
      esperando cambiar el ranking de entidades.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_analyze_html(
        ...     "<h1>Tesla</h1><p>The company opened a factory in Berlin.</p>"
        ... )
        Tesla (ORGANIZATION) salience=0.42
        company (ORGANIZATION) salience=0.35
        factory (LOCATION) salience=0.19
        Berlin (LOCATION) salience=0.04

    Args:
      html_content: El HTML crudo a analizar.
    """

    client = language_v1.LanguageServiceClient()

    # La única diferencia con el resto del archivo: HTML en vez de PLAIN_TEXT.
    type_ = language_v1.Document.Type.HTML
    document = {"content": html_content, "type_": type_, "language": "en"}
    encoding_type = language_v1.EncodingType.UTF8

    response = client.analyze_entities(
        request={"document": document, "encoding_type": encoding_type}
    )

    for entity in response.entities:
        print(
            "{} ({}) salience={:.2f}".format(
                entity.name,
                language_v1.Entity.Type(entity.type_).name,
                entity.salience,
            )
        )


def sample_analyze_gcs_file(gcs_uri):
    """
    Analiza un archivo que vive en Cloud Storage, pasando su URI en vez del
    contenido: la API lo lee directo del bucket.

    CUÁNDO USARLO
      - Los documentos ya están en un bucket y no quieres descargarlos a tu
        proceso solo para volver a subirlos dentro de la petición. Ahorras el
        viaje de bajada, la memoria y el ancho de banda de salida.
      - Archivos grandes: no tienes que cargarlos en RAM.
      - Pipelines serverless (Cloud Functions, Cloud Run) disparados por la
        subida de un archivo: recibes el evento con el URI y se lo pasas tal
        cual, sin tocar el contenido.
      - Trabajo por lotes sobre miles de documentos ya almacenados.

    CUÁNDO **NO** USARLO
      - Si el texto lo tienes en memoria (viene de un formulario, de tu base de
        datos o de una API): usar `content` es directo y no involucra un bucket.

    REQUISITOS QUE SUELEN FALLAR
      - `content` y `gcs_content_uri` son MUTUAMENTE EXCLUYENTES (son un `oneof`
        del protocolo). Y no falla si mandas los dos: el segundo que asignes
        BORRA al primero en silencio, así que acabarías analizando un texto que
        no querías sin ver ningún error. Manda solo uno.
      - El formato es gs://nombre-del-bucket/ruta/archivo.txt (no una URL https).
      - La cuenta de servicio de key.json necesita permiso de lectura sobre el
        bucket (rol storage.objectViewer). Este es el error número uno al
        empezar: el análisis funciona en local con `content` y truena aquí con
        un 403 que no menciona a Natural Language sino a Storage.
      - El archivo debe ser texto plano o HTML. Un PDF o un .docx no sirven:
        para eso necesitas Document AI antes.

    EJEMPLO
        >>> sample_analyze_gcs_file("gs://mi-bucket/reseñas/2024-01.txt")
        Score: 0.4
        Magnitude: 12.8      # magnitude alta: es un archivo largo

    Args:
      gcs_uri: URI del objeto en Cloud Storage (gs://bucket/objeto.txt).
    """

    client = language_v1.LanguageServiceClient()

    # Nota: se usa gcs_content_uri EN LUGAR de content, nunca ambos.
    document = {
        "gcs_content_uri": gcs_uri,
        "type_": language_v1.Document.Type.PLAIN_TEXT,
    }

    response = client.analyze_sentiment(
        request={"document": document, "encoding_type": language_v1.EncodingType.UTF8}
    )

    sentiment = response.document_sentiment
    print("Score: {}".format(sentiment.score))
    print("Magnitude: {}".format(sentiment.magnitude))


def sample_analyze_sentiment_by_sentence(text_content):
    """
    Desglosa el sentimiento oración por oración, además del global.

    CUÁNDO USARLO
      - Textos largos donde el promedio global no dice nada: transcripciones de
        llamadas, respuestas extensas de encuesta, artículos, hilos de correo.
      - Localizar exactamente DÓNDE se tuerce un texto: útil para resaltar en la
        interfaz la frase que disparó la alerta, en vez de mostrar un número.
      - Seguir el arco de una conversación: ver si una llamada de soporte
        empieza mal y termina bien, o al revés.

    POR QUÉ ESTA FUNCIÓN EXISTE
      Este desglose YA VIENE en la respuesta de analyze_sentiment: está en
      `response.sentences` y no cuesta ni una llamada extra ni un centavo más.
      sample_analyze_sentiment simplemente lo estaba tirando a la basura, que es
      la razón de ser de esta función.

    RELACIÓN CON LOS OTROS ANÁLISIS
      Divide por POSICIÓN en el texto, no por tema. Si lo que quieres es
      "¿qué opinan del envío?" sin importar en qué frase se dijo, lo tuyo es
      sample_analyze_entity_sentiment.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_analyze_sentiment_by_sentence(
        ...     "The setup was painful. Support took days to answer. "
        ...     "Once running, it works beautifully."
        ... )
        Global: score=-0.10  magnitude=2.30   # "casi neutral": no dice nada
          [-0.80] The setup was painful.
          [-0.50] Support took days to answer.
          [+0.90] Once running, it works beautifully.

      El global (-0.10) es la media de tres frases que van de -0.80 a +0.90. Un
      tablero que muestre solo ese -0.10 está escondiendo toda la información
      útil: que la instalación es un desastre y que el producto, una vez
      andando, encanta.

      Uso típico: sacar las frases problemáticas para revisión.

        peores = sorted(response.sentences, key=lambda s: s.sentiment.score)[:3]
        for s in peores:
            print(s.text.content, s.text.begin_offset)

    Args:
      text_content: El texto a analizar.
    """

    client = language_v1.LanguageServiceClient()

    document = {
        "content": text_content,
        "type_": language_v1.Document.Type.PLAIN_TEXT,
    }

    response = client.analyze_sentiment(
        request={"document": document, "encoding_type": language_v1.EncodingType.UTF8}
    )

    overall = response.document_sentiment
    print(
        "Global: score={:.2f}  magnitude={:.2f}".format(
            overall.score, overall.magnitude
        )
    )

    # Este desglose viene gratis en la misma respuesta.
    for sentence in response.sentences:
        print(
            "  [{:+.2f}] {}".format(
                sentence.sentiment.score, sentence.text.content
            )
        )


def sample_link_entities_knowledge_graph(text_content):
    """
    Resuelve cada entidad contra el Knowledge Graph de Google: no solo dice qué
    se menciona, sino A QUÉ COSA DEL MUNDO se refiere exactamente, devolviendo
    un identificador estable (el MID) y su URL de Wikipedia.

    QUÉ PROBLEMA RESUELVE (y por qué no basta con sample_analyze_entities)
      sample_analyze_entities te devuelve el TEXTO de la entidad. Eso es una
      cadena, y las cadenas son ambiguas:
        - "Jordan" -> ¿el país, Michael Jordan, o la marca Air Jordan?
        - "Apple"  -> ¿la empresa o la fruta?
        - "Amazon" -> ¿la empresa, el río o la selva?
      Y al revés, la misma cosa se escribe de mil formas: "IBM",
      "International Business Machines" y "Big Blue" son la misma empresa, pero
      como texto no se parecen en nada.

      El MID (Machine ID, del estilo "/m/0k8z") resuelve las dos cosas a la vez:
      es un identificador único y estable. Todas las variantes de escritura de
      IBM devuelven el MISMO MID, y "Jordan" el país devuelve un MID distinto de
      "Jordan" la persona.

    CUÁNDO USARLO
      - Deduplicar entidades entre documentos: si agrupas por texto, "IBM" y
        "International Business Machines" te quedan como dos filas distintas en
        el reporte. Si agrupas por MID, es una sola. Este es el caso de uso
        principal y la razón de ser de esta función.
      - Cruzar con datos externos: el MID es la llave para consultar la
        Knowledge Graph Search API y traer descripción, tipo, imagen, etc.:
            https://kgsearch.googleapis.com/v1/entities:search?ids=/m/0k8z&key=API_KEY
      - Enriquecer una interfaz: enlazar cada mención a su artículo de Wikipedia.
      - Distinguir lo conocido de lo desconocido. Una entidad SIN metadata no es
        un error: significa que el Knowledge Graph no la reconoce. Suele ser una
        entidad genérica ("the company", "the laptop") o algo demasiado local o
        nuevo. Esa separación es señal útil por sí sola: te dice qué menciones
        son marcas reales y cuáles son ruido.

    CUÁNDO **NO** USARLO
      - Si solo quieres etiquetas legibles para mostrar, sample_analyze_entities
        te basta y es más simple.
      - Si tus entidades son internas del negocio (SKUs, nombres de clientes,
        códigos de proyecto): el Knowledge Graph no las conoce y vas a obtener
        metadata vacía en casi todo. Ahí necesitas tu propio catálogo.

    OJO: ESTO SOLO EXISTE EN v1
      La v2 de la API NO devuelve `wikipedia_url` en la metadata (ni `salience`).
      El campo `metadata` sigue existiendo en el objeto Entity de v2, así que tu
      código NO va a fallar si migras: simplemente te va a llegar vacío y vas a
      perder el enlace en silencio. Por eso esta función está fijada a v1.

      La razón de fondo es de arquitectura, no de calidad: la v2 corre sobre
      modelos basados en PaLM, y el enlace al Knowledge Graph es un paso de
      resolución contra una base de conocimiento externa, un sistema aparte que
      el modelo no produce por sí solo. Google documenta la ausencia
      ("No wikipedia_url as metadata", notas de versión del 28/08/2023) pero no
      publica el motivo; lo anterior es la explicación más razonable, no una
      declaración oficial.

    EJEMPLO (salida real, medida contra la API)
        >>> sample_link_entities_knowledge_graph(
        ...     "Apple released the iPhone in Cupertino. "
        ...     "I ate an apple for breakfast."
        ... )
        --- Entidades enlazadas al Knowledge Graph ---
        Apple      ORGANIZATION   mid=/m/0k8z      .../wiki/Apple_Inc.
        iPhone     CONSUMER_GOOD  mid=/m/027lnzs   .../wiki/IPhone
        Cupertino  LOCATION       mid=/m/0r679     .../wiki/Cupertino,_California

        --- Sin enlace (el Knowledge Graph no las reconoce) ---
        breakfast  OTHER
        apple      OTHER

      Ahí está la desambiguación: "Apple" y "apple" son casi el mismo texto y la
      API separa la empresa (enlazada, con MID) de la fruta (sin enlazar).

    LÍMITES REALES DEL ENLACE (medidos, no supuestos)
      El enlace NO es mágico y conviene saber dónde falla antes de diseñar algo
      encima:

      1) Necesita contexto. Con la frase escueta
         "Jordan won six titles with the Bulls. Jordan borders Israel."
         NINGUNO de los dos "Jordan" se enlaza: salen sin MID, tipados
         ORGANIZATION y PERSON. Con contexto suficiente sí funcionan:
         "Michael Jordan won six NBA championships with the Chicago Bulls"
         -> Michael Jordan /m/054c1, NBA /m/05jvx, Chicago Bulls /m/0jm74.
         Es decir: prefiere NO enlazar antes que enlazar mal. Para tu código eso
         es buena noticia (pocos falsos enlaces) pero significa que la tasa de
         enlace depende mucho de qué tan rico sea el texto.

      2) Los apodos no se resuelven. En
         "IBM reported earnings. International Business Machines has offices
          worldwide. Big Blue is old."
         "IBM" se enlaza a /m/03sc8 y absorbe "International Business Machines"
         como mención de la misma entidad — o sea, la deduplicación por nombre
         formal SÍ funciona. Pero "Big Blue" sale como ORGANIZATION aparte y SIN
         MID. No cuentes con que los apodos colapsen solos.

      Uso típico: agrupar menciones por identidad real y no por escritura.

        from collections import Counter
        Counter(
            e.metadata["mid"]
            for e in response.entities
            if "mid" in e.metadata
        )

    Args:
      text_content: El texto a analizar.
    """

    client = language_v1.LanguageServiceClient()

    document = {
        "content": text_content,
        "type_": language_v1.Document.Type.PLAIN_TEXT,
        "language": "en",
    }

    response = client.analyze_entities(
        request={"document": document, "encoding_type": language_v1.EncodingType.UTF8}
    )

    # Una entidad está "enlazada" si el Knowledge Graph la reconoció y por tanto
    # trae un MID. wikipedia_url es opcional incluso entre las enlazadas: hay
    # entidades con MID que no tienen artículo de Wikipedia.
    enlazadas = [e for e in response.entities if e.metadata.get("mid")]
    sueltas = [e for e in response.entities if not e.metadata.get("mid")]

    print("--- Entidades enlazadas al Knowledge Graph ---")
    if not enlazadas:
        print("(ninguna: el Knowledge Graph no reconoció nada en este texto)")
    for entity in enlazadas:
        print(
            "{:<15} {:<13} mid={:<12} {}".format(
                entity.name,
                language_v1.Entity.Type(entity.type_).name,
                entity.metadata.get("mid"),
                entity.metadata.get("wikipedia_url", "(sin artículo en Wikipedia)"),
            )
        )

    print("\n--- Sin enlace (el Knowledge Graph no las reconoce) ---")
    if not sueltas:
        print("(ninguna)")
    for entity in sueltas:
        print(
            "{:<15} {}".format(
                entity.name, language_v1.Entity.Type(entity.type_).name
            )
        )


if __name__ == "__main__":
    # Descomenta el análisis que quieras probar. Cada uno es una llamada
    # facturable independiente, por eso están comentados y no se ejecutan todos.
    #
    # Ojo: este texto de ejemplo es largo a propósito. Es de los pocos que
    # supera el mínimo de ~20 palabras que exige sample_classify_text; con una
    # frase corta esa función devuelve cero categorías y parece "rota".
    text_content = "That actor on TV makes movies in Hollywood and also stars in a variety of popular new TV shows."

    # --- Los cinco análisis base ---
    # sample_classify_text(text_content)             # ¿de qué TEMA es el texto?
    # sample_analyze_entities(text_content)          # ¿de QUÉ/QUIÉN habla?
    # sample_analyze_entity_sentiment(text_content)  # ¿qué opina de cada cosa?
    # sample_analyze_sentiment(text_content)         # ¿positivo o negativo?
    sample_analyze_syntax(text_content)              # ¿cómo está construida?

    # --- Entity linking: desambiguar contra el Knowledge Graph (solo v1) ---
    # sample_link_entities_knowledge_graph(
    #     "Jordan won six titles with the Bulls. Jordan borders Israel."
    # )

    # --- Moderación de contenido ---
    # sample_moderate_text("You are an idiot and I hope you get hurt.")
    # sample_moderate_text_v2("Eres un imbécil.")   # modelo v2, más idiomas

    # --- Varios análisis en UNA sola llamada ---
    # sample_annotate_text(text_content)

    # --- Otras formas de entrada ---
    # sample_analyze_html("<h1>Tesla</h1><p>The company opened a factory in Berlin.</p>")
    # sample_analyze_gcs_file("gs://mi-bucket/reseñas/2024-01.txt")

    # --- Sentimiento oración por oración (viene gratis en la misma llamada) ---
    # sample_analyze_sentiment_by_sentence(
    #     "The setup was painful. Support took days to answer. "
    #     "Once running, it works beautifully."
    # )
