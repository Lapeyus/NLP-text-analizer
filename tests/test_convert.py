"""Pruebas de nlp_local.convert: JSON del modelo -> protos de Google."""

from google.cloud import language_v1

from nlp_local import convert, prompts


class FakeLinker:
    def __init__(self):
        self.vistos = []

    def enlazar(self, nombre, contexto, es_propio=True):
        self.vistos.append((nombre, es_propio))
        if nombre == "Apple":
            return ("/m/0k8z", "https://en.wikipedia.org/wiki/Apple_Inc.")
        return (None, None)


class TestSentimiento:
    def test_leyes_media_y_suma(self):
        texto = "Great price. Awful build quality."
        datos = {"sentences": [
            {"text": "Great price.", "score": 0.8},
            {"text": "Awful build quality.", "score": -0.8},
        ]}
        r = convert.sentimiento(datos, texto, "en")
        assert isinstance(r, language_v1.AnalyzeSentimentResponse)
        assert r.document_sentiment.score == 0.0
        # magnitude = sum(|score|) * 1.05, redondeado a 0.1
        assert abs(r.document_sentiment.magnitude - 1.7) < 1e-6
        assert [s.text.begin_offset for s in r.sentences] == [0, 13]
        assert r.language == "en"

    def test_clamp_y_redondeo(self):
        r = convert.sentimiento(
            {"sentences": [{"text": "x", "score": 3.27}]}, "x", "en")
        assert r.sentences[0].sentiment.score == 1.0

    def test_sin_oraciones_utilizables(self):
        r = convert.sentimiento({"sentences": ["basura", {"text": ""}]},
                                " Hola. ", "es")
        assert len(r.sentences) == 1
        assert r.sentences[0].text.content == "Hola."
        assert r.document_sentiment.score == 0.0

    def test_fragmento_reescrito_offset_menos_uno(self):
        r = convert.sentimiento(
            {"sentences": [{"text": "no aparece", "score": 0.5}]}, "otro", "en")
        assert r.sentences[0].text.begin_offset == -1


class TestEntidades:
    DATOS = {"entities": [
        {"name": "Mexico City", "type": "LOCATION", "weight": 4, "proper": True,
         "mentions": ["Mexico City"], "mid": "/m/inventado"},
        {"name": "Apple", "type": "organization", "weight": 65, "proper": True,
         "mentions": ["Apple"], "wikipedia_url": "https://en.wikipedia.org/wiki/Apple_Inc."},
        {"name": "store", "type": "TIPO_RARO", "weight": 31, "proper": False},
        {"name": "", "weight": 10},
        "no-es-dict",
    ]}
    TEXTO = "Apple opened a new store in Mexico City."

    def test_salience_suma_uno_y_orden(self):
        r = convert.entidades(self.DATOS, self.TEXTO, "en")
        assert isinstance(r, language_v1.AnalyzeEntitiesResponse)
        assert [e.name for e in r.entities] == ["Apple", "store", "Mexico City"]
        assert abs(sum(e.salience for e in r.entities) - 1.0) < 1e-5

    def test_tipos_y_menciones(self):
        r = convert.entidades(self.DATOS, self.TEXTO, "en")
        T = language_v1.Entity.Type
        apple, store, cdmx = r.entities
        assert apple.type_ == T.ORGANIZATION
        assert store.type_ == T.OTHER  # enum desconocido -> OTHER
        assert apple.mentions[0].type_ == language_v1.EntityMention.Type.PROPER
        assert store.mentions[0].type_ == language_v1.EntityMention.Type.COMMON
        assert cdmx.mentions[0].text.begin_offset == self.TEXTO.index("Mexico")

    def test_mid_del_modelo_se_suprime(self):
        r = convert.entidades(self.DATOS, self.TEXTO, "en")
        cdmx = r.entities[2]
        assert "mid" not in cdmx.metadata
        assert r.entities[0].metadata["wikipedia_url"].endswith("Apple_Inc.")

    def test_con_linker(self):
        linker = FakeLinker()
        r = convert.entidades(self.DATOS, self.TEXTO, "en", linker)
        assert r.entities[0].metadata["mid"] == "/m/0k8z"
        assert "mid" not in r.entities[2].metadata
        assert ("store", False) in linker.vistos

    def test_con_sentimiento(self):
        datos = {"entities": [
            {"name": "food", "weight": 70, "score": 0.44, "mentions": ["food"]},
            {"name": "service", "weight": 30, "score": -0.4},
        ]}
        r = convert.entidades_con_sentimiento(
            datos, "The food was amazing but the service was terrible.", "en")
        assert isinstance(r, language_v1.AnalyzeEntitySentimentResponse)
        assert abs(r.entities[0].sentiment.score - 0.4) < 1e-6
        assert abs(r.entities[1].sentiment.score + 0.4) < 1e-6
        assert abs(r.entities[0].mentions[0].sentiment.score - 0.4) < 1e-6

    def test_pesos_cero(self):
        r = convert.entidades({"entities": [{"name": "a", "weight": "x"}]}, "a", "en")
        assert r.entities[0].salience == 0.0


def test_clasificacion_filtra_y_ordena():
    datos = {"categories": [
        {"name": "/News/", "confidence": 0.2},
        {"name": "sin barra", "confidence": 0.9},
        {"name": "/Finance/Banking", "confidence": 1.7},
        {"name": "/X", "confidence": "nan?"},
    ]}
    r = convert.clasificacion(datos, "texto")
    assert [(c.name, round(c.confidence, 3)) for c in r.categories] == [
        ("/Finance/Banking", 1.0), ("/News", 0.2)]


def test_moderacion_siempre_16_en_orden():
    datos = {"categories": [{"name": "toxic", "confidence": 0.97},
                            {"name": "Inventada", "confidence": 0.9}]}
    r = convert.moderacion(datos)
    nombres = [c.name for c in r.moderation_categories]
    assert nombres == prompts.MODERATION_CATEGORIES
    assert len(nombres) == 16
    conf = {c.name: round(c.confidence, 3) for c in r.moderation_categories}
    assert conf["Toxic"] == 0.97
    assert conf["Legal"] == 0.05


def test_sintaxis_head_fuera_de_rango():
    datos = {"tokens": [
        {"text": "Dogs", "pos": "NOUN", "lemma": "dog", "head": 1, "label": "NSUBJ"},
        {"text": "bark", "pos": "VERB", "head": 1, "label": "ROOT", "tense": "PRESENT"},
        {"text": ".", "pos": "???", "head": 99, "label": "P"},
    ]}
    r = convert.sintaxis(datos, "Dogs bark.", "en")
    assert [t.dependency_edge.head_token_index for t in r.tokens] == [1, 1, 2]
    assert r.tokens[1].lemma == "bark"
    assert r.tokens[2].part_of_speech.tag == language_v1.PartOfSpeech.Tag.UNKNOWN
    assert r.tokens[2].text.begin_offset == 9


def test_anotacion_funde_parciales():
    s = convert.sentimiento({"sentences": [{"text": "Hi.", "score": 0.5}]}, "Hi.", "en")
    m = convert.moderacion({})
    r = convert.anotacion({"sentiment": s, "moderation": m}, "en")
    assert isinstance(r, language_v1.AnnotateTextResponse)
    assert len(r.sentences) == 1
    assert len(r.moderation_categories) == 16
    assert len(r.entities) == 0
