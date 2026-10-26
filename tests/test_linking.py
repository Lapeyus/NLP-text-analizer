"""Pruebas de EntityLinker con Wikidata simulado (sin red)."""

import pytest

from nlp_local import linking


@pytest.fixture
def wikidata(monkeypatch):
    estado = {"candidatos": [], "resolver": (None, None), "falla": None}

    def buscar(nombre, limite=5, idioma="en"):
        if estado["falla"] == "buscar":
            raise linking._FalloRed("429")
        return estado["candidatos"]

    def resolver(qid):
        if estado["falla"] == "resolver":
            raise linking._FalloRed("timeout")
        return estado["resolver"]

    monkeypatch.setattr(linking, "buscar_candidatos", buscar)
    monkeypatch.setattr(linking, "resolver", resolver)
    return estado


def test_sustantivo_comun_no_se_busca(wikidata):
    loc = linking.EntityLinker()
    assert loc.enlazar("store", "ctx", es_propio=False) == (None, None)
    assert (loc.enlazadas, loc.rechazadas, loc.fallos_red) == (0, 0, 0)


def test_candidato_unico_identico_sin_modelo(wikidata):
    wikidata["candidatos"] = [("Q312", "Apple", "empresa")]
    wikidata["resolver"] = ("/m/0k8z", "https://en.wikipedia.org/wiki/Apple_Inc.")
    loc = linking.EntityLinker(backend=None)
    assert loc.enlazar("Apple", "Apple released the iPhone") == wikidata["resolver"]
    assert loc.enlazadas == 1


def test_sin_modelo_etiqueta_distinta_rechaza(wikidata):
    wikidata["candidatos"] = [("Q1", "Apple Records", ""), ("Q2", "Apple", "")]
    loc = linking.EntityLinker(backend=None)
    assert loc.enlazar("Apple", "ctx") == (None, None)
    assert loc.rechazadas == 1


def test_modelo_elige_indice(wikidata, fake_backend):
    wikidata["candidatos"] = [("Q1", "Jordan", "país"), ("Q2", "Michael Jordan", "")]
    wikidata["resolver"] = ("/m/054c1", None)
    fb = fake_backend([("desambiguador", {"index": 1})])
    loc = linking.EntityLinker(backend=fb)
    assert loc.enlazar("Jordan", "Jordan won six NBA titles") == ("/m/054c1", None)
    assert fb.llamadas[0][2] == 64
    assert "1. Michael Jordan" in fb.llamadas[0][1]


def test_modelo_dice_ninguno(wikidata, fake_backend):
    wikidata["candidatos"] = [("Q1", "Apple Inc.", ""), ("Q2", "Apple Records", "")]
    loc = linking.EntityLinker(backend=fake_backend([("desambiguador", {"index": -1})]))
    assert loc.enlazar("apple", "I ate an apple") == (None, None)
    assert loc.rechazadas == 1


@pytest.mark.parametrize("donde", ["buscar", "resolver"])
def test_fallo_de_red_se_cuenta_aparte(wikidata, donde):
    wikidata["candidatos"] = [("Q312", "Apple", "")]
    wikidata["falla"] = donde
    loc = linking.EntityLinker()
    assert loc.enlazar("Apple", "ctx") == (None, None)
    assert (loc.fallos_red, loc.rechazadas) == (1, 0)


def test_resolver_formato_url(monkeypatch, tmp_path):
    monkeypatch.setattr(linking, "_cache", linking._Cache(str(tmp_path / "w.json")))
    respuesta = {"entities": {"Q1": {
        "claims": {"P646": [{"mainsnak": {"datavalue": {"value": "/m/0r679"}}},
                            {"mainsnak": {"datavalue": {"value": "/m/otro"}}}]},
        "sitelinks": {"enwiki": {"title": "Cupertino, California"}},
    }}}
    monkeypatch.setattr(linking, "_http", lambda url: respuesta)
    mid, url = linking.resolver("Q1")
    assert mid == "/m/0r679"  # el primero, como Google
    assert url == "https://en.wikipedia.org/wiki/Cupertino,_California"
    # segunda vez sale de la caché
    monkeypatch.setattr(linking, "_http", lambda url: (_ for _ in ()).throw(AssertionError))
    assert linking.resolver("Q1") == (mid, url)
