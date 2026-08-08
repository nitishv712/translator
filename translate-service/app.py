"""
Translation orchestrator — the only service that knows how the translation
pipeline actually works. `talkmos-backend` just authenticates the caller and
forwards the request here; this owns the real logic:

  1. Resolve/alias language codes.
  2. If the source isn't already known, ask the langid service to identify
     it. It answers with one of the app's three languages plus whether the
     text is romanized Indic ("Hinglish", e.g. "toh kaise hai aap").
  3. Romanized text goes through the transliterate service first, so the
     translation model gets the native Devanagari script it was actually
     trained on rather than Latin text it reads as out-of-distribution.
  4. Call the NLLB translation service for the real translation.

Both of those first two steps used to be LibreTranslate's job, and neither
is any more. Translation (step 4) moved to a dedicated NLLB service because
Argos Translate's fluency was noticeably worse for this app's pairs (e.g.
"How is you" instead of "How are you"). Detection (step 2) then moved to
the langid service, because LibreTranslate's detector reported Hinglish as
confident English often enough to break step 3 — see that service's
docstring for what that did to the response.

Kept as its own service — not a module inside the main backend — so the
translation pipeline can be understood, deployed, and scaled on its own,
without main-backend concerns (Mongo, auth, sockets, ...) anywhere near it.
"""

import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

LANGID_URL = os.environ.get("LANGID_URL", "http://langid:8400")
NLLB_URL = os.environ.get("NLLB_URL", "http://nllb-translate:8300")
TRANSLITERATE_URL = os.environ.get("TRANSLITERATE_URL", "http://transliterate:8100")

# "hinglish" isn't a real language the translation model has directly — an
# explicit "hinglish" source is aliased to Hindi. In practice callers send
# "auto" and let langid decide; this only matters for a caller that names it
# explicitly.
LANGUAGE_ALIASES = {
    "hinglish": "hi",
}

REQUEST_TIMEOUT = 30


def resolve_language_code(code):
    return LANGUAGE_ALIASES.get(code, code)


def detect_language(text):
    resp = requests.post(
        f"{LANGID_URL}/detect", json={"text": text}, timeout=REQUEST_TIMEOUT
    )
    data = resp.json()
    if not resp.ok or not data.get("language"):
        raise RuntimeError(data.get("error") or "Language detection failed")
    return data


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
            if source in LANGUAGE_ALIASES:
                # Explicit "hinglish" hits the same out-of-distribution
                # problem the detector catches below — the translation model
                # needs Devanagari, not romanized text.
                working_text = transliterate_to_hindi(text)
        else:
            detected = detect_language(text)
            source_code = detected["language"]
            if detected["romanized"]:
                working_text = transliterate_to_hindi(text)

        if source_code == target_code:
            # Nothing left to translate — either already in the target
            # language, or (Hinglish -> Hindi) the transliteration above
            # already *is* the answer.
            return jsonify({"translatedText": working_text})

        resp = requests.post(
            f"{NLLB_URL}/translate",
            json={
                "q": working_text,
                "source": source_code,
                "target": target_code,
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
        resp = requests.get(f"{NLLB_URL}/languages", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        if not resp.ok:
            return jsonify({"error": "Translation service error"}), 502
        return jsonify(data)
    except requests.RequestException:
        return jsonify({"error": "Translation backend is unavailable"}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8200)
