"""
Translation orchestrator — the only service that knows how the translation
pipeline actually works. `talkmos-backend` just authenticates the caller and
forwards the request here; this owns the real logic:

  1. Resolve/alias language codes.
  2. If the source isn't already known, ask LibreTranslate to detect it.
  3. A low-confidence detection is most often romanized Hindi ("Hinglish")
     misread as English (LibreTranslate has no "Hinglish" label of its own,
     and Hinglish is Latin-script like English) — run it through the
     transliterate service first so Argos Translate's Hindi model gets the
     native Devanagari script it was actually trained on.
  4. Call LibreTranslate (Argos Translate) for the real translation.

Kept as its own service — not a module inside the main backend — so the
translation pipeline can be understood, deployed, and scaled on its own,
without main-backend concerns (Mongo, auth, sockets, ...) anywhere near it.
"""

import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

LIBRETRANSLATE_URL = os.environ.get("LIBRETRANSLATE_URL", "http://libretranslate:5000")
TRANSLITERATE_URL = os.environ.get("TRANSLITERATE_URL", "http://transliterate:8100")

# "hinglish" isn't a real language LibreTranslate/Argos Translate has a model
# for, so an explicit "hinglish" source is aliased to Hindi. In practice
# callers send "auto" and let detection + the confidence fallback below
# decide; this only matters for a caller that names it explicitly.
LANGUAGE_ALIASES = {
    "hinglish": "hi",
}

# Below this, a detection is treated as too unreliable to trust — most often
# because the text is romanized Hindi, which reads as low-confidence English
# (or something else entirely) to LibreTranslate's detector. There's no
# principled "correct" threshold here, just a practical cutoff; 100 is the
# maximum LibreTranslate reports.
HINGLISH_FALLBACK_CONFIDENCE = 50

REQUEST_TIMEOUT = 30


def resolve_language_code(code):
    return LANGUAGE_ALIASES.get(code, code)


def detect_language(text):
    resp = requests.post(
        f"{LIBRETRANSLATE_URL}/detect", json={"q": text}, timeout=REQUEST_TIMEOUT
    )
    data = resp.json()
    if not resp.ok or not isinstance(data, list) or len(data) == 0:
        message = data.get("error") if isinstance(data, dict) else None
        raise RuntimeError(message or "Language detection failed")
    return data[0]  # sorted by confidence, highest first


def transliterate_to_hindi(text):
    resp = requests.post(
        f"{TRANSLITERATE_URL}/transliterate",
        json={"text": text},
        timeout=REQUEST_TIMEOUT,
    )
    data = resp.json()
    if not resp.ok or not data.get("transliterated"):
        raise RuntimeError(data.get("error", "Transliteration failed"))
    return data["transliterated"]


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/translate", methods=["POST"])
def translate():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    target = body.get("target")
    source = body.get("source")

    if not text or not target:
        return jsonify({"error": "text and target are required"}), 400

    target_code = resolve_language_code(target)
    working_text = text

    try:
        if source and source != "auto":
            source_code = resolve_language_code(source)
        else:
            detected = detect_language(text)
            if detected["confidence"] < HINGLISH_FALLBACK_CONFIDENCE:
                working_text = transliterate_to_hindi(text)
                source_code = "hi"
            else:
                source_code = detected["language"]

        if source_code == target_code:
            # Nothing left to translate — either already in the target
            # language, or (Hinglish -> Hindi) the transliteration above
            # already *is* the answer.
            return jsonify({"translatedText": working_text})

        resp = requests.post(
            f"{LIBRETRANSLATE_URL}/translate",
            json={
                "q": working_text,
                "source": source_code,
                "target": target_code,
                "format": "text",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        if not resp.ok:
            return jsonify({"error": data.get("error", "Translation service error")}), 502

        return jsonify({"translatedText": data["translatedText"]})

    except requests.RequestException:
        return jsonify({"error": "Translation backend is unavailable"}), 503
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502


@app.route("/languages", methods=["GET"])
def languages():
    try:
        resp = requests.get(f"{LIBRETRANSLATE_URL}/languages", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        if not resp.ok:
            return jsonify({"error": "Translation service error"}), 502
        return jsonify(data)
    except requests.RequestException:
        return jsonify({"error": "Translation backend is unavailable"}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8200)
