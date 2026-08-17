"""
Conversión: JSON del modelo local -> objetos de respuesta de Google.

DECISIÓN DE DISEÑO CENTRAL
Aquí no se define ninguna clase de respuesta propia. Construimos e devolvemos
los MISMOS objetos proto de google-cloud-language que devuelve la API oficial
(language_v1.AnalyzeSentimentResponse, etc.).

Por qué: la compatibilidad "de verdad" no es tener campos con el mismo nombre.
Es que `isinstance(r, language_v1.AnalyzeEntitiesResponse)` sea cierto, que
`language_v1.Entity.Type(e.type_).name` funcione, que los enums comparen igual y
que el objeto se serialice idéntico. Reimplementar todo eso a mano sería mucho
trabajo para acabar con un parecido aproximado. Reusando los protos, la
compatibilidad del modelo de datos es exacta por construcción y solo nos queda
el problema interesante: que los NÚMEROS se parezcan.

Todo lo de este módulo es tolerante a fallos del modelo: un campo ausente, un
enum inventado o un índice fuera de rango no deben tumbar la llamada. Ante la
duda, se cae al valor neutro que usaría Google.
"""

from google.cloud import language_v1

from . import prompts


def _redondear_10(x):
    """
    Google devuelve scores en pasos de 0.1. Redondeamos igual para no perder
    paridad por ruido decimal irrelevante.
    """
    return round(round(float(x), 1), 4)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _enum_o_default(enum_cls, nombre, default):
    """Convierte un nombre de enum en su valor, tolerando basura del modelo."""
    if not nombre:
        return default
    try:
        return enum_cls[str(nombre).strip().upper().replace(" ", "_")]
    except KeyError:
        return default


def _tipo_entidad(nombre):
    return _enum_o_default(
        language_v1.Entity.Type, nombre, language_v1.Entity.Type.OTHER
    )


def _offsets(texto, fragmentos):
    """
    Localiza cada fragmento en el texto original y devuelve su begin_offset.

    El modelo no nos da posiciones fiables, así que las calculamos nosotros. Se
    busca de izquierda a derecha sin solapar, para que menciones repetidas
    ("Jordan ... Jordan") caigan en posiciones distintas y no todas en la
    primera. Si un fragmento no aparece literal (el modelo lo reescribió),
    devolvemos -1, que es lo que Google usa cuando no hay offset.
    """
    salida = []
    cursor = 0
    for frag in fragmentos:
        if not frag:
            salida.append((frag, -1))
            continue
        pos = texto.find(frag, cursor)
        if pos == -1:
            pos = texto.find(frag)  # segundo intento desde el principio
        salida.append((frag, pos))
        if pos != -1:
            cursor = pos + len(frag)
    return salida


# --------------------------------------------------------------------------
# Sentimiento
# --------------------------------------------------------------------------
def sentimiento(datos, texto, idioma):
    """
    Construye AnalyzeSentimentResponse a partir de {"sentences": [...]}.

    Aquí se aplican las dos leyes que medimos contra Google:
        score del documento = media de los scores por oración
        magnitude           = suma de |score| por oración

    Medido sobre 6 textos, la media reproducía el score de Google con una
    diferencia máxima de 0.03, y la suma de valores absolutos reproducía la
    magnitude con una diferencia máxima de 0.4 (siempre por debajo, de ahí el
    factor de corrección).
    """
    brutos = datos.get("sentences") or []

    frases = []
    for item in brutos:
        if not isinstance(item, dict):
            continue
        t = str(item.get("text", "")).strip()
        if not t:
            continue
        frases.append((t, _clamp(_redondear_10(item.get("score", 0.0)), -1.0, 1.0)))

    if not frases:
        # Sin oraciones utilizables tratamos el texto como una sola, neutra.
        frases = [(texto.strip(), 0.0)]

    scores = [s for _, s in frases]
    doc_score = _redondear_10(sum(scores) / len(scores))
    # 1.05: la suma cruda queda sistemáticamente por debajo de la magnitude de
    # Google. Es una corrección empírica, no una constante de la API.
    doc_magnitude = round(sum(abs(s) for s in scores) * 1.05, 1)

    resp = language_v1.AnalyzeSentimentResponse()
    resp.document_sentiment = language_v1.Sentiment(
        score=doc_score, magnitude=doc_magnitude
    )
    resp.language = idioma

    for t, s in _reasignar_offsets(texto, frases):
        oracion = language_v1.Sentence()
        oracion.text = language_v1.TextSpan(content=t[0], begin_offset=t[1])
        oracion.sentiment = language_v1.Sentiment(score=s, magnitude=abs(s))
        resp.sentences.append(oracion)

    return resp


def _reasignar_offsets(texto, frases):
    pares = _offsets(texto, [t for t, _ in frases])
    return [(par, s) for par, (_, s) in zip(pares, frases)]


# --------------------------------------------------------------------------
# Entidades
# --------------------------------------------------------------------------
# Los MIDs del Knowledge Graph se suprimen por defecto. Ver EMITIR_MIDS.
#
# MEDICIÓN QUE MOTIVA ESTO: comparados contra Google sobre 7 entidades famosas
# (Apple, iPhone, Cupertino, Michael Jordan, NBA, Chicago Bulls, Tesla, Berlin),
# el modelo local acertó 0. Todos tenían el formato correcto "/m/xxxxx" y todos
# apuntaban a otra cosa.
#
# Un MID equivocado es peor que ninguno: el caso de uso principal del MID es
# deduplicar entidades entre documentos, así que un identificador inventado
# produce uniones incorrectas EN SILENCIO, sin lanzar ningún error. La ausencia
# del campo, en cambio, es un estado que Google también produce (entidad no
# reconocida por el Knowledge Graph) y que el código consumidor ya sabe manejar.
#
# wikipedia_url sí se emite: es texto memorizable y en las pruebas salió
# correcto ("Apple_Inc.", "Cupertino,_California"). Aun así, verifícalo si lo
# vas a usar para algo que importe.
EMITIR_MIDS = False


def _construir_entidades(datos, texto, con_sentimiento, linker=None):
    brutos = datos.get("entities") or []
    limpias = []

    for item in brutos:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("name", "")).strip()
        if not nombre:
            continue
        peso = item.get("weight", 0)
        try:
            peso = max(0.0, float(peso))
        except (TypeError, ValueError):
            peso = 0.0
        limpias.append((nombre, item, peso))

    # LEY 3: la salience de todas las entidades suma exactamente 1.0.
    # Normalizamos el peso crudo del modelo en vez de creerle una salience.
    total = sum(p for _, _, p in limpias)

    entidades = []
    for nombre, item, peso in limpias:
        ent = language_v1.Entity()
        ent.name = nombre
        ent.type_ = _tipo_entidad(item.get("type"))
        ent.salience = round(peso / total, 6) if total > 0 else 0.0

        es_propio = bool(item.get("proper"))

        if linker is not None:
            # Enlace real contra Wikidata: el modelo elige entre candidatos
            # existentes en vez de recordar un identificador.
            mid, url = linker.enlazar(nombre, texto, es_propio)
            if mid:
                ent.metadata["mid"] = mid
            if url:
                ent.metadata["wikipedia_url"] = url
        else:
            # Sin linker: los valores que propone el modelo NO son de fiar.
            mid = item.get("mid")
            if EMITIR_MIDS and isinstance(mid, str) and mid.startswith("/"):
                ent.metadata["mid"] = mid
            url = item.get("wikipedia_url")
            if isinstance(url, str) and url.startswith("http"):
                ent.metadata["wikipedia_url"] = url
        tipo_mencion = (
            language_v1.EntityMention.Type.PROPER
            if es_propio
            else language_v1.EntityMention.Type.COMMON
        )

        menciones = item.get("mentions") or [nombre]
        if not isinstance(menciones, list):
            menciones = [nombre]
        for frag, off in _offsets(texto, [str(m) for m in menciones if m]):
            m = language_v1.EntityMention()
            m.text = language_v1.TextSpan(content=frag, begin_offset=off)
            m.type_ = tipo_mencion
            if con_sentimiento:
                s = _clamp(_redondear_10(item.get("score", 0.0)), -1.0, 1.0)
                m.sentiment = language_v1.Sentiment(score=s, magnitude=abs(s))
            ent.mentions.append(m)

        if con_sentimiento:
            s = _clamp(_redondear_10(item.get("score", 0.0)), -1.0, 1.0)
            ent.sentiment = language_v1.Sentiment(score=s, magnitude=abs(s))

        entidades.append(ent)

    # Google las devuelve ordenadas por salience descendente.
    entidades.sort(key=lambda e: e.salience, reverse=True)
    return entidades


def entidades(datos, texto, idioma, linker=None):
    resp = language_v1.AnalyzeEntitiesResponse()
    resp.language = idioma
    for e in _construir_entidades(datos, texto, False, linker):
        resp.entities.append(e)
    return resp


def entidades_con_sentimiento(datos, texto, idioma, linker=None):
    resp = language_v1.AnalyzeEntitySentimentResponse()
    resp.language = idioma
    for e in _construir_entidades(datos, texto, True, linker):
        resp.entities.append(e)
    return resp


# --------------------------------------------------------------------------
# Clasificación
# --------------------------------------------------------------------------
def clasificacion(datos, texto):
    """
    Construye ClassifyTextResponse.

    El control de longitud mínima NO está aquí sino en el cliente, porque
    medimos que Google lanza InvalidArgument(400) en vez de devolver una lista
    vacía. Rechazar es responsabilidad de quien recibe la petición, no de quien
    formatea la respuesta.
    """
    resp = language_v1.ClassifyTextResponse()
    brutas = datos.get("categories") or []
    limpias = []
    for item in brutas:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("name", "")).strip()
        if not nombre.startswith("/"):
            continue
        try:
            conf = _clamp(float(item.get("confidence", 0.0)), 0.0, 1.0)
        except (TypeError, ValueError):
            continue
        limpias.append((nombre.rstrip("/"), conf))

    limpias.sort(key=lambda x: x[1], reverse=True)
    for nombre, conf in limpias:
        resp.categories.append(
            language_v1.ClassificationCategory(name=nombre, confidence=round(conf, 6))
        )
    return resp


# --------------------------------------------------------------------------
# Moderación
# --------------------------------------------------------------------------
def moderacion(datos):
    """
    Construye ModerateTextResponse.

    Google SIEMPRE devuelve las 16 categorías, en un orden fijo. Reproducimos
    ambas cosas: rellenamos las que el modelo omitió y reordenamos según el
    orden del maestro. Sin esto, un consumidor que recorra la lista por índice
    se rompería.
    """
    brutas = {}
    for item in datos.get("categories") or []:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("name", "")).strip()
        try:
            brutas[nombre.lower()] = _clamp(float(item.get("confidence", 0.0)), 0.0, 1.0)
        except (TypeError, ValueError):
            continue

    resp = language_v1.ModerateTextResponse()
    for nombre in prompts.MODERATION_CATEGORIES:
        conf = brutas.get(nombre.lower(), 0.05)
        resp.moderation_categories.append(
            language_v1.ClassificationCategory(name=nombre, confidence=round(conf, 6))
        )
    return resp


# --------------------------------------------------------------------------
# Sintaxis
# --------------------------------------------------------------------------
def sintaxis(datos, texto, idioma):
    resp = language_v1.AnalyzeSyntaxResponse()
    resp.language = idioma

    brutos = [t for t in (datos.get("tokens") or []) if isinstance(t, dict)]
    n = len(brutos)

    textos = [str(t.get("text", "")) for t in brutos]
    posiciones = _offsets(texto, textos)

    for i, item in enumerate(brutos):
        tok = language_v1.Token()
        contenido, off = posiciones[i]
        tok.text = language_v1.TextSpan(content=contenido, begin_offset=off)
        tok.lemma = str(item.get("lemma") or contenido)

        pos = language_v1.PartOfSpeech()
        pos.tag = _enum_o_default(
            language_v1.PartOfSpeech.Tag, item.get("pos"),
            language_v1.PartOfSpeech.Tag.UNKNOWN,
        )
        pos.tense = _enum_o_default(
            language_v1.PartOfSpeech.Tense, item.get("tense"),
            language_v1.PartOfSpeech.Tense.TENSE_UNKNOWN,
        )
        tok.part_of_speech = pos

        arista = language_v1.DependencyEdge()
        try:
            head = int(item.get("head", i))
        except (TypeError, ValueError):
            head = i
        # Un head fuera de rango rompería a cualquiera que recorra el árbol.
        arista.head_token_index = head if 0 <= head < n else i
        arista.label = _enum_o_default(
            language_v1.DependencyEdge.Label, item.get("label"),
            language_v1.DependencyEdge.Label.UNKNOWN,
        )
        tok.dependency_edge = arista

        resp.tokens.append(tok)

    return resp


# --------------------------------------------------------------------------
# annotate_text
# --------------------------------------------------------------------------
def anotacion(parciales, idioma):
    """Funde las respuestas parciales en un único AnnotateTextResponse."""
    resp = language_v1.AnnotateTextResponse()
    resp.language = idioma

    if "sentiment" in parciales:
        s = parciales["sentiment"]
        resp.document_sentiment = s.document_sentiment
        for o in s.sentences:
            resp.sentences.append(o)
    if "entities" in parciales:
        for e in parciales["entities"].entities:
            resp.entities.append(e)
    if "syntax" in parciales:
        for t in parciales["syntax"].tokens:
            resp.tokens.append(t)
    if "categories" in parciales:
        for c in parciales["categories"].categories:
            resp.categories.append(c)
    if "moderation" in parciales:
        for c in parciales["moderation"].moderation_categories:
            resp.moderation_categories.append(c)

    return resp
