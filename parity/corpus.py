"""
Corpus de evaluación.

Los textos están elegidos para cubrir los casos donde medimos que la API oficial
se comporta de forma no obvia, no para que el modelo local quede bien. Cada
entrada dice qué pretende estresar.
"""

CORPUS = [
    # --- sentimiento: los casos límite que documentamos ---
    ("sent-mixto", "The food was amazing but the service was terrible."),
    ("sent-positivo", "I loved it. Best purchase this year."),
    ("sent-mixto-2frases", "Great price. Awful build quality."),
    ("sent-neutro", "The device weighs 300 grams."),
    ("sent-arco", "The setup was painful. Support took days to answer. "
                  "Once running, it works beautifully."),
    ("sent-intenso", "Terrible. Awful. Horrible. Worst ever. Never again."),

    # --- entidades: salience contraintuitiva y enlace al Knowledge Graph ---
    ("ent-tienda", "Apple opened a new store in Mexico City last Tuesday."),
    ("ent-kg", "Michael Jordan won six NBA championships with the Chicago "
               "Bulls during the 1990s."),
    ("ent-ambiguo", "Apple released the iPhone in Cupertino. "
                    "I ate an apple for breakfast."),
    ("ent-fabrica", "Tesla opened a factory in Berlin."),

    # --- clasificación: necesita >= 20 palabras ---
    ("clas-tv", "That actor on TV makes movies in Hollywood and also stars in "
                "a variety of popular new TV shows."),
    ("clas-corto", "Short text."),
    ("clas-finanzas", "The central bank raised interest rates again this "
                      "quarter, and mortgage lenders responded by tightening "
                      "credit requirements for new home buyers across the "
                      "country."),

    # --- moderación ---
    ("mod-toxico", "You are an idiot and I hope you get hurt."),
    ("mod-limpio", "The weather is nice today and I plan to go for a walk."),
    ("mod-queja", "This product is a complete waste of money and the company "
                  "should be ashamed of selling it."),

    # --- sintaxis ---
    ("syn-simple", "The dog chased the cat."),
    ("syn-pasiva", "The report was written by the committee last year."),

    # --- español: cuatro funciones del original fijan language='en' ---
    ("es-resena", "La comida estuvo increíble pero el servicio fue pésimo."),
    ("es-neutro", "El dispositivo pesa 300 gramos."),
]

# Qué análisis corre sobre qué textos. Correr los 7 sobre los 20 textos gasta
# cuota de Google y minutos de inferencia local sin aportar señal: un texto de
# moderación no dice nada útil sobre la calidad del parser sintáctico.
PLAN = {
    "sentiment": ["sent-", "es-"],
    "entities": ["ent-", "es-"],
    "entity_sentiment": ["sent-mixto", "ent-", "es-resena"],
    "classify": ["clas-"],
    "moderate": ["mod-"],
    "syntax": ["syn-", "sent-neutro"],
}


def textos_para(analisis):
    prefijos = PLAN[analisis]
    return [(k, t) for k, t in CORPUS if any(k.startswith(p) for p in prefijos)]
