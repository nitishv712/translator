"""
Romanized Hindi ("Hinglish") -> Devanagari transliteration.

Argos Translate's Hindi model (like Google ML Kit's before it) was trained
on native Devanagari text — fed "toh kaise hai aap" directly, it doesn't
reliably recognize that as Hindi at all, since that's out-of-distribution
input for it. This service is the missing step: convert romanized text to
proper Devanagari first, so the translation model downstream gets input it
actually understands.

Uses AI4Bharat's IndicXlit (github.com/AI4Bharat/IndicXlit), a transformer
model trained specifically on real-world romanized/colloquial Indic text
(the Aksharantar corpus) rather than a formal transliteration scheme —
the closest fit for how people actually type Hinglish.
"""

from flask import Flask, jsonify, request
from ai4bharat.transliteration import XlitEngine

app = Flask(__name__)

# Loaded once at process start (not per-request) — this also means the
# container isn't "ready" until this finishes, same as LibreTranslate
# loading its own models on boot.
engine = XlitEngine("hi", beam_width=4, rescore=True)


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
        result = engine.translit_sentence(text)
        transliterated = result.get("hi", text)
        return jsonify({"transliterated": transliterated})
    except Exception as e:  # noqa: BLE001 - surface any model error to the caller
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8100)
