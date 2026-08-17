"""
Métricas de paridad.

Cada función compara una respuesta de Google (el maestro) con la del modelo
local y devuelve un dict con métricas crudas más un `score` de 0 a 100.

Criterio para elegir métricas: tienen que medir lo que un CONSUMIDOR notaría al
cambiar de implementación. Por eso, por ejemplo, en sentimiento pesa más el
acuerdo de signo que el error absoluto: una app que colorea de verde o rojo se
rompe si el signo se invierte, y no se entera de una diferencia de 0.1.
"""


def _mae(pares):
    if not pares:
        return 0.0
    return sum(abs(a - b) for a, b in pares) / len(pares)


def _norm_nombre(s):
    return " ".join(str(s).lower().split())


def _pct(x):
    return round(100.0 * x, 1)


# --------------------------------------------------------------------------
def sentiment(g, l):
    gs, ls = g.document_sentiment.score, l.document_sentiment.score
    gm, lm = g.document_sentiment.magnitude, l.document_sentiment.magnitude

    err_score = abs(gs - ls)
    err_mag = abs(gm - lm)

    # Signo: tratamos |score| < 0.15 como "neutro".
    def signo(x):
        return 0 if abs(x) < 0.15 else (1 if x > 0 else -1)

    # ...pero un umbral duro crea un acantilado absurdo: -0.10 y -0.20 caen en
    # cubetas distintas y puntuaban como desacuerdo TOTAL, cuando para cualquier
    # consumidor son el mismo veredicto ("levemente negativo"). Medido: un
    # cambio de prompt que movía un caso de -0.10 a -0.20 hundía su puntuación
    # de 98 a 39 sin que nada empeorara de verdad.
    # Por eso valores suficientemente cercanos cuentan como acuerdo aunque
    # caigan en cubetas distintas.
    signo_ok = signo(gs) == signo(ls) or abs(gs - ls) <= 0.2

    # El score combina: signo correcto (60%) + cercanía de score (25%) +
    # cercanía de magnitude (15%). La magnitude pesa poco porque casi ningún
    # consumidor la usa como número absoluto.
    s = (0.60 * (1.0 if signo_ok else 0.0)
         + 0.25 * max(0.0, 1.0 - err_score / 2.0)
         + 0.15 * max(0.0, 1.0 - err_mag / max(1.0, gm if gm else 1.0)))

    return {
        "google": "score={:+.2f} mag={:.2f}".format(gs, gm),
        "local": "score={:+.2f} mag={:.2f}".format(ls, lm),
        "err_score": round(err_score, 3),
        "err_magnitude": round(err_mag, 3),
        "signo_ok": signo_ok,
        "n_oraciones_g": len(g.sentences),
        "n_oraciones_l": len(l.sentences),
        "score": _pct(s),
    }


# --------------------------------------------------------------------------
def _emparejar_entidades(ge, le):
    """
    Empareja entidades por nombre normalizado.

    Se usa coincidencia exacta y, si falla, contención en cualquier dirección,
    porque el desacuerdo más común no es inventarse una entidad sino recortarla
    distinto: Google dice "Michael Jordan" donde el modelo dice "Jordan".
    """
    gmap = {_norm_nombre(e.name): e for e in ge}
    lmap = {_norm_nombre(e.name): e for e in le}

    parejas = []
    usados_l = set()
    for gk, gv in gmap.items():
        if gk in lmap:
            parejas.append((gv, lmap[gk]))
            usados_l.add(gk)
            continue
        cand = None
        for lk, lv in lmap.items():
            if lk in usados_l:
                continue
            if gk in lk or lk in gk:
                cand = (lk, lv)
                break
        if cand:
            parejas.append((gv, cand[1]))
            usados_l.add(cand[0])

    solo_g = [v for k, v in gmap.items()
              if not any(v is p[0] for p in parejas)]
    solo_l = [v for k, v in lmap.items() if k not in usados_l]
    return parejas, solo_g, solo_l


def _f1(n_match, n_g, n_l):
    if n_g == 0 and n_l == 0:
        return 1.0
    prec = n_match / n_l if n_l else 0.0
    rec = n_match / n_g if n_g else 0.0
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


def entities(g, l, con_sentimiento=False):
    parejas, solo_g, solo_l = _emparejar_entidades(g.entities, l.entities)
    f1 = _f1(len(parejas), len(g.entities), len(l.entities))

    tipo_ok = sum(1 for a, b in parejas if a.type_ == b.type_)
    tipo_acc = tipo_ok / len(parejas) if parejas else 0.0
    err_sal = _mae([(a.salience, b.salience) for a, b in parejas])

    # ¿Coincide cuál es la entidad principal? Es lo que más se nota al cambiar
    # de implementación: casi todo el mundo usa entities[0] o filtra por
    # salience.
    top_ok = bool(g.entities) and bool(l.entities) and (
        _norm_nombre(g.entities[0].name) in _norm_nombre(l.entities[0].name)
        or _norm_nombre(l.entities[0].name) in _norm_nombre(g.entities[0].name)
    )

    out = {
        "f1_nombres": _pct(f1),
        "acc_tipo": _pct(tipo_acc),
        "err_salience": round(err_sal, 3),
        "top1_ok": top_ok,
        "solo_google": [e.name for e in solo_g][:6],
        "solo_local": [e.name for e in solo_l][:6],
        "n_g": len(g.entities),
        "n_l": len(l.entities),
    }

    if con_sentimiento:
        err = _mae([(a.sentiment.score, b.sentiment.score) for a, b in parejas])
        signo_ok = sum(
            1 for a, b in parejas
            if (abs(a.sentiment.score) < 0.15 and abs(b.sentiment.score) < 0.15)
            or (a.sentiment.score > 0) == (b.sentiment.score > 0)
        )
        acc_signo = signo_ok / len(parejas) if parejas else 0.0
        out["err_sentimiento"] = round(err, 3)
        out["acc_signo_ent"] = _pct(acc_signo)
        s = (0.35 * f1 + 0.15 * tipo_acc + 0.30 * acc_signo
             + 0.10 * (1.0 if top_ok else 0.0)
             + 0.10 * max(0.0, 1.0 - err_sal / 0.5))
    else:
        s = (0.45 * f1 + 0.20 * tipo_acc + 0.15 * (1.0 if top_ok else 0.0)
             + 0.20 * max(0.0, 1.0 - err_sal / 0.5))

    out["score"] = _pct(s)
    return out


# --------------------------------------------------------------------------
def classify(g, l):
    gset = {c.name for c in g.categories}
    lset = {c.name for c in l.categories}

    # Nivel 1 es lo que casi todo el mundo consume; la ruta completa es un
    # extra. Los puntuamos por separado.
    #
    # El filtro `if "/" in c.name[1:]` que había aquí excluía las rutas de un
    # solo nivel ("/Finance"), así que cuando el modelo devolvía justo eso, el
    # nivel 1 puntuaba 0.0 pese a coincidir con Google. Medía mal el fallo real,
    # que es que la ruta viene corta, no que el tema esté equivocado.
    def nivel1(cats):
        out = set()
        for c in cats:
            partes = [p for p in c.name.split("/") if p]
            if partes:
                out.add(partes[0])
        return out

    n1_g = nivel1(g.categories)
    n1_l = nivel1(l.categories)

    # Profundidad: mide aparte si el modelo se queda corto en la ruta.
    prof_g = _media_profundidad(g.categories)
    prof_l = _media_profundidad(l.categories)

    # Caso especial: ambos vacíos es paridad perfecta.
    if not g.categories and not l.categories:
        return {"nota": "ambas listas vacías", "score": 100.0}

    exacto = _f1(len(gset & lset), len(gset), len(lset))
    f1_n1 = _f1(len(n1_g & n1_l), len(n1_g), len(n1_l))

    def raiz(cats):
        partes = [p for p in cats[0].name.split("/") if p]
        return partes[0] if partes else ""

    top_ok = bool(g.categories) and bool(l.categories) and raiz(g.categories) == raiz(
        l.categories
    )

    s = 0.5 * f1_n1 + 0.3 * (1.0 if top_ok else 0.0) + 0.2 * exacto
    return {
        "google": [c.name for c in g.categories][:3],
        "local": [c.name for c in l.categories][:3],
        "f1_nivel1": _pct(f1_n1),
        "f1_ruta_exacta": _pct(exacto),
        "top1_ok": top_ok,
        "prof_g": prof_g,
        "prof_l": prof_l,
        "score": _pct(s),
    }


def _media_profundidad(cats):
    if not cats:
        return 0.0
    return round(
        sum(len([p for p in c.name.split("/") if p]) for c in cats) / len(cats), 2
    )


# --------------------------------------------------------------------------
def moderate(g, l):
    gm = {c.name: c.confidence for c in g.moderation_categories}
    lm = {c.name: c.confidence for c in l.moderation_categories}
    comunes = sorted(set(gm) & set(lm))

    err = _mae([(gm[k], lm[k]) for k in comunes])

    # Lo que de verdad importa: ¿coincide qué categorías se disparan?
    UMBRAL = 0.5
    g_on = {k for k in comunes if gm[k] >= UMBRAL}
    l_on = {k for k in comunes if lm[k] >= UMBRAL}
    f1_disparo = _f1(len(g_on & l_on), len(g_on), len(l_on))
    orden_ok = [c.name for c in g.moderation_categories] == [
        c.name for c in l.moderation_categories
    ]

    s = 0.55 * f1_disparo + 0.30 * max(0.0, 1.0 - err / 0.4) + 0.15 * (
        1.0 if orden_ok else 0.0
    )
    return {
        "google_activas": sorted(g_on),
        "local_activas": sorted(l_on),
        "f1_disparo": _pct(f1_disparo),
        "err_medio": round(err, 3),
        "orden_identico": orden_ok,
        "n_categorias_l": len(lm),
        "score": _pct(s),
    }


# --------------------------------------------------------------------------
def syntax(g, l):
    gt, lt = list(g.tokens), list(l.tokens)

    # Alineamos por texto de token en secuencia. Si la tokenización difiere, el
    # resto de métricas no significa nada, así que se reporta primero.
    gtx = [t.text.content for t in gt]
    ltx = [t.text.content for t in lt]
    tok_ok = gtx == ltx

    n = min(len(gt), len(lt))
    if tok_ok and n:
        pos = sum(1 for i in range(n)
                  if gt[i].part_of_speech.tag == lt[i].part_of_speech.tag) / n
        lem = sum(1 for i in range(n)
                  if gt[i].lemma.lower() == lt[i].lemma.lower()) / n
        head = sum(1 for i in range(n)
                   if gt[i].dependency_edge.head_token_index
                   == lt[i].dependency_edge.head_token_index) / n
        lab = sum(1 for i in range(n)
                  if gt[i].dependency_edge.label
                  == lt[i].dependency_edge.label) / n
    else:
        pos = lem = head = lab = 0.0

    s = (0.20 * (1.0 if tok_ok else 0.0) + 0.30 * pos + 0.20 * lem
         + 0.15 * head + 0.15 * lab)
    return {
        "tokenizacion_identica": tok_ok,
        "n_tokens_g": len(gt),
        "n_tokens_l": len(lt),
        "acc_pos": _pct(pos),
        "acc_lemma": _pct(lem),
        "acc_head": _pct(head),
        "acc_label": _pct(lab),
        "score": _pct(s),
    }
