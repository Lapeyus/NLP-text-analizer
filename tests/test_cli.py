"""Pruebas del CLI analyze.py y de los atajos analyze-google / analyze-local."""

import io
import os
import runpy
import sys

import pytest
from google.cloud import language_v1 as G

import analyze
import nlp_local

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Fake:
    model = "fake-model"

    def complete_json(self, system_prompt, user_prompt, num_predict=None):
        if "sentimiento compatible" in system_prompt:
            return {"sentences": [{"text": "Great price.", "score": 0.8},
                                  {"text": "Awful build quality.", "score": -0.8}]}
        if "extracción de entidades" in system_prompt:
            return {"entities": [{"name": "Apple", "type": "ORGANIZATION", "weight": 1}]}
        if "moderación" in system_prompt:
            return {"categories": [{"name": "Toxic", "confidence": 0.9}]}
        raise AssertionError(system_prompt[:60])


@pytest.fixture
def local(monkeypatch):
    creados = []

    def crear(backend, model=None, entity_linking=False):
        creados.append(backend)
        return nlp_local.LanguageServiceClient(backend=_Fake())

    monkeypatch.setattr(analyze, "crear_cliente", crear)
    return creados


def test_lista_de_analisis():
    assert analyze.ANALISIS == ["sentiment", "sentences", "entities",
                                "entity-sentiment", "classify", "moderate",
                                "syntax", "link", "annotate"]
    assert analyze.BACKENDS == ["google", "local"]


def test_doc():
    d = analyze._doc("hola", "es", html=True)
    assert d == {"content": "hola", "type_": G.Document.Type.HTML, "language": "es"}
    assert "language" not in analyze._doc("x")


def test_f_sentiment_marca_mixto():
    r = G.AnalyzeSentimentResponse(document_sentiment=G.Sentiment(score=0.0, magnitude=1.8))
    lineas = analyze._f_sentiment(r)
    assert lineas[0] == "score=+0.00   magnitude=1.80"
    assert "MIXTO" in lineas[1]


def test_f_moderate_umbral():
    r = G.ModerateTextResponse(moderation_categories=[
        G.ClassificationCategory(name="Toxic", confidence=0.2)])
    assert analyze._f_moderate(r)[0].startswith("(nada por encima de 0.50")


def test_f_link_separa_enlazadas():
    r = G.AnalyzeEntitiesResponse()
    e = G.Entity(name="Apple", type_=G.Entity.Type.ORGANIZATION)
    e.metadata["mid"] = "/m/0k8z"
    r.entities.append(e)
    r.entities.append(G.Entity(name="apple", type_=G.Entity.Type.OTHER))
    out = analyze._f_link(r)
    assert out[0] == "enlazadas al Knowledge Graph:"
    assert "/m/0k8z" in out[1]
    assert out[2] == "sin enlace:" and "apple" in out[3]


def test_seguro_convierte_excepcion():
    def boom():
        raise ValueError("fallo\nsegunda línea")
    assert analyze._seguro(boom) == ["ERROR ValueError: fallo"]


def test_main_backend_local(local, capsys):
    assert analyze.main(["--backend", "local", "sentiment", "Great price. Awful build quality."]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "== sentiment · local (fake-model) =="
    assert out[1].startswith("score=+0.00")
    assert local == ["local"]


def test_main_lee_archivo(local, capsys, tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("Apple opened a store.", encoding="utf-8")
    analyze.main(["--backend", "local", "entities", "--file", str(f)])
    assert "Apple" in capsys.readouterr().out


def test_main_lee_stdin(local, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("Apple opened a store."))
    analyze.main(["--backend", "local", "entities"])
    assert "ORGANIZATION" in capsys.readouterr().out


def test_main_texto_vacio(local, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
    with pytest.raises(SystemExit) as e:
        analyze.main(["--backend", "local", "sentiment"])
    assert e.value.code == 2


def test_main_analisis_invalido():
    with pytest.raises(SystemExit):
        analyze.main(["nada", "texto"])


def test_compare_lado_a_lado(local, capsys):
    analyze.main(["--compare", "moderate", "You are an idiot."])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("GOOGLE") and "LOCAL (fake-model)" in out[0]
    assert local == ["google", "local"]
    assert "Toxic" in out[2]


@pytest.mark.parametrize("nombre,backend", [("analyze-google", "google"),
                                            ("analyze-local", "local")])
def test_atajos_fuerzan_backend(monkeypatch, nombre, backend):
    recibido = []
    monkeypatch.setattr(analyze, "main", lambda argv: recibido.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", [nombre, "sentiment", "hola"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(os.path.join(RAIZ, nombre), run_name="__main__")
    assert e.value.code == 0
    assert recibido == [["sentiment", "hola", "--backend", backend]]
