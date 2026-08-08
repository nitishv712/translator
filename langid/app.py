"""
Language identification for the three languages the app translates between
(English, Hindi, Bengali), including romanized Hindi ("Hinglish").

Replaces LibreTranslate's /detect, which was the last thing LibreTranslate
was still in this stack for. Its detector reported plenty of Hinglish as
*confident* English — "kya kar rahe ho" scored as English well above the
confidence cutoff translate-service used to trigger transliteration. The
transliteration step was skipped, the source came back as English, and a
request asking for English then had nothing to translate and echoed the
user's own message back at them.

GlotLID (v3) is a fastText classifier covering ~2100 language/script pairs
and, unlike general-purpose detectors, has explicit labels for romanized
Indic text (`hin_Latn`, `ben_Latn`, ...) rather than only native script.
fastText also keeps this cheap: one flat model, CPU-only, no torch runtime
idling in the background — the same constraint that pushed the transliterate
service off IndicXlit.

Two things make the raw model usable here:

  1. Native script is decided by Unicode range, not by the model. Devanagari
     is Hindi and Bengali script is Bengali as far as this app is concerned;
     no classifier can beat a script check at that, and it can only add
     mistakes. The model is asked only about Latin-script text, which is the
     genuinely ambiguous case (real English vs romanized Hindi).

  2. The decision reads *grouped* probability mass, not the top label. On
     short chat messages GlotLID scatters its confidence across the many
     mutually-intelligible romanized Indic languages — "bhai paise bhej do"
     comes back as Irish, "zara jaldi karo" as Javanese, each with a dozen
     Indic labels splitting the rest. Any single-label reading throws that
     away. Summing English mass against romanized-Indic mass is the question
     actually being asked, since the answer only has to be good enough to
     pick one of three languages.

Tuning is deliberately lopsided. Calling English "Hinglish" is the expensive
mistake — it sends real English through the transliterator and returns
Devanagari nonsense — while the reverse just leaves a message untranslated.
MIN_ROMANIZED_MASS is set at the point where English stops being misread at
all (84/84 on a held-out set of English chat messages, including the short
ones like "ok" and "brb" that detectors usually trip on), which still catches
39 of 44 Hinglish messages. Lowering it to 0.001 catches all 44 but starts
transliterating sentences like "i am here"; that trade isn't worth it.
"""

import os
import re

import fasttext
from flask import Flask, jsonify, request

app = Flask(__name__)

MODEL_PATH = os.environ.get("MODEL_PATH", "/app/model/glotlid.bin")

_model = fasttext.load_model(MODEL_PATH)

# ISO 639-3 codes GlotLID uses for languages of the Indian subcontinent. A
# romanized hit on any of them means the same thing to this pipeline: text
# that has to go through transliteration before a translation model can read
# it. They're grouped rather than distinguished because the app only offers
# three languages — telling Punjabi from Maithili would be precision nothing
# downstream can use.
INDIC_LANGUAGES = frozenset(
    """
    hin urd pan ben asm guj mar ory ori tam tel kan mal nep npi snd sin kas mai
    mni bho awa mag hne raj doi kok san brx sat gom hns bpy syl kmm pnb skr lah
    dhd mup bgc bns gbm kfy xnr tcy kru hoc unr gon sck kha lus grt trp njz anp
    bfy bra noe wbr hoj ctg rkt rhg mjz dty bhb gju hif gbc kfr vah
    """.split()
)

# Scripts of the subcontinent. GlotLID sometimes labels romanized text with
# an Indic *script* it was never written in (`und_Modi` for "ghar pe hu"),
# which is still the pipeline's answer: Indic, and not in Latin-readable form.
INDIC_SCRIPTS = frozenset(
    """
    Deva Beng Guru Gujr Orya Taml Telu Knda Mlym Sinh Modi Olck Mtei Tirh Shrd
    Takr Sylo Newa Khoj Sind Diak
    """.split()
)

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
BENGALI = re.compile(r"[ঀ-৿]")

# How much of the probability mass has to land on romanized Indic labels
# before Latin-script text is treated as Hinglish rather than English. See
# the module docstring for why this is set where it is.
MIN_ROMANIZED_MASS = 0.03

# Enough labels to collect the scattered Indic mass without walking all ~2100.
TOP_K = 50


def _grouped_mass(text):
    labels, probabilities = _model.predict(text.replace("\n", " "), k=TOP_K)
    english = indic = 0.0
    for label, probability in zip(labels, probabilities):
        code = label.replace("__label__", "")
        language, _, script = code.partition("_")
        if language == "eng":
            english += float(probability)
        elif language in INDIC_LANGUAGES or script in INDIC_SCRIPTS:
            indic += float(probability)
    return english, indic


def identify(text):
    """Returns the app language for `text`, and whether it needs transliterating."""
    if DEVANAGARI.search(text):
        return "hi", False
    if BENGALI.search(text):
        return "bn", False

    english_mass, indic_mass = _grouped_mass(text)
    if indic_mass > english_mass and indic_mass >= MIN_ROMANIZED_MASS:
        # Romanized Indic is handled as Hindi: it's what the app's users
        # actually write, and transliteration only targets Devanagari.
        return "hi", True
    return "en", False


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/detect", methods=["POST"])
def detect():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    language, romanized = identify(text)
    return jsonify({"language": language, "romanized": romanized})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8400)
