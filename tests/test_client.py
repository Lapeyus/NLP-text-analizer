"""Pruebas del cliente compatible nlp_local.LanguageServiceClient (sin Ollama)."""

import pytest
from google.api_core.exceptions import InvalidArgument
from google.cloud import language_v1 as real_v1

import nlp_local
from nlp_local import client as cl
from nlp_local import prompts

SENT = {"sentences": [{"text": "The food was amazing but the service was terrible.",
                       "score": -0.4}]}


def _cliente(fake_backend, respuestas):
    fb = fake_backend(respuestas)
    return nlp_local.LanguageServiceClient(backend=fb, entity_linking=False), fb


def test_fachada_language_v1_reusa_tipos_reales():
    v1 = nlp_local.language_v1
    assert v1.LanguageServiceClient is nlp_local.LanguageServiceClient
    assert v1.Entity is real_v1.Entity
    assert v1.Document.Type.HTML == real_v1.Document.Type.HTML
    assert "LanguageServiceClient" in dir(v1)


def test_acepta_kwargs_de_google(fake_backend):
    c = nlp_local.LanguageServiceClient(backend=fake_backend([]),
                                        credentials=object(), client_options={})
    assert c.model == "fake-model"


def test_analyze_sentiment_devuelve_proto(fake_backend):
    c, fb = _cliente(fake_backend, [("sentimiento compatible", SENT)])
    doc = {"content": SENT["sentences"][0]["text"],
           "type_": real_v1.Document.Type.PLAIN_TEXT}
    r = c.analyze_sentiment(request={"document": doc, "encoding_type": real_v1.EncodingType.UTF8})
    assert isinstance(r, real_v1.AnalyzeSentimentResponse)
    assert abs(r.document_sentiment.score + 0.4) < 1e-6
    assert r.language == "en"
    assert fb.llamadas[0][1].startswith("Texto:\n")


def test_request_como_proto_y_document_suelto(fake_backend):
    c, _ = _cliente(fake_backend, [("sentimiento compatible", SENT)])
    doc = real_v1.Document(content="Hola, esto es una prueba de la función.",
                           type_=real_v1.Document.Type.PLAIN_TEXT, language="es")
    r1 = c.analyze_sentiment(request=real_v1.AnalyzeSentimentRequest(document=doc))
    r2 = c.analyze_sentiment(document=doc)
    assert r1.language == r2.language == "es"


def test_falta_document(fake_backend):
    c, _ = _cliente(fake_backend, [])
    with pytest.raises(ValueError):
        c.analyze_sentiment(request={})


def test_gcs_no_soportado(fake_backend):
    c, _ = _cliente(fake_backend, [])
    with pytest.raises(NotImplementedError):
        c.analyze_entities(request={"document": {"gcs_content_uri": "gs://b/o.txt"}})


def test_html_se_desmarca_antes_del_modelo(fake_backend):
    c, fb = _cliente(fake_backend, [("extracción de entidades", {"entities": []})])
    html = "<h1>Tesla</h1><script>x()</script><p>Opened &amp; built.</p>"
    c.analyze_entities(document={"content": html, "type_": real_v1.Document.Type.HTML})
    assert fb.llamadas[0][1] == "Texto:\nTesla Opened & built."


def test_classify_texto_corto_lanza_invalid_argument(fake_backend):
    c, fb = _cliente(fake_backend, [])
    with pytest.raises(InvalidArgument, match="too few tokens"):
        c.classify_text(document={"content": "Short text."})
    assert fb.llamadas == []  # no se llama al modelo


def test_classify_umbral_exacto(fake_backend):
    c, _ = _cliente(fake_backend, [("clasificador", {"categories": [
        {"name": "/News", "confidence": 0.6}]})])
    diecinueve = " ".join(["w"] * 19)
    veinte = " ".join(["w"] * 20)
    assert cl.contar_tokens(diecinueve) == 19
    with pytest.raises(InvalidArgument):
        c.classify_text(document={"content": diecinueve})
    r = c.classify_text(document={"content": veinte})
    assert r.categories[0].name == "/News"
    assert prompts.CLASSIFY_MIN_TOKENS == 20


def test_contar_tokens_puntuacion():
    assert cl.contar_tokens("Hola, mundo.") == 4
    assert cl.contar_tokens(None) == 0


@pytest.mark.parametrize("texto,esperado", [
    ("The food was amazing.", "en"),
    ("La comida de la casa es buena", "es"),
    ("¿Qué?", "es"),
])
def test_detectar_idioma(texto, esperado):
    assert cl._detectar_idioma(texto) == esperado


def test_syntax_pide_techo_alto(fake_backend):
    c, fb = _cliente(fake_backend, [("analizador sintáctico", {"tokens": []})])
    c.analyze_syntax(document={"content": "Hi."})
    assert fb.llamadas[0][2] == 4096


def test_annotate_text_una_llamada_por_feature(fake_backend):
    c, fb = _cliente(fake_backend, [
        ("sentimiento por entidad", {"entities": [{"name": "food", "weight": 1, "score": 0.4}]}),
        ("sentimiento compatible", SENT),
        ("moderación", {"categories": []}),
    ])
    r = c.annotate_text(request={
        "document": {"content": SENT["sentences"][0]["text"]},
        "features": {"extract_document_sentiment": True,
                     "extract_entity_sentiment": True,
                     "moderate_text": True},
    })
    assert isinstance(r, real_v1.AnnotateTextResponse)
    assert len(fb.llamadas) == 3
    assert r.entities[0].name == "food"
    assert len(r.moderation_categories) == 16
    assert len(r.categories) == 0
