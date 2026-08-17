"""
LanguageServiceClient: reemplazo directo del cliente de Google.

Objetivo de compatibilidad: que este cliente se pueda intercambiar por el de
Google cambiando UNA línea de import, sin tocar el resto del programa.

    # antes
    from google.cloud import language_v1
    client = language_v1.LanguageServiceClient()

    # después
    from nlp_local import language_v1
    client = language_v1.LanguageServiceClient()

Para lograrlo se respetan tres cosas:

1. Las firmas. Cada método acepta `request=` como dict o como objeto de
   petición del proto, más los mismos argumentos sueltos.
2. Los tipos de retorno. Se devuelven los protos reales de Google (ver
   convert.py), no imitaciones.
3. Los comportamientos raros. Si Google devuelve lista vacía en vez de error
   ante un texto corto en classify_text, aquí pasa lo mismo.

Lo que NO se replica: la autenticación, las cuotas y los errores de red de
Google. Este cliente no habla con Google en ningún momento.
"""

import re

from google.api_core.exceptions import InvalidArgument
from google.cloud import language_v1

from . import convert, prompts
from .backend import CachingBackend, OllamaBackend
from .linking import EntityLinker

# Tokenizador aproximado: palabras y signos de puntuación por separado.
# Reproduce el conteo con el que Google decide si un texto es demasiado corto
# para classify_text; verificado contra el umbral real barriendo longitudes.
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def contar_tokens(texto):
    return len(_TOKEN.findall(texto or ""))


def _desempaquetar(request, document, kwargs):
    """
    Normaliza las tres formas de llamada que acepta el cliente de Google:
    request como dict, request como proto, o document suelto.
    """
    if request is None:
        request = {}
    if not isinstance(request, dict):
        # Objeto de petición del proto: lo pasamos a dict.
        try:
            request = type(request).to_dict(request)
        except Exception:
            request = {
                "document": getattr(request, "document", None),
                "encoding_type": getattr(request, "encoding_type", None),
            }

    doc = document if document is not None else request.get("document")
    if doc is None:
        raise ValueError("falta 'document' en la petición")

    if not isinstance(doc, dict):
        try:
            doc = language_v1.Document.to_dict(doc)
        except Exception:
            doc = {
                "content": getattr(doc, "content", ""),
                "type_": getattr(doc, "type_", None),
                "language": getattr(doc, "language", ""),
                "gcs_content_uri": getattr(doc, "gcs_content_uri", ""),
            }

    kwargs.update(request)
    return doc


def _texto_de(doc):
    """
    Saca el texto del documento.

    Si viene como HTML lo desmarcamos, porque medimos que analizar HTML crudo
    como si fuera texto plano destruye el análisis: la entidad principal
    desaparece y aparecen entidades fantasma con la puntuación pegada.
    """
    if doc.get("gcs_content_uri"):
        raise NotImplementedError(
            "nlp_local no lee de Cloud Storage: descarga el contenido y pásalo "
            "en document['content']."
        )

    contenido = doc.get("content") or ""
    tipo = doc.get("type_")
    es_html = tipo == language_v1.Document.Type.HTML or str(tipo).endswith("HTML")
    if es_html:
        contenido = _quitar_marcado(contenido)
    return contenido


def _quitar_marcado(html):
    """Quita etiquetas, <script> y <style>, y normaliza espacios."""
    import re

    sin = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    sin = re.sub(r"(?s)<[^>]+>", " ", sin)
    sin = (
        sin.replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", sin).strip()


class LanguageServiceClient:
    """
    Cliente compatible respaldado por un LLM local.

    El modelo es reemplazable por cualquiera de estas vías, de mayor a menor
    precedencia:

        LanguageServiceClient(model="gemma4:31b-mlx")   # explícito
        NLP_LOCAL_MODEL=... en el entorno                # por entorno
        el default de backend.py                         # último recurso

    También acepta un backend entero, para cambiar de motor y no solo de modelo:

        LanguageServiceClient(backend=MiBackendPropio())
    """

    def __init__(self, model=None, backend=None, cache=True,
                 entity_linking=False, **_ignorado):
        # **_ignorado absorbe credentials=, client_options=, transport=... para
        # que el código escrito contra Google no tenga que cambiar.
        if backend is None:
            backend = OllamaBackend(model=model)
            if cache:
                backend = CachingBackend(backend)
        self.backend = backend

        # entity_linking requiere RED (consulta Wikidata). Va apagado por
        # defecto para no romper en silencio la propiedad de funcionar sin
        # conexión, que es media razón de ser de esta librería.
        self.linker = EntityLinker(backend=backend) if entity_linking else None

    def __repr__(self):
        return "nlp_local.LanguageServiceClient(backend={!r})".format(self.backend)

    @property
    def model(self):
        return getattr(self.backend, "model", "desconocido")

    # -- helpers internos ---------------------------------------------------

    def _idioma(self, doc, texto):
        declarado = (doc.get("language") or "").strip()
        return declarado or _detectar_idioma(texto)

    def _pedir(self, system_prompt, texto, num_predict=None):
        return self.backend.complete_json(
            system_prompt, "Texto:\n" + texto, num_predict=num_predict
        )

    # -- API pública --------------------------------------------------------

    def analyze_sentiment(self, request=None, document=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        datos = self._pedir(prompts.SENTIMENT, texto)
        return convert.sentimiento(datos, texto, self._idioma(doc, texto))

    def analyze_entities(self, request=None, document=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        datos = self._pedir(prompts.ENTITIES, texto)
        return convert.entidades(datos, texto, self._idioma(doc, texto), self.linker)

    def analyze_entity_sentiment(self, request=None, document=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        datos = self._pedir(prompts.ENTITY_SENTIMENT, texto)
        return convert.entidades_con_sentimiento(
            datos, texto, self._idioma(doc, texto), self.linker
        )

    def classify_text(self, request=None, document=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        # Google LANZA InvalidArgument(400) por debajo del mínimo, no devuelve
        # lista vacía. Replicamos el error, mensaje incluido, para que el
        # try/except del consumidor se comporte igual con ambas librerías.
        if contar_tokens(texto) < prompts.CLASSIFY_MIN_TOKENS:
            raise InvalidArgument(
                "Invalid text content: too few tokens (words) to process."
            )
        datos = self._pedir(prompts.CLASSIFY, texto)
        return convert.clasificacion(datos, texto)

    def moderate_text(self, request=None, document=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        datos = self._pedir(prompts.MODERATE, texto)
        return convert.moderacion(datos)

    def analyze_syntax(self, request=None, document=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        # La sintaxis produce ~6 campos por token: necesita techo alto.
        datos = self._pedir(prompts.SYNTAX, texto, num_predict=4096)
        return convert.sintaxis(datos, texto, self._idioma(doc, texto))

    def annotate_text(self, request=None, document=None, features=None, **kwargs):
        doc = _desempaquetar(request, document, kwargs)
        texto = _texto_de(doc)
        feats = features if features is not None else kwargs.get("features") or {}
        if not isinstance(feats, dict):
            try:
                feats = language_v1.AnnotateTextRequest.Features.to_dict(feats)
            except Exception:
                feats = {}

        idioma = self._idioma(doc, texto)
        parciales = {}

        # A diferencia de Google, aquí cada feature es una llamada al modelo:
        # no hay un modelo único que produzca todo de una pasada. La ventaja de
        # annotate_text sobre llamadas sueltas, en local, es de comodidad, no de
        # latencia.
        if feats.get("extract_document_sentiment"):
            parciales["sentiment"] = convert.sentimiento(
                self._pedir(prompts.SENTIMENT, texto), texto, idioma
            )
        if feats.get("extract_entity_sentiment"):
            parciales["entities"] = convert.entidades_con_sentimiento(
                self._pedir(prompts.ENTITY_SENTIMENT, texto), texto, idioma
            )
        elif feats.get("extract_entities"):
            parciales["entities"] = convert.entidades(
                self._pedir(prompts.ENTITIES, texto), texto, idioma
            )
        if feats.get("extract_syntax"):
            parciales["syntax"] = convert.sintaxis(
                self._pedir(prompts.SYNTAX, texto, num_predict=4096), texto, idioma
            )
        if feats.get("classify_text"):
            parciales["categories"] = self.classify_text(document=doc)
        if feats.get("moderate_text"):
            parciales["moderation"] = convert.moderacion(
                self._pedir(prompts.MODERATE, texto)
            )

        return convert.anotacion(parciales, idioma)


def _detectar_idioma(texto):
    """
    Detección mínima es/en.

    Google devuelve el idioma detectado en `response.language` y hay código que
    lo lee, así que no podemos dejarlo vacío. No usamos una librería externa
    para no añadir dependencias por un campo informativo; si te importa de
    verdad, declara `language` en el documento y esto no se ejecuta.
    """
    t = " " + texto.lower() + " "
    marcas_es = (
        " el ", " la ", " los ", " las ", " de ", " que ", " y ", " es ",
        " un ", " una ", " por ", " con ", " para ", " pero ", " más ",
    )
    puntos = sum(t.count(m) for m in marcas_es)
    if any(c in texto for c in "ñáéíóúü¿¡"):
        puntos += 3
    return "es" if puntos >= 3 else "en"
