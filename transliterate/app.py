"""
Romanized Hindi ("Hinglish") -> Devanagari transliteration.

NLLB's Hindi (like Argos Translate's and Google ML Kit's before it) was
trained on native Devanagari text — fed "toh kaise hai aap" directly, it
doesn't reliably recognize that as Hindi at all, since that's
out-of-distribution input for it. This service is the missing step: convert romanized text to
proper Devanagari first, so the translation model downstream gets input it
actually understands.

Uses `indic-transliteration`'s OPTITRANS scheme converter — a rule-based
Latin -> Devanagari phonetic mapping, not a learned model. This used to be
AI4Bharat's IndicXlit (a transformer, via `ai4bharat-transliteration` +
fairseq + torch), which produced better Devanagari for inconsistent/unusual
spellings, but loading that model pegged this container at high CPU and
memory even with zero incoming requests (PyTorch's own runtime footprint,
independent of traffic). A rule-based converter has no model to load — no
torch, no checkpoint, near-zero footprint at idle — at the cost of being
less forgiving of unusual spelling than a model trained on how people
actually type.
"""

from flask import Flask, jsonify, request
from indic_transliteration import sanscript

app = Flask(__name__)

# OPTITRANS over plain ITRANS: ITRANS distinguishes short/long vowels by
# letter case ("a" vs "A"), which casual all-lowercase Hinglish typing never
# does — every long vowel would misparse as short. OPTITRANS relaxes that
# (e.g. doubled letters for long vowels instead of relying on case), which
# matches how people actually type on a phone keyboard.
SOURCE_SCHEME = sanscript.OPTITRANS
TARGET_SCHEME = sanscript.DEVANAGARI


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/transliterate", methods=["POST"])
def transliterate():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        transliterated = sanscript.transliterate(text, SOURCE_SCHEME, TARGET_SCHEME)
        return jsonify({"transliterated": transliterated})
    except Exception as e:  # noqa: BLE001 - surface any conversion error to the caller
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8100)
