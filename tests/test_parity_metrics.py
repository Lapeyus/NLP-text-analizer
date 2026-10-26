"""Pruebas del arnés de paridad: métricas, corpus y comparación de errores."""

from google.api_core.exceptions import InvalidArgument
from google.cloud import language_v1 as G

from parity import corpus, harness, metrics


def _sent(score, mag):
    r = G.AnalyzeSentimentResponse()
    r.document_sentiment = G.Sentiment(score=score, magnitude=mag)
    return r


def _ents(*pares):
    r = G.AnalyzeEntitiesResponse()
    for nombre, sal, tipo in pares:
        r.entities.append(G.Entity(name=nombre, salience=sal, type_=tipo))
    return r


T = G.Entity.Type


class TestSentiment:
    def test_identicos_100(self):
        assert metrics.sentiment(_sent(-0.4, 0.4), _sent(-0.4, 0.4))["score"] == 100.0

    def test_sin_acantilado(self):
        m = metrics.sentiment(_sent(-0.1, 0.1), _sent(-0.2, 0.2))
        assert m["signo_ok"] is True
        assert m["score"] > 90

    def test_signo_invertido(self):
        m = metrics.sentiment(_sent(0.8, 0.8), _sent(-0.8, 0.8))
        assert m["signo_ok"] is False
        assert m["score"] < 40


class TestEntities:
    def test_contencion_de_nombres(self):
        g = _ents(("Michael Jordan", 0.7, T.PERSON), ("NBA", 0.3, T.ORGANIZATION))
        loc = _ents(("Jordan", 0.6, T.PERSON), ("NBA", 0.4, T.ORGANIZATION))
        m = metrics.entities(g, loc)
        assert m["f1_nombres"] == 100.0
        assert m["acc_tipo"] == 100.0
        assert m["top1_ok"] is True

    def test_entidad_faltante(self):
        g = _ents(("Apple", 0.6, T.ORGANIZATION), ("store", 0.4, T.LOCATION))
        loc = _ents(("Apple", 1.0, T.ORGANIZATION))
        m = metrics.entities(g, loc)
        assert m["solo_google"] == ["store"]
        assert round(m["f1_nombres"], 1) == 66.7

    def test_f1_vacios(self):
        assert metrics._f1(0, 0, 0) == 1.0
        assert metrics._f1(0, 2, 0) == 0.0


def test_classify_ambas_vacias():
    vacio = G.ClassifyTextResponse()
    assert metrics.classify(vacio, vacio)["score"] == 100.0


def test_classify_nivel1_con_ruta_corta():
    g = G.ClassifyTextResponse(categories=[G.ClassificationCategory(name="/Finance/Banking", confidence=0.8)])
    loc = G.ClassifyTextResponse(categories=[G.ClassificationCategory(name="/Finance", confidence=0.8)])
    m = metrics.classify(g, loc)
    assert m["f1_nivel1"] == 100.0 and m["top1_ok"] is True
    assert m["f1_ruta_exacta"] == 0.0
    assert m["score"] == 80.0


def test_moderate_disparo():
    def resp(conf):
        return G.ModerateTextResponse(moderation_categories=[
            G.ClassificationCategory(name=n, confidence=c) for n, c in conf])
    g = resp([("Toxic", 0.97), ("Legal", 0.01)])
    loc = resp([("Toxic", 0.90), ("Legal", 0.05)])
    m = metrics.moderate(g, loc)
    assert m["f1_disparo"] == 100.0 and m["orden_identico"] is True


def test_syntax_tokenizacion_distinta_anula():
    g = G.AnalyzeSyntaxResponse(tokens=[G.Token(text=G.TextSpan(content="a"))])
    loc = G.AnalyzeSyntaxResponse(tokens=[G.Token(text=G.TextSpan(content="b"))])
    m = metrics.syntax(g, loc)
    assert m["tokenizacion_identica"] is False and m["score"] == 0.0


def test_corpus_y_plan():
    claves = [k for k, _ in corpus.CORPUS]
    assert len(claves) == 20 and len(set(claves)) == 20
    assert set(corpus.PLAN) == set(harness.ANALISIS)
    assert [k for k, _ in corpus.textos_para("classify")] == [
        "clas-tv", "clas-corto", "clas-finanzas"]
    for a in harness.ANALISIS:
        assert corpus.textos_para(a), a


def test_comparar_errores():
    e = InvalidArgument("too few tokens")
    assert harness._comparar_errores(e, InvalidArgument("x"))["score"] == 100.0
    assert harness._comparar_errores(e, ValueError("x"))["score"] == 40.0
    assert harness._comparar_errores(e, None)["score"] == 0.0
    assert harness._comparar_errores(None, e)["score"] == 0.0
